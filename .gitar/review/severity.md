# Severity policy

Rate every finding, and cite the `AGENTS.md` rule it breaks (for example ACCESS-1).

- **Critical (blocks merge):**
  - leaks a secret;
  - lets one persona read or change another's private data;
  - shows an access claim that isn't in `access_facts` (ACCESS-1);
  - puts generated words in a real speaker's mouth (SPEAKER-1);
  - lets unpublished transcript text reach search or Ask (TRANSCRIPT-1);
  - sends what attendees type to telemetry or logs (PRIV-1).
- **Important (blocks merge):**
  - wrong behavior a user would hit;
  - a missing permission check (AUTHZ-1);
  - a non-atomic write and outbox (DATA-1);
  - a fixed-offset time (TIME-1);
  - an API change without the regenerated client (API-1);
  - a keyboard trap or an unlabeled control (A11Y-1);
  - an insecure workload setting (K8S-1);
  - an incident PR outside its scope (OPS-1);
  - new logic with no test at all, or a bug fix with no regression test (TEST-1).
- **Minor (advisory):** naming, clarity, small simplifications, and a missing test for an edge case when the main
  behavior is covered.

Don't raise style preferences that the linters already enforce. Prefer one finding with a concrete fix over several
vague ones.
