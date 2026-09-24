# gitpilot for Claude Code

Safe Git helpers for Claude Code. Claude writes the text; a Python script does
every exact step and the safety checks. No API key is needed.

## Commands
- `/gitpilot:commit` — Conventional Commit message for staged changes
  - `/gitpilot:commit --all` stages tracked changes first
  - `/gitpilot:commit --yes` skips the approval step
  - `/gitpilot:commit mention it fixes #42` passes a hint
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
  are uncommitted changes

`git -C <dir>` is checked against `<dir>`. The guard never blocks on its own
errors, so a bug can't lock you out of git.

## Standalone CLI
`scripts/gitpilot.py` also works without Claude Code (it then calls the
Anthropic API: `pip install anthropic`, set `ANTHROPIC_API_KEY`):
- `gitpilot.py commit [--all] [--dry-run]` — AI message, accept/edit/regenerate
- `gitpilot.py hook install` — fill the message on every plain `git commit`

## Requirements
Python 3.10+ and Git.

## Tests
`python3 scripts/test_gitpilot.py`
