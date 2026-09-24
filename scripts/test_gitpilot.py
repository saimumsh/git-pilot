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


if __name__ == "__main__":
    test_text()
    test_guard()
    print("ok")
