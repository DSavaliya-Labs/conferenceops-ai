# Design principles

These are **Minor (advisory) by default.** Raise one only when the pull request shows a concrete consequence: a
caller that now has to know too much, a test that becomes impossible, a change that will have to be made in two
places, a contract that silently changed. Name that consequence; a principle's name alone is not a finding.

- **STD-DESIGN-01 One reason to change.** A module owns one concern: routes parse and answer, services decide,
  repositories read and write, the worker projects. A route that opens a session and builds an Elasticsearch
  query, or a component that calls `fetch` directly, has crossed a layer.
- **STD-DESIGN-02 Depend on the narrow interface.** Services take the `Data`/`PortalData` protocols, not a concrete
  PostgreSQL class, so tests use the fakes in `backend/tests/` and the web specs use `test-support.ts`. A new
  dependency on a concrete class where a protocol exists is a finding.
- **STD-DESIGN-03 Don't repeat a decision.** The same rule (a filter, a time-zone conversion, a status code, a label)
  lives in one function and is called from both places. Copying it is how PRIV-1 and TRANSCRIPT-1 get broken in one
  path and not the other.
- **STD-DESIGN-04 Keep it as simple as the next change needs.** No abstraction for a single caller, no configuration for
  a value nobody changes, no plugin system for two implementations. Prefer a plain function to a class with one
  method.
- **STD-DESIGN-05 You aren't going to need it.** A field, parameter or route that nothing uses yet is removed, or the
  pull request says which follow-up uses it.
- **STD-DESIGN-06 Explicit over clever.** A reader should be able to trace a request from the route to the response
  without opening more than three files. Metaprogramming, decorators that change control flow and implicit globals
  need a comment that says why.
- **STD-DESIGN-07 Fail in a known way.** Every external call has a defined failure path: fall back (AI-1: deterministic
  answer when the model is slow), reject fast (admission control), or surface an error the UI shows. "It will
  probably work" isn't a path.
- **STD-DESIGN-08 Names say what, comments say why.** A comment that restates the code goes; a comment that explains a
  non-obvious constraint (a rule ID, a vendor quirk, a measured number) stays.
