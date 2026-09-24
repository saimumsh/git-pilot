#!/usr/bin/env python3
"""
gitpilot - AI commit messages with safety checks.

Used by the Claude Code plugin (no API key needed, Claude writes the text):
  gitpilot context             Print staged-change info as JSON, no AI call
  gitpilot commit --stdin      Commit with a message given on stdin, after checks
  gitpilot undo                Undo the last commit, keep changes staged, save a backup
  gitpilot guard               PreToolUse hook: blocks secret commits, force pushes
                               and commands that throw away work

Standalone CLI (calls the Anthropic API itself):
  gitpilot commit              Generate a commit message for staged changes
  gitpilot commit --all        Stage all tracked changes first (like git commit -a)
  gitpilot commit --dry-run    Show the message and command without committing
  gitpilot hook install        Auto-fill messages whenever you run plain `git commit`
  gitpilot hook uninstall      Remove the hook

  Setup: pip install anthropic; export ANTHROPIC_API_KEY=...
  Optional: GITPILOT_MODEL (default: claude-haiku-4-5-20251001)

Design rule: plain code does everything that must be exact (reading the
diff, safety checks, formatting, running git). The AI only writes text,
and nothing runs without being shown to you first.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

MODEL = os.environ.get("GITPILOT_MODEL", "claude-haiku-4-5-20251001")
DIFF_BUDGET = 12_000         # max diff characters sent to the AI in total
PER_FILE_BUDGET = 3_000      # max characters per file
LARGE_FILE_BYTES = 5 * 1024 * 1024
PROTECTED_BRANCHES = {"main", "master", "production", "release"}
HOOK_MARKER = "# gitpilot-hook"

NOISE_PATTERNS = [
    "*.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "go.sum",
    "poetry.lock", "Cargo.lock", "*.min.js", "*.map", "dist/*", "build/*",
]

TYPES = "feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert"
SUBJECT_RE = re.compile(rf"^({TYPES})(\([\w\-./]+\))?!?: \S.*$")

SECRET_PATTERNS = [
    ("AWS access key", r"AKIA[0-9A-Z]{16}"),
    ("private key", r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ("GitHub token", r"gh[pousr]_[A-Za-z0-9]{36,}"),
    ("API key (sk-...)", r"sk-(ant-)?[A-Za-z0-9_\-]{20,}"),
    ("Stripe key", r"[sr]k_(live|test)_[A-Za-z0-9]{16,}"),
    ("Google API key", r"AIza[0-9A-Za-z_\-]{35}"),
    ("JWT", r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("Slack token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("hardcoded credential",
     r"(?i)(password|passwd|secret|api_?key|token)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
]

# ------------------------------------------------------------------ output
TTY = sys.stdout.isatty()


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def info(msg): print(msg)
def warn(msg): print(color("! " + msg, "33"))
def error(msg): print(color("x " + msg, "31"), file=sys.stderr)
def ok(msg): print(color("✓ " + msg, "32"))


def confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# --------------------------------------------------------------- git layer
class GitError(Exception):
    pass


def git(*args: str, check: bool = True, input_text: str | None = None) -> str:
    """Read-only or internal git call. Returns stdout."""
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", input=input_text)
    if check and r.returncode != 0:
        raise GitError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], capture_output=True).returncode == 0


def run(args: list[str], dry_run: bool, input_text: str | None = None) -> None:
    """Every command that CHANGES the repository goes through here, so the
    user always sees it, and --dry-run can skip it."""
    info(color("$ git " + " ".join(args), "36"))
    if dry_run:
        info(color("  (dry run: not executed)", "2"))
        return
    git(*args, input_text=input_text)


def git_path(name: str) -> Path:
    return Path(git("rev-parse", "--git-path", name).strip())


def staged_files() -> list[str]:
    out = git("diff", "--staged", "--name-only", "-z")
    return [f for f in out.split("\0") if f]


def is_noise(path: str) -> bool:
    name = Path(path).name
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p)
               for p in NOISE_PATTERNS)


# ------------------------------------------------------------ safety layer
def find_secrets(diff_args: tuple[str, ...] = ("--staged",)) -> list[str]:
    """Scan only ADDED lines of a diff (staged changes by default)."""
    found, current = [], "?"
    for line in git("diff", *diff_args, "-U0", "--no-color").splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            for label, pattern in SECRET_PATTERNS:
                if re.search(pattern, line):
                    found.append(f"{label} in {current}")
    return sorted(set(found))


def find_secrets_untracked(limit: int = 200) -> list[str]:
    """Scan new, not-yet-tracked files (they are invisible to git diff)."""
    found = []
    names = [f for f in git("ls-files", "--others", "--exclude-standard",
                            "-z").split("\0") if f][:limit]
    for f in names:
        p = Path(f)
        if not p.is_file() or p.stat().st_size > 1_000_000:
            continue
        text = p.read_text(errors="ignore")
        for label, pattern in SECRET_PATTERNS:
            if re.search(pattern, text):
                found.append(f"{label} in {f}")
    return found


def large_files(files: list[str]) -> list[str]:
    result = []
    for f in files:
        size = git("cat-file", "-s", f":{f}", check=False).strip()
        if size.isdigit() and int(size) > LARGE_FILE_BYTES:
            result.append(f"{f} ({int(size) / 1024 / 1024:.1f} MB)")
    return result


def current_branch() -> str:
    return git("branch", "--show-current", check=False).strip()


# ---------------------------------------------------------- diff for the AI
def build_diff_context(files: list[str]) -> tuple[str, str]:
    """Deterministically shrink the diff so cost stays low on big changes."""
    stat = git("diff", "--staged", "--stat", "--no-color")
    parts, used = [], 0
    for f in files:
        if is_noise(f):
            parts.append(f"--- {f}: (lock/generated file, contents skipped)")
            continue
        d = git("diff", "--staged", "--no-color", "--", f)
        if len(d) > PER_FILE_BUDGET:
            d = d[:PER_FILE_BUDGET] + "\n... (truncated)"
        if used + len(d) > DIFF_BUDGET:
            parts.append(f"--- {f}: (omitted, see stat summary)")
            continue
        parts.append(d)
        used += len(d)
    return stat, "\n".join(parts)


def scope_hint(files: list[str]) -> str | None:
    """If every real change is inside one top-level folder, suggest it as scope."""
    tops = {f.split("/")[0] for f in files if "/" in f and not is_noise(f)}
    flat = [f for f in files if "/" not in f and not is_noise(f)]
    return tops.pop() if len(tops) == 1 and not flat else None


def recent_subjects() -> list[str]:
    return [s for s in git("log", "-10", "--format=%s", check=False).splitlines() if s]


# ---------------------------------------------------------------- AI layer
SYSTEM = f"""You write git commit messages in the Conventional Commits format.
Output ONLY the commit message, nothing else.
Line 1: type(scope): summary
  - type is one of: {TYPES.replace('|', ', ')}
  - scope is optional, short, lowercase
  - summary in imperative mood ("add", not "added"), no trailing period
  - whole line at most 72 characters
