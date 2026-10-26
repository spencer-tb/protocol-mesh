---
name: review-tests
description: Review the current PR for test quality and coverage, and list the concrete test cases that are missing. Use when asked to review a PR that adds or changes tests.
---

Review the current PR for test quality and coverage.

## Steps

1. Run `gh pr diff` to get the full changeset
2. Identify all new or modified test files
3. For each test file:
   - Check that happy path cases are covered
   - Check edge cases: zero values, max uint256, empty calldata, out-of-gas scenarios
   - Check boundary conditions for gas calculations
   - Check that invalid inputs are tested and produce the correct error/exception
   - Check that state is properly set up and torn down
   - Verify test fixtures will be generated correctly by the `fill` command
4. Identify any implementation changes that do NOT have corresponding tests
5. Suggest specific test cases that are missing

## Output format

For each gap:
- What is not tested
- Why it matters (what bug could slip through)
- A concrete test case description (inputs, expected output)

Be specific. "Add more edge case tests" is useless. "Test CREATE2 with zero-length initcode and verify gas is still charged" is useful.
