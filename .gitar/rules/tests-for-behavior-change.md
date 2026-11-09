---
title: "Tests prove behavior changes"
description: "New behavior has tests; a bug fix has a regression test that fails without it (TEST-1)"
type: check
when: "PRs that change code under backend/ or apps/web/src/"
actions: "Fail the check and name the behavior that has no test"
---

# Tests prove behavior changes

Fail when:

- new or changed behavior has no test that would fail if the behavior broke;
- a bug fix has no regression test that fails without the fix, unless the description says why one isn't practical
  and how the fix was verified instead;
- a test or assertion was weakened or deleted to make the PR pass.

Tests must check outcomes (what a user or caller sees), not mirror the implementation. Rules with exact thresholds
(the 30-second crowd freshness, the 2-minute buffer, the RRF constant) need a test at the boundary value.
