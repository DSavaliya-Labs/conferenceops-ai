---
title: "PR description is complete"
description: "Why, what, how it was verified, and what it doesn't cover; stacked PRs name their base and merge order"
type: check
when: "Every pull request"
actions: "Fail the check and comment with the missing sections"
---

# PR description is complete

The description must say:

- **why** the change is needed;
- **what** changed;
- **how it was verified:** commands run, tests added, or what was seen in the running app or in Dash0;
- **what it doesn't cover.**

A stacked PR must also name the PR it sits on and the merge order. Fail with the list of missing parts. A
documentation-only PR may keep "how it was verified" to one line.
