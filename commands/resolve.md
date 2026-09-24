---
description: Resolve merge/rebase conflicts by combining both sides, then continue
allowed-tools: Bash(python3:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git checkout --ours:*), Bash(git checkout --theirs:*), Bash(git rm:*), Read, Edit, Grep, Glob
---

You are resolving the user's Git conflicts with the gitpilot tool. The tool
finds the conflicts, checks the result, stages it and continues the
operation. Your job is to understand both sides and write the combined code.

gitpilot script path: !`echo "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py"`

Conflict state (JSON from `gitpilot conflicts`):

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py" conflicts 2>&1`

## Steps

1. If `files` is empty and `operation` is null, there is nothing to resolve.
   Say so and stop. If `files` is empty but an `operation` is in progress,
   skip to step 4.
2. Tell the user what is happening in one line: the `operation`, and what is
   coming in (`incoming`). If `note` is present, follow it: the sides are
   swapped during a rebase.
3. For each file in `files` (paths are relative to `root`):
   - See why each side changed it:
     `git log --oneline --left-right HEAD...<incoming_ref> -- <path>`.
     Read the three versions when it helps: `git show :1:<path>` (common
     ancestor), `:2:` (ours), `:3:` (theirs).
   - `both modified` / `both added`: edit the file so it keeps what BOTH
     sides meant to do, and remove every conflict marker. Only drop one
     side when the other clearly replaces it, and say so.
   - `lock_file` true: do not hand-merge. Take one side
     (`git checkout --theirs -- <path>`) and tell the user to regenerate it
     (e.g. `npm install`) after the operation finishes.
   - Binary files, or `deleted by us` / `deleted by them`: ask the user which
     version to keep (`git checkout --ours|--theirs -- <path>`, or
     `git rm <path>` to delete it).
   - If both sides change the same logic in ways that cannot both be true
     and the intent is unclear, show the user both versions briefly and
     ask. Do not guess.
4. Show a short summary per file: what each side changed and how you
   combined it. Ask whether to continue the `operation`. Wait for a yes.
5. Run: `python3 "<script path>" conflicts --finish`
   - If it lists files that still have markers, fix them and run it again.
   - During a rebase it may stop on the next commit's conflicts: run
     `python3 "<script path>" conflicts` and repeat from step 3.
6. Report the result. If this finished a merge on a branch with a pull
   request, ask whether to `git push`, and push only on a yes. After that the
   user can run `/gitpilot:merge`.

If the user wants to give up, `git <operation> --abort` restores the state
from before it started; say that it throws away the resolution work, and run
it only if they confirm. Never force-push.
