---
description: Write a Conventional Commit message for the staged changes and commit safely
argument-hint: "[--all] [--yes] [hint for the message]"
allowed-tools: Bash(python3:*), Bash(git add:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*)
---

You are committing the user's changes with the gitpilot tool. The tool does
every exact step (collecting the diff, safety checks, format validation,
running git). Your job is to write the message and talk to the user.

gitpilot script path: !`echo "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py"`

User arguments: $ARGUMENTS

Current repository context (JSON from `gitpilot context`):

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py" context`

## Steps

1. If `merge_in_progress` is true, stop. Tell the user to finish the merge with
   plain `git commit`, which uses Git's own merge message.
2. If `staged_files` is empty:
   - If the arguments contain `--all`, run `git add -u`.
   - Otherwise, list `unstaged_tracked_files` and `untracked_files` and ask the
     user which ones to stage, then `git add` exactly those. Never stage
     untracked files without asking.
   - After staging anything, run `python3 "<script path>" context` again and
     use only the new result from here on.
3. If `secrets_found` is not empty, STOP. List where each possible secret is,
   without repeating the secret values. Do not commit. Suggest moving them to
   environment variables or a `.env` file listed in `.gitignore`, and rotating
   any key that was ever pushed.
4. If `large_files` is not empty, warn the user and suggest Git LFS or `.gitignore`.
5. Write the commit message from `stat` and `diff`:
   - Line 1: `type(scope): summary`, at most 72 characters, imperative mood
     ("add", not "added"), no trailing period.
   - `type` is one of: feat, fix, docs, style, refactor, perf, test, build, ci,
     chore, revert. Use `suggested_scope` if it fits.
   - Match the style of `recent_commit_subjects`.
   - For non-trivial changes, add a blank line, then a short body saying WHAT
     changed and WHY. Use `- ` bullets for several changes.
   - Only describe what is visible in the diff. Follow any hint in the arguments.
6. Unless the arguments contain `--yes`, show the message and ask the user to
   approve or request changes. Wait for their answer.
   If the last line of `stat` shows more than 10 files or more than 300
   changed lines, add one line to that question: "This is a large change —
   run `/code-review` before committing?" If they want a review, stop here;
   their changes stay staged. (`/gitpilot:review` reviews committed branch
   work before merging; `/code-review` covers staged changes.)
7. If `protected_branch` is true, tell the user they are committing directly
   to `branch` and ask whether that is intended. Only add `--allow-protected`
   if they explicitly agree.
8. Commit with this exact form (the quoted heredoc keeps the text safe):

   ```
   python3 "<script path>" commit --yes --stdin <<'GITPILOT_MSG'
   <the approved message>
   GITPILOT_MSG
   ```

   If gitpilot says the message is invalid, fix the reported problem and run it
   once more.
9. Report the commit hash and subject line.

Never run `git commit` directly, never use `--no-verify`, and never push.
