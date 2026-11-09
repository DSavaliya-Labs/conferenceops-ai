Raise to **high risk** any pull request that changes:

- CI or deploy workflows (`.github/workflows/`), branch protection (`scripts/protect-main.sh`), or Gitar
  configuration (`.gitar/`);
- the rules in `AGENTS.md`;
- secret handling, authentication headers, or permission and ownership checks;
- the publication boundary for transcripts (TRANSCRIPT-1), or how access facts are shown (ACCESS-1);
- database migrations that drop or rewrite data.

Do not raise the risk of a pull request whose only change is the discovery workload's `replicas` or `resources` in
`deploy/env/aks/values.yaml`, within the chart's bounds. That is the planned incident fix, and it must stay
mergeable without a human.
