"""Self-check for gitpilot. Run: python3 scripts/test_gitpilot.py"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import gitpilot as g

SCRIPT = Path(__file__).with_name("gitpilot.py")


def guard(cmd: str, cwd: Path) -> int:
    data = json.dumps({"tool_input": {"command": cmd}, "cwd": str(cwd)})
    return subprocess.run([sys.executable, SCRIPT, "guard"], input=data,
                          capture_output=True, text=True).returncode


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_text() -> None:
    assert g.clean('"fix: add x"') == "fix: add x"
    assert g.clean('fix: handle "foo"') == 'fix: handle "foo"'
    url = "https://example.com/" + "a" * 80
    assert url in g.rewrap("fix: x\n\n" + url)
    assert g.validate("feat(api): add x") is None
    assert g.validate("added stuff")
    pr_diff = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
               "@@ -10,2 +10,3 @@ def f():\n a\n+b\n c\n"
               "@@ -40 +41 @@\n-old\n+new\n"
               "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n"
               "@@ -1 +0,0 @@\n-bye\n")
    assert g.diff_lines(pr_diff) == {"x.py": {10, 11, 12, 41}}
    # built by concatenation so this file never trips a secret scanner itself
    for s in ["sk_" + "live_" + "a" * 24, "AI" + "za" + "a" * 35,
              "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
              "AK" + "IA" + "ABCDEFGHIJKLMNOP"]:
        assert any(re.search(p, s) for _, p in g.SECRET_PATTERNS), s


def test_guard() -> None:
    with tempfile.TemporaryDirectory() as d:
        root, repo = Path(d), Path(d) / "repo"
        repo.mkdir()
        sh(repo, "init", "-q")
        sh(repo, "config", "user.email", "t@t")
        sh(repo, "config", "user.name", "t")
        (repo / "a.txt").write_text("one\n")
        sh(repo, "add", "a.txt")
        sh(repo, "commit", "-qm", "init")
        (repo / "a.txt").write_text("two\n")  # uncommitted work

        for cmd in ["git push -f", "git push -uf origin main", "git push --force",
                    "git push origin +main", "git commit -n -m x",
                    "git commit -an -m x", "git reset --hard", "git clean -fd",
                    "git checkout -- .", "git restore a.txt", "git branch -D x",
                    "ls && git reset --hard HEAD", "git stash drop",
                    "git stash clear", "git push origin --delete x",
                    "git push -d origin x", "git push origin :x",
                    "git switch -f main", "git switch --discard-changes main"]:
            assert guard(cmd, repo) == 2, f"should block: {cmd}"

        for cmd in ["git push --force-with-lease", 'git commit -m "fix -n flag"',
                    "git restore --staged a.txt", "git branch -d x",
                    "git reset --soft HEAD~1", "git clean -n", "ls -la",
                    "git stash", "git stash pop", "git push origin main",
                    "git switch main", "git switch -c feature"]:
            assert guard(cmd, repo) == 0, f"should allow: {cmd}"

        (repo / "key.py").write_text('k = "' + "AK" + "IA" + "ABCDEFGHIJKLMNOP" + '"\n')
        sh(repo, "add", "key.py")
        assert guard("git commit -m x", root) == 0  # root is not a repo
        assert guard("git -C repo commit -m x", root) == 2


def test_review() -> None:
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sh(repo, "init", "-q", "-b", "main")
        sh(repo, "config", "user.email", "t@t")
        sh(repo, "config", "user.name", "t")
        sh(repo, "commit", "-q", "--allow-empty", "-m", "init")
        sh(repo, "switch", "-qc", "feature")
        (repo / "key.py").write_text('k = "' + "AK" + "IA" + "ABCDEFGHIJKLMNOP" + '"\n')
        (repo / "yarn.lock").write_text("x\n")
        sh(repo, "add", ".")
        sh(repo, "commit", "-qm", "feat: add key")
        r = subprocess.run([sys.executable, SCRIPT, "review"], cwd=repo,
                           capture_output=True, text=True)
        ctx = json.loads(r.stdout)
        assert ctx["base"] == "main" and len(ctx["commits"]) == 1, ctx
        assert ctx["skip_files"] == ["yarn.lock"], ctx
        assert ctx["secrets_found"], ctx


def test_merge_problems() -> None:
    pr = {"baseRefName": "main", "state": "OPEN", "isDraft": False,
          "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
          "reviewDecision": "", "statusCheckRollup": [
              {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
              {"context": "ci/legacy", "state": "SUCCESS"}]}
    assert g.merge_problems(pr) == ([], [])
    bad = dict(pr, isDraft=True, mergeable="CONFLICTING", statusCheckRollup=[
        {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"},
        {"name": "e2e", "status": "IN_PROGRESS", "conclusion": ""}])
    problems, _ = g.merge_problems(bad)
    assert len(problems) == 4 and "lint" in problems[2] and "e2e" in problems[3], problems
    assert g.merge_problems(dict(pr, mergeStateStatus="BEHIND"))[1]


def test_conflicts() -> None:
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, SCRIPT, "conflicts", *args],
                              cwd=repo, capture_output=True, text=True)

    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sh(repo, "init", "-q", "-b", "main")
        sh(repo, "config", "user.email", "t@t")
        sh(repo, "config", "user.name", "t")
        (repo / "a.txt").write_text("base\n")
        sh(repo, "add", ".")
        sh(repo, "commit", "-qm", "init")
        sh(repo, "switch", "-qc", "f")
        (repo / "a.txt").write_text("feature\n")
        sh(repo, "commit", "-qam", "f")
        sh(repo, "switch", "-q", "main")
        (repo / "a.txt").write_text("main\n")
        sh(repo, "commit", "-qam", "m")
        subprocess.run(["git", "merge", "f"], cwd=repo, capture_output=True)

        ctx = json.loads(run().stdout)
        assert ctx["operation"] == "merge", ctx
        assert ctx["files"] == [{"path": "a.txt", "kind": "both modified",
                                 "markers": 3, "lock_file": False}], ctx
        assert guard("git checkout --theirs -- a.txt", repo) == 0
        assert run("--finish").returncode == 1  # markers still there

        (repo / "a.txt").write_text("main\nfeature\n")
        assert run("--finish").returncode == 0
        assert json.loads(run().stdout)["operation"] is None
        log = subprocess.run(["git", "log", "-1", "--format=%p"], cwd=repo,
                             capture_output=True, text=True).stdout.split()
        assert len(log) == 2  # a real merge commit


if __name__ == "__main__":
    test_text()
    test_guard()
    test_review()
    test_merge_problems()
    test_conflicts()
    print("ok")
