Gitar may fix Important and Minor findings in application code, tests, docs and the Helm chart.

Do not auto-fix anything in `.github/workflows/`, `.gitar/`, `scripts/protect-main.sh` or `AGENTS.md`; leave those
findings for the author.

A fix must never weaken or delete a test, an assertion, a CI check, an acceptance threshold or a load-test rate to
make something pass. Add or strengthen a test instead.
