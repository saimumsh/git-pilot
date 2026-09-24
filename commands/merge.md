---
description: Merge this branch's pull request safely (checks, conflicts, squash, cleanup)
argument-hint: "[PR number] [--method squash|merge|rebase]"
allowed-tools: Bash(python3:*), Bash(git fetch:*), Bash(git merge origin/:*), Bash(git status:*), Bash(git log:*), Bash(gh pr view:*), Bash(gh pr checks:*)
---

You are merging the user's pull request with the gitpilot tool. The tool does
every exact step (readiness checks, secret scan, the merge itself). Your job
is to explain and ask.

gitpilot script path: !`echo "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py"`

User arguments: $ARGUMENTS

PR readiness (JSON from `gitpilot pr-merge`, nothing has changed yet):

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py" pr-merge 2>&1`

## Steps

1. If the arguments name a PR number, run `python3 "<script path>" pr-merge <number>`
   and use that result instead. The method is `squash` unless the arguments
   say `--method merge` or `--method rebase`.
2. If the output is an error (no PR for this branch, `gh` missing, not logged
   in), explain it and stop. To open a PR: `gh pr create`.
3. If `problems` mentions conflicts:
   - Explain that `base` changed since the branch was made. Offer to bring it
     in on this branch: `git fetch origin <base>` then `git merge origin/<base>`.
     This only changes the local branch. Run it only if the user agrees, and
     only when `git status` shows no uncommitted changes.
   - If git reports conflicts, tell the user to run `/gitpilot:resolve`, then
     push, then `/gitpilot:merge` again. Stop.
   - If it merged cleanly, ask whether to `git push`; push only on a yes,
     then stop and tell them to re-run `/gitpilot:merge` once GitHub updates.
4. For any other `problems`, list them and stop. Suggestions:
   - checks still running: wait, or watch with `gh pr checks --watch`
   - failing checks: `gh pr checks` shows which; fix and push
   - draft: `gh pr ready` once it is done
   - changes requested / protected branch: get the review first
   - possible secrets: remove them from the branch and rotate the keys
   Never try to get around a problem (no `--admin`, no force).
5. Mention every item in `warnings`.
6. Write the commit subject for `squash` or `merge`: the PR `title` as a
   Conventional Commit (`type(scope): summary`, at most 72 characters,
   imperative, no trailing period). Show: PR number and title, `branch` →
   `base`, the method, the subject, and that the remote branch will be
   deleted. Ask the user to approve. Wait for the answer.
7. Only on a yes, run this exact form:

   ```
   python3 "<script path>" pr-merge <number> --method <method> --stdin <<'GITPILOT_MSG'
   <the approved subject>
   GITPILOT_MSG
   ```

   gitpilot re-runs every check before merging and refuses if anything
   changed. If it says the subject is invalid, fix it and run it once more.
8. Report the result and the PR URL.
