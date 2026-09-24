---
description: Review this branch's commits before merging (bugs, security, missing tests)
argument-hint: "[--comment] [base branch] [what to focus on]"
allowed-tools: Bash(python3:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(gh pr view:*), Read, Grep, Glob
---

You are reviewing the user's branch before it is merged, with the gitpilot
tool. The tool gathers the exact facts (which commits, which files, secret
scan). Your job is to read the changes and report real problems.

gitpilot script path: !`echo "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py"`

User arguments: $ARGUMENTS

Branch context (JSON from `gitpilot review`, compared with the default base):

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gitpilot.py" review 2>&1`

## Steps

1. If the arguments name a base branch (e.g. `develop`), run
   `python3 "<script path>" review <base>` and use that result instead.
   If the arguments contain `--comment` and name no base, run
   `gh pr view --json baseRefName` and review against `origin/<baseRefName>`
   the same way, so you review exactly what the PR shows.
   Treat the rest of the arguments as what to focus on.
2. If `commits` is empty, tell the user there is nothing on `branch` that is
   not already in `base`. If `branch` is the base itself, suggest reviewing
   from a feature branch. Stop.
3. If `secrets_found` is not empty, report it as the first blocker. Say where
   each one is, without repeating the secret values.
4. If `large_files` is not empty, warn about them.
5. Read the changes: `git diff <range> -- <file>` for each file in `files`,
   skipping `skip_files` (lock/generated files). Read the surrounding code
   with Read or Grep whenever you need it to judge a change.
6. Look for real problems in the changed code:
   - bugs, wrong logic, unhandled edge cases, broken error handling
   - security: injection, unsafe input handling, leaked data
   - behaviour changes that callers elsewhere in the repo do not expect
   - new non-trivial logic with no test
   - leftover debug code or commented-out code
   Skip style nitpicks unless the user asked for them. Do not report
   something unless you can say how it fails.
7. Report:
   - First line: **Ready to merge** or **Fix before merging**.
   - Then findings, most serious first, one per item:
     `file:line` — the problem — how it fails — the suggested fix.
   - If there are no findings, say so plainly.
   - If `uncommitted_changes` is true, note that uncommitted changes were not
     reviewed.
8. If the arguments contain `--comment` and there are findings, ask: "Post
   these N comments to the PR?" Wait for a yes. Then run this exact form, with
   `line` being the line number in the new version of the file:

   ```
   python3 "<script path>" pr-comment <<'GITPILOT_REVIEW'
   {"body": "<verdict line and a one-line summary>",
    "comments": [{"path": "src/app.py", "line": 42, "body": "<problem — how it fails — fix>"}]}
   GITPILOT_REVIEW
   ```

   If gitpilot says to push first or that there is no PR, tell the user
   (`git push`, or `gh pr create`) and stop. Do not push for them.
9. Offer to fix the findings. Do not edit files until the user says yes.

Never commit, push, or merge. Never post to GitHub without the user's yes.
