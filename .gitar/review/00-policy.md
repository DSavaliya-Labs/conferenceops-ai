# Review policy: how these files fit together

1. `AGENTS.md` is the standard. Its rule IDs (ACCESS-1 … TEST-1) win over anything below, and every finding cites
   one when it applies.
2. `project.md` lists what is easy to miss in this repository; `severity.md` says how to grade.
3. The numbered files here (`01-` to `06-`) are the general engineering standards for this codebase: security,
   coding, design, API and data, testing and operations, delivery. They have their own IDs, always written with
   the `STD-` prefix and two digits (`STD-SEC-01`, `STD-OPS-11`), so a finding can point at the exact expectation and
   can never be mistaken for an `AGENTS.md` rule (`SEC-1`, `OPS-1`). Where a general standard and an `AGENTS.md` rule
   overlap, cite the `AGENTS.md` rule.
4. The rules in `.gitar/rules/` are the checks that run on every pull request they match. They are the enforcement;
   the files here are the explanation.

How to review with them:

- Read the pull request's description first and judge the change against what it says it does. A description that
  skips "how it was verified" or "what it doesn't cover" is a finding by itself (`pr-description` rule).
- Report what is wrong and why it matters to an attendee, an operator or the next developer, with the smallest fix.
  One finding with a concrete fix beats three vague ones.
- Don't repeat what the linters already enforce: ruff and its formatter for Python, Prettier and the Angular
  compiler for the web app, shellcheck for scripts, actionlint for workflows, helm lint and `check-chart-policy.sh`
  for the chart. If a linter should catch something and doesn't, say so once.
- A change that only moves or renames code gets a short review. A change to a boundary (a route, a schema, a
  migration, a workflow, the chart, a security header, telemetry) gets a full one.
- When a pull request was opened by an agent (Agent0, Radar's bridge, Gitar's own fixes), apply exactly the same
  standard, and check the change against the evidence quoted in its description.
- Never ask for work the pull request doesn't need. A missing test for the behavior it changes is required (TEST-1);
  a missing test for a neighboring, unchanged behavior is a Minor note at most.
