---
name: review-eip
description: Review the current PR for EIP implementation correctness against the EIP text. Use when asked to review a spec PR that implements an EIP.
---

Review the current PR for EIP implementation correctness.

## Steps

1. Run `gh pr view --json title,body,labels` to get PR context
2. Run `gh pr diff` to get the full changeset
3. Identify which EIP is being implemented from the PR title/body/branch name
4. For each changed file in the execution-specs fork directories:
   - Verify the implementation matches the EIP specification exactly
   - Check state transition logic handles all edge cases (empty inputs, max values, zero values)
   - Verify gas calculations match the EIP document
   - Check that no floating point arithmetic is used in consensus-critical paths
   - Verify `Uint`/`U256` types are used correctly, never raw `int` for on-chain values
5. Check cross-fork consistency: if the change exists in multiple forks, verify they are consistent

## Output format

For each finding, report:
- File and line number
- Severity: CRITICAL (consensus bug) / HIGH (correctness issue) / MEDIUM (edge case) / LOW (style)
- What the code does vs what the EIP specifies
- Suggested fix

Only report real issues. Do not nitpick style, formatting, or things a linter would catch.
