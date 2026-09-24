---
description: Undo the last commit safely (keeps changes staged, makes a backup branch)
allowed-tools: Bash(python3:*)
---

You are undoing the user's last commit with the gitpilot tool.

gitpilot script path: !`echo "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py"`

Dry-run output (nothing has been changed yet):

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py" undo --dry-run --yes 2>&1`

## Steps

1. If the output says the commit is already pushed, or there is no earlier
   commit, explain that to the user. For a pushed commit, suggest
   `git revert HEAD`, which safely adds a new commit reversing it. Do not run
   anything else.
2. Otherwise, tell the user which commit will be undone, that their changes
   will stay staged, and that a backup branch will be created. Ask them to
   confirm and wait for the answer.
3. Only if they confirm, run: `python3 "<script path>" undo --yes`
4. Report the result, including the backup branch name and how to restore it.

Never use `git reset --hard` and never push.
