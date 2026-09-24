# gitpilot for Claude Code

Safe Git helpers for Claude Code. Claude writes the text; a Python script does
every exact step and the safety checks. No API key is needed.

## Commands
- `/gitpilot:commit` — Conventional Commit message for staged changes
  - `/gitpilot:commit --all` stages tracked changes first
  - `/gitpilot:commit --yes` skips the approval step
  - `/gitpilot:commit mention it fixes #42` passes a hint
- `/gitpilot:review` — review this branch's commits before merging: bugs,
  security, missing tests, secrets. Verdict first, then `file:line` findings
  - `/gitpilot:review develop` compares with `develop` instead of `main`
  - `/gitpilot:review focus on error handling` narrows the review
  - `/gitpilot:review --comment` also posts the findings as inline comments
    on the branch's GitHub PR, after you approve (needs `gh`, and the branch
    pushed)
- `/gitpilot:merge` — merge this branch's PR after checking it is ready
  (checks passed, no conflicts, not a draft, no changes requested, no
  secrets). Squash by default, deletes the remote branch, pulls `main`
  - `/gitpilot:merge 12 --method rebase` picks the PR and method
  - on conflicts it offers to merge `main` into your branch locally
- `/gitpilot:resolve` — resolve merge/rebase/cherry-pick conflicts: Claude
  combines both sides, asks when intent is unclear, and gitpilot refuses to
  continue while any conflict marker is left
- `/gitpilot:undo` — undo the last unpushed commit, keeping changes staged
  (refuses merge commits and commits found on any remote branch)

## Always-on guard (hook)
Whenever Claude runs a Bash command, gitpilot checks it first and blocks:
- `git commit` when possible secrets (AWS/GitHub/Stripe/Google/Slack keys,
  private keys, JWTs, hardcoded passwords) are in the changes being committed
- `git commit --no-verify` / `-n`
- `git push --force` / `-f` / `+branch` (use `--force-with-lease` after the
  user agrees)
- `git branch -D` (use `-d`)
- `git push --delete` / `-d` / `:branch` (deleting a remote branch)
- `git stash drop` / `git stash clear`
- `git reset --hard`, `git clean -f`, `git checkout -- <path>`,
  `git restore <path>`, and `git switch -f` / `--discard-changes` while there
  are uncommitted changes (`git checkout --ours/--theirs` stays allowed for
  resolving conflicts)

`git -C <dir>` is checked against `<dir>`. The guard never blocks on its own
errors, so a bug can't lock you out of git.

## Standalone CLI
`scripts/gitpilot.py` also works without Claude Code (it then calls the
Anthropic API: `pip install anthropic`, set `ANTHROPIC_API_KEY`):
- `gitpilot.py commit [--all] [--dry-run]` — AI message, accept/edit/regenerate
- `gitpilot.py hook install` — fill the message on every plain `git commit`

## Requirements
Python 3.10+ and Git. `review --comment` and `merge` also need the GitHub CLI
(`gh`, logged in with `gh auth login`).

## Tests
`python3 scripts/test_gitpilot.py`
