# Delivery: CI, containers, chart and workflows

Every merge to `main` deploys to AKS. Review a delivery change as if it were going to production in the next ten
minutes, because it is.

- **STD-DEL-01 CI checks are always present.** The required check names in `ci.yml` never disappear or get renamed in
  the same pull request that changes what they test; a check that can't run yet passes as not-applicable and says so
  in its log.
- **STD-DEL-02 Workflows are least-privilege and pinned:** explicit `permissions`, actions pinned to a version, tools
  downloaded with a pinned version and a checksum where the vendor publishes one, OIDC for Azure, no long-lived
  cloud secret. A workflow run must never print a secret or a token (STD-SEC-10).
- **STD-DEL-03 Images are reproducible:** pinned base images, one image per service with the command chosen by the
  Deployment, non-root user, no package manager left in the final stage, the commit SHA as the tag. `latest` and
  floating tags are findings.
- **STD-DEL-04 The chart renders and passes policy** with the AKS values: `helm lint`, `helm template … | scripts/check-chart-policy.sh`,
  kubeconform. A new container satisfies K8S-1 (non-root, no privilege escalation, dropped capabilities, read-only
  root, requests and a memory limit, probes independent of downstream).
- **STD-DEL-05 Upgrades are safe to repeat:** `helm upgrade --install --reset-values --server-side=true --force-conflicts`,
  hooks ordered (migrations before the seed), and a change to a Secret or ConfigMap rolls the pods that read it.
- **STD-DEL-06 Rollback is possible.** A deploy change says how to go back (the previous image tag, `helm rollback`,
  the reverse migration) and doesn't combine an irreversible data change with a code change.
- **STD-DEL-07 Public entry point.** The web Service keeps `externalTrafficPolicy: Cluster`, an HTTP readiness probe,
  and serves HTTP and HTTPS; a change to nginx's templates is tested with `nginx -t` in the image and the
  read-only-root `docker run` in `AGENTS.md`.
- **STD-DEL-08 Incident changes are narrow** (OPS-1): only the discovery workload's `replicas` or `resources` in
  `deploy/env/aks/values.yaml`, within the chart's bounds, with the evidence in the description. Anything else in
  an incident pull request is blocked.
- **STD-DEL-09 Scripts that touch the cluster or the cloud** pass `--subscription`, name the resource group, print
  what they will do, and never delete without the guard `main "$@"; exit`.
- **STD-DEL-10 The deploy verifies itself:** after `helm upgrade`, the workflow checks that the public URL serves the
  app, its config and a search, and marks the result in Dash0. A change to the deploy keeps or extends that check.
