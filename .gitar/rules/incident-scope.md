---
title: "Incident fixes stay in scope"
description: "An incident PR changes only the discovery workload's replicas or resources (OPS-1)"
type: check
when: "PRs labeled incident, or authored by dash0-app[bot]"
actions: "Fail the check for any change outside the allowed fields, and comment whether the change matches the evidence in the description"
---

# Incident fixes stay in scope

An incident PR may change only the discovery workload's `replicas` or `resources` in `deploy/env/aks/values.yaml`.

Fail when it also changes:

- any other file;
- the image, the per-pod admission limit or any other value;
- tests, CI, Gitar configuration, the load-test rate, or acceptance thresholds.

Also check that the description links the telemetry or investigation that supports the change, and that the new
values stay within the chart's bounds. The `incident-scope` CI job enforces the same file and key limits
mechanically.
