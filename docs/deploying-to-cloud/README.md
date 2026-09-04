# Deploying to the cloud

Three reference deployments for a full NovoMCP stack on AWS, GCP, and Azure. Each is opinionated but minimal: single VM or small cluster, the engine + the web dashboard + a handful of compute services, HTTPS, no managed database (audit logs stay on disk unless you swap in Postgres or DynamoDB).

These are reference recipes, not supported products. Copy, adapt, and own the resulting deployment.

## The stack is two services (plus optional compute)

Every recipe deploys at least two things, wired together:

- **Engine** (`orchestrator/`, port 8018) — the headless REST/MCP backend. Keep it **internal**; only the dashboard needs to reach it. Expose its API publicly only if you deliberately want programmatic REST/MCP access.
- **Web dashboard** (`frontend-nextjs/`, port 3000) — the browser-facing UI. Set `NOVOMCP_ENGINE_URL` to the engine's internal address. This is the only service most deployments expose publicly.

Optional compute services (docking, MD, QM, NNP, …) are more separate services, added by env var when you need them — see [Deploying services](../deploying-services/README.md).

!!! warning "Local mode has no authentication — don't expose the engine publicly as-is"
    The engine defaults to `NOVO_AUTH=local`: **every request resolves to a `local` user with unlimited access.** That's correct for a laptop, but on a reachable cloud host it means anyone who can hit port 8018 can run tools. Before exposing anything publicly:

    - **Keep the engine private** (security group / firewall / internal-only load balancer) and put auth on the dashboard, **or**
    - **Bring your own auth** via the pluggable spine — `NOVO_AUTH=custom` loads your own `AuthGate` (API keys / JWT / your IdP). See [Architecture → the spine](../architecture.md).

    `NOVO_AUTH=hosted` expects **our** managed backend and is not part of self-hosting — use `local` behind a boundary, or `custom`.

## Which cloud?

| Cloud | Cheapest useful GPU | Cheapest per-hour spot | Managed K8s | Best for |
|---|---|---|---|---|
| AWS | g5.xlarge (A10G) | ~$0.30/hr | EKS | Most services + regions; the default choice |
| GCP | n1-standard-4 + T4 | ~$0.15/hr | GKE Autopilot | Cheapest GPU spot; simplest managed K8s |
| Azure | Standard_NC4as_T4_v3 | ~$0.20/hr | AKS | If you're already on Entra ID / Azure OpenAI |

For a solo researcher or small team, GCP tends to be the least expensive. For enterprises with existing AWS accounts, EKS is the path of least friction. For Microsoft-shop deployments, AKS integrates cleanly with Entra ID.

## Deployment tiers

Each of the per-cloud pages covers three tiers:

1. **Single VM** (docker compose on one box). Cheapest, no HA. Fine for a research group or a demo.
2. **Managed K8s** (EKS / GKE / AKS). Auto-scaling for GPU services; the engine + spine sit on cheap CPU nodes.
3. **Serverless spine + on-demand GPU** (Fargate / Cloud Run / Container Apps for the engine; GPU services burst from zero). Cheapest at low steady-state, most complex to configure.

## Pages

Full-stack reference deployments:
- [aws.md](./aws.md)
- [gcp.md](./gcp.md)
- [azure.md](./azure.md)

Rent-a-GPU: run a single GPU compute service on a serverless / on-demand GPU platform and point the engine at it (no full VM):
- [modal.md](./modal.md) — docking (`autodock-gpu`) on Modal's serverless GPUs; fits synchronous `dock_molecules`.
- [runpod.md](./runpod.md) — molecular dynamics (`gromacs-md`) on a Runpod GPU pod; fits async `run_molecular_dynamics`.

## What none of these deploy for you

- **Managed database**. Audit logs use the local file sink by default. If you want durable audit for compliance, add RDS Postgres (AWS), Cloud SQL (GCP), or Azure Database for PostgreSQL and set `AURORA_HOST` (env var name is legacy; any Postgres works).
- **Persistent config**. The engine keeps local config in plaintext files under `~/.novo/` (`credentials.env` at `0600`, `connectors.json`, `audit.jsonl`). Mount a persistent volume at that path, or these reset on every redeploy. The single-VM `docker compose` recipe already maps a named volume for it.
- **Custom domain + TLS**. Each recipe uses the load balancer's default DNS name. Add Route 53 / Cloud DNS / Azure DNS + ACM/Certificate Manager as needed.
- **Compute-service auto-scaling policy**. The K8s recipes ship scale-from-zero for GPU nodes but assume you'll tune the pod-autoscaler thresholds for your traffic.
- **Backup + disaster recovery**. Standard cloud best practices apply; not covered here.

If you need any of those managed, you're past the reference-deployment stage. Talk to a cloud consultant or your platform team.