If the change is not trivial, add a blank line and a short body explaining
WHAT changed and WHY (not how). Use "- " bullets for several changes.
Never invent details that are not visible in the diff."""


def ask_model(system: str, prompt: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise GitError("The anthropic package is missing: pip install anthropic")
    client = anthropic.Anthropic()
    resp = client.messages.create(model=MODEL, max_tokens=500, system=system,
                                  messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in resp.content if b.type == "text")


def clean(msg: str) -> str:
    msg = re.sub(r"^```\w*\n?|```$", "", msg.strip(), flags=re.M).strip()
    if len(msg) > 1 and msg[0] == msg[-1] == '"':  # only quotes wrapping it all
        msg = msg[1:-1].strip()
    return msg


def rewrap(msg: str) -> str:
    """Formatting is fixed by code, not by asking the AI again."""
    lines = msg.splitlines()
    if not lines:
        return msg
    subject, body = lines[0].rstrip().rstrip("."), lines[1:]
    while body and not body[0].strip():
        body.pop(0)
    wrapped = []
    for line in body:
        indent = "  " if line.lstrip().startswith(("- ", "* ")) else ""
        wrapped.extend(textwrap.wrap(line, 72, subsequent_indent=indent,
                                     break_on_hyphens=False,
                                     break_long_words=False) or [""])
    return subject + ("\n\n" + "\n".join(wrapped) if wrapped else "")


def validate(msg: str) -> str | None:
    subject = msg.splitlines()[0] if msg else ""
    if not subject:
        return "the message is empty"
    if len(subject) > 72:
        return f"the subject line is {len(subject)} characters (max 72)"
    if not SUBJECT_RE.match(subject):
        return "the subject must look like 'type(scope): summary'"
    return None


def generate_message(files: list[str], hint: str = "") -> str:
    stat, diff = build_diff_context(files)
    scope = scope_hint(files)
    history = recent_subjects()
    prompt = "Write a commit message for these staged changes.\n\n"
    if history:
        prompt += "Recent commits in this repo (match their style):\n"
        prompt += "\n".join(f"  {s}" for s in history) + "\n\n"
    if scope:
        prompt += f"Suggested scope: {scope}\n\n"
    if hint:
        prompt += f"Extra instruction from the author: {hint}\n\n"
    prompt += f"Summary:\n{stat}\nDiff:\n{diff}"

    feedback = ""
    for _ in range(3):
        msg = rewrap(clean(ask_model(SYSTEM, prompt + feedback)))
        problem = validate(msg)
        if not problem:
            return msg
        feedback = (f"\n\nYour previous answer was invalid because {problem}. "
                    f"Previous answer:\n{msg}\nWrite a corrected message.")
    raise GitError(f"Could not get a valid message ({problem}). Try again or use --edit.")


def edit_in_editor(text: str) -> str:
    editor = git("var", "GIT_EDITOR").strip()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as f:
        f.write(text + "\n\n# Lines starting with '#' are ignored.\n")
        path = f.name
    try:
        subprocess.run(f'{editor} "{path}"', shell=True, check=True)
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    finally:
        os.unlink(path)
    return "\n".join(l for l in lines if not l.startswith("#")).strip()


def show_message(msg: str) -> None:
    bar = color("─" * 60, "2")
    print(f"\n{bar}\n{color(msg.splitlines()[0], '1')}")
    rest = "\n".join(msg.splitlines()[1:])
    if rest.strip():
        print(rest)
    print(bar)


# ---------------------------------------------------------------- commands
def cmd_commit(args) -> int:
    provided = args.message
    if args.stdin:
        provided = sys.stdin.read()
    if git_path("MERGE_HEAD").exists():
        warn("A merge is in progress. Finish it with plain `git commit`, "
             "which uses Git's merge message.")
        return 1

    if args.all:
        if args.dry_run:
            pending = [f for f in git("diff", "--name-only", "-z").split("\0") if f]
            info(color("$ git add -u", "36") + color("  (dry run: not executed)", "2"))
            if pending:
                info("It would stage: " + ", ".join(pending))
                info("Stage them yourself and re-run with --dry-run to preview "
                     "the message without committing.")
                return 0
        else:
            run(["add", "-u"], dry_run=False)

    files = staged_files()
    if not files:
        if git("status", "--porcelain").strip():
            info("Nothing is staged. Stage files with `git add <file>`, "
                 "or use `gitpilot commit --all` for all tracked changes.")
        else:
            info("Nothing to commit, working tree clean.")
        return 1

    info(f"Staged {len(files)} file(s):")
    for f in files[:15]:
        info(f"  {f}")
    if len(files) > 15:
        info(f"  ... and {len(files) - 15} more")

    # --- safety checks (all deterministic) ---
    for f in large_files(files):
        warn(f"Large file staged: {f}. Consider Git LFS or .gitignore.")
    secrets = find_secrets()
    if secrets:
        error("Possible secrets in staged changes:")
        for s in secrets:
            error(f"  {s}")
        if not args.force:
            info("Remove them (and rotate the keys if they were ever pushed), "
                 "or re-run with --force if these are false alarms.")
            return 1
        warn("--force given. The diff will NOT be sent to the AI; "
             "write the message yourself.")
        msg = provided.strip() if provided else edit_in_editor("")
        if not msg:
            info("Empty message, commit cancelled.")
            return 1
        run(["commit", "-F", "-"], args.dry_run, input_text=msg)
        return 0

    branch = current_branch()
    if branch in PROTECTED_BRANCHES and not args.allow_protected:
        interactive = sys.stdin.isatty() and not args.stdin
        if not interactive or not confirm(
                f"You are committing directly to '{branch}'. Continue?"):
            info(f"Refusing to commit to '{branch}' without confirmation. "
                 "Tip: git switch -c my-feature, or pass --allow-protected.")
            return 1

    # --- message supplied by the caller (e.g. Claude Code): validate, commit ---
    if provided is not None:
        msg = rewrap(clean(provided))
        if problem := validate(msg):
            error(f"Invalid commit message: {problem}. Nothing was committed.")
            return 1
        show_message(msg)
        run(["commit", "-F", "-"], args.dry_run, input_text=msg)
        if not args.dry_run:
            ok(f"Committed {git('log', '-1', '--format=%h').strip()}: "
               f"{msg.splitlines()[0]}")
        return 0

    # --- AI writes, human decides ---
    info(color(f"\nWriting commit message with {MODEL}...", "2"))
    msg = generate_message(files, args.hint or "")
    while True:
        show_message(msg)
        if args.yes:
            break
        try:
            choice = input("[a]ccept  [e]dit  [r]egenerate  [c]ancel: ").strip().lower()
        except EOFError:
            choice = "c"
        if choice in ("a", ""):
            break
        if choice == "e":
            msg = edit_in_editor(msg)
            if not msg:
                info("Empty message, commit cancelled.")
                return 1
            if problem := validate(msg):
                warn(f"Note: {problem}. Keeping your version anyway.")
            break
        if choice == "r":
            hint = input("Hint for the new message (Enter to skip): ").strip()
            msg = generate_message(files, hint)
            continue
        info("Cancelled. Your changes are still staged.")
        return 1

    run(["commit", "-F", "-"], args.dry_run, input_text=msg)
    if not args.dry_run:
        ok(f"Committed {git('log', '-1', '--format=%h').strip()}: {msg.splitlines()[0]}")
    return 0


def cmd_undo(args) -> int:
    if not git_ok("rev-parse", "--verify", "HEAD~1"):
        error("There is no earlier commit to go back to.")
        return 1
    subject = git("log", "-1", "--format=%s").strip()
    if git_ok("rev-parse", "--verify", "-q", "HEAD^2"):
        error(f"'{subject}' is a merge commit.")
        info("A soft reset would silently drop the merge. Use `git revert -m 1 "
             "HEAD` instead, which adds a new commit that reverses it.")
        return 1
    # any remote branch, not just the upstream: the commit may be shared elsewhere
    remotes = git("branch", "-r", "--contains", "HEAD", check=False).splitlines()
    if remotes:
        error(f"'{subject}' is already pushed to {remotes[0].strip()}.")
        info("Undoing it would rewrite shared history. Use `git revert HEAD` "
             "instead, which adds a new commit that reverses it.")
        return 1

    info(f"This will undo: {color(subject, '1')}")
    info("Your changes will stay staged, and a backup branch will be created.")
    if not args.yes and not confirm("Continue?"):
        return 1

    backup = f"gitpilot/backup-{time.strftime('%Y%m%d-%H%M%S')}"
    run(["branch", backup, "HEAD"], args.dry_run)
    run(["reset", "--soft", "HEAD~1"], args.dry_run)
    if not args.dry_run:
        ok("Commit undone. Changes are still staged.")
        info(f"To restore it exactly: git reset --soft {backup}")
        info(f"When you no longer need the backup: git branch -D {backup}")
    return 0


def hook_path() -> Path:
    hooks = git_path("hooks")
    hooks.mkdir(parents=True, exist_ok=True)
    return hooks / "prepare-commit-msg"


def cmd_hook(args) -> int:
    path = hook_path()
    backup = path.with_suffix(".gitpilot-backup")
    ours = path.exists() and HOOK_MARKER in path.read_text(errors="replace")

    if args.action == "install":
        if path.exists() and not ours:
            if not args.force:
                error(f"A different hook already exists at {path}.")
                info("Re-run with --force to back it up and replace it.")
                return 1
            path.rename(backup)
            warn(f"Existing hook saved as {backup.name}")
        script = Path(__file__).resolve().as_posix()
        python = Path(sys.executable).as_posix()
        path.write_text(f"""#!/bin/sh
{HOOK_MARKER}
# Only fill the message for plain `git commit` (not -m, merge, amend, etc).
[ -n "$2" ] && exit 0
[ -n "$GITPILOT_SKIP" ] && exit 0
"{python}" "{script}" hook-run "$1" || true
exit 0
""")
        path.chmod(0o755)
        ok(f"Hook installed at {path}")
        info("Now `git commit` opens your editor with an AI-written message.")
        info("Skip it once with: GITPILOT_SKIP=1 git commit")
        return 0

    if not ours:
        info("No gitpilot hook is installed.")
        return 0
    path.unlink()
    if backup.exists():
        backup.rename(path)
        ok("Hook removed; your previous hook was restored.")
    else:
        ok("Hook removed.")
    return 0


def cmd_hook_run(args) -> int:
    """Called by the git hook. Must never block or break a commit."""
    msg_file = Path(args.msg_file)
    existing = msg_file.read_text(encoding="utf-8", errors="replace")
    if any(l.strip() and not l.startswith("#") for l in existing.splitlines()):
        return 0  # a message is already there; leave it alone
    try:
        files = staged_files()
        if not files:
            return 0
        secrets = find_secrets()
        if secrets:
            note = ("# gitpilot: AI message skipped, possible secrets staged:\n"
                    + "".join(f"#   {s}\n" for s in secrets))
            msg_file.write_text(note + existing, encoding="utf-8")
            return 0
        msg = generate_message(files)
        msg_file.write_text(msg + "\n\n" + existing, encoding="utf-8")
    except Exception as e:  # never break the user's commit
        msg_file.write_text(f"# gitpilot: could not write a message ({e})\n"
                            + existing, encoding="utf-8")
    return 0


def cmd_context(args) -> int:
    """Everything an AI needs to write the message, gathered by plain code."""
    files = staged_files()
    branch = current_branch()
    unstaged = [f for f in git("diff", "--name-only", "-z").split("\0") if f]
    untracked = [f for f in git("ls-files", "--others", "--exclude-standard",
                                "-z").split("\0") if f]
    secrets = find_secrets() if files else []
    ctx = {
        "branch": branch,
        "protected_branch": branch in PROTECTED_BRANCHES,
        "merge_in_progress": git_path("MERGE_HEAD").exists(),
        "staged_files": files,
        "unstaged_tracked_files": unstaged,
        "untracked_files": untracked[:50],
        "secrets_found": secrets,
        "large_files": large_files(files),
        "recent_commit_subjects": recent_subjects(),
        "suggested_scope": scope_hint(files) if files else None,
    }
    if secrets:
        ctx["diff"] = "(withheld: possible secrets are staged)"
    elif files:
        ctx["stat"], ctx["diff"] = build_diff_context(files)
    print(json.dumps(ctx, indent=2, ensure_ascii=False))
    return 0


GIT_CMD = r"(?:^|[;&|(]\s*|\s)git\s+(?:-C\s+\S+\s+)?"
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
LOSES_WORK = ("It permanently throws away uncommitted work. Ask the user first; "
              "suggest `git stash` to keep the changes, or let the user run the "
              "command themselves.")


def has_flag(text: str, short: str, long: str = "") -> bool:
    """-x alone or inside a cluster like -ux, or --long."""
    longs = f"--{long}|" if long else ""
    return bool(re.search(rf"\s({longs}-[a-zA-Z]*{short}[a-zA-Z]*)(?=\s|$)", text))


def guard_check(part: str, cmd: str) -> str | None:
    """Why this one shell command must be blocked, or None to allow it."""
    flags = QUOTED.sub("''", part)  # "-n" inside a commit message is not a flag

    def sub(name: str) -> bool:
        return bool(re.search(GIT_CMD + name + r"\b", flags))

    def dirty() -> bool:
        return bool(git("status", "--porcelain", check=False).strip())

    if sub("push") and (has_flag(flags, "f", "force") or (
            re.search(r"\s\+\S", flags) and "--force-with-lease" not in flags)):
        return ("gitpilot blocked a force push: it can permanently delete other "
                "people's commits on the remote. Ask the user first. If they "
                "agree, use --force-with-lease instead of --force.")

    if sub("push") and (has_flag(flags, "d", "delete") or re.search(r"\s:\S", flags)):
        return ("gitpilot blocked deleting a remote branch: other people may still "
                "need it. Ask the user to run it themselves if it should go.")

    if re.search(GIT_CMD + r"stash\s+(drop|clear)\b", flags):
        return ("gitpilot blocked `git stash drop/clear`: stashed work is gone "
                "for good. Use `git stash pop` to apply it, or ask the user to "
                "run it themselves.")

    if sub("commit") and git_ok("rev-parse", "--git-dir"):
        if has_flag(flags, "n", "no-verify"):
            return ("gitpilot blocked `git commit --no-verify`: it skips the "
                    "repository's safety hooks. Commit without it.")
        stages = re.search(GIT_CMD + r"add\b", QUOTED.sub("''", cmd))
        diff_args = ("HEAD",) if (has_flag(flags, "a", "all") or stages) and \
            git_ok("rev-parse", "--verify", "HEAD") else ("--staged",)
        secrets = find_secrets(diff_args)
        if stages:
            secrets = sorted(set(secrets + find_secrets_untracked()))
        if secrets:
            return ("gitpilot blocked this commit. Possible secrets: "
                    + "; ".join(secrets) + ". Do not commit them. Tell the user, "
                    "and suggest moving them to environment variables or a .env "
                    "file listed in .gitignore.")

    if sub("branch") and (has_flag(flags, "D") or (
            has_flag(flags, "d", "delete") and has_flag(flags, "f", "force"))):
        return ("gitpilot blocked `git branch -D`: it deletes commits that are "
                "not merged anywhere. Use `git branch -d`, or ask the user to "
                "run it themselves.")

    discards = (
        (sub("reset") and "--hard" in flags.split())
        or (sub("clean") and has_flag(flags, "f", "force")
            and not has_flag(flags, "n", "dry-run"))
        or (sub("checkout") and (re.search(r"\s(--|\.)(?=\s|$)", flags)
                                 or has_flag(flags, "f", "force")))
        or (sub("switch") and (has_flag(flags, "f", "force")
                               or "--discard-changes" in flags.split()))
        or (sub("restore") and not (has_flag(flags, "S", "staged")
                                    and not has_flag(flags, "W", "worktree"))))
    if discards and dirty():
        return f"gitpilot blocked `{part.strip()}`. {LOSES_WORK}"
    return None


def cmd_guard(args) -> int:
    """Claude Code PreToolUse hook for the Bash tool.
    Exit 2 = block the command and tell Claude why. Any error = allow (exit 0),
    so a bug here can never lock the user out of git."""
    try:
        data = json.load(sys.stdin)
        cmd = data.get("tool_input", {}).get("command", "") or ""
        if data.get("cwd"):
            os.chdir(data["cwd"])
    except Exception:
        return 0
    try:
        base = os.getcwd()
        for part in re.split(r"&&|\|\||;|\n", cmd):
            os.chdir(base)
            # `git -C dir ...` acts on another repo: check that one
            if m := re.search(r"\bgit\s+-C\s+(\"[^\"]*\"|'[^']*'|\S+)", part):
                os.chdir(os.path.expanduser(m.group(1).strip("'\"")))
            if reason := guard_check(part, cmd):
                print(reason, file=sys.stderr)
                return 2
    except Exception:
        return 0
    return 0


# -------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(prog="gitpilot",
                                 description="AI commit messages with safety checks")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("commit", help="commit staged changes with an AI message")
    c.add_argument("-a", "--all", action="store_true", help="stage tracked changes first")
    c.add_argument("-y", "--yes", action="store_true", help="accept without asking")
    c.add_argument("--hint", help="extra guidance, e.g. 'mention the login bug'")
    c.add_argument("--dry-run", action="store_true", help="show, don't commit")
    c.add_argument("--force", action="store_true", help="commit despite secret warnings")
    c.add_argument("--allow-protected", action="store_true",
                   help="allow committing directly to main/master")
    c.add_argument("-m", "--message", help="use this message instead of the AI")
    c.add_argument("--stdin", action="store_true", help="read the message from stdin")
    c.set_defaults(func=cmd_commit)

    x = sub.add_parser("context", help="print staged-change context as JSON")
    x.set_defaults(func=cmd_context)

    g = sub.add_parser("guard", help=argparse.SUPPRESS)
    g.set_defaults(func=cmd_guard)

    u = sub.add_parser("undo", help="undo the last (unpushed) commit safely")
    u.add_argument("-y", "--yes", action="store_true")
    u.add_argument("--dry-run", action="store_true")
    u.set_defaults(func=cmd_undo)

    h = sub.add_parser("hook", help="install or remove the git commit hook")
    h.add_argument("action", choices=["install", "uninstall"])
    h.add_argument("--force", action="store_true", help="replace an existing hook")
    h.set_defaults(func=cmd_hook)

    r = sub.add_parser("hook-run", help=argparse.SUPPRESS)
    r.add_argument("msg_file")
    r.set_defaults(func=cmd_hook_run)

    args = ap.parse_args()
    if args.command == "guard":
        return cmd_guard(args)
    try:
        if not git_ok("rev-parse", "--git-dir"):
            error("Not inside a Git repository.")
            return 1
        return args.func(args)
    except GitError as e:
        error(str(e))
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
