# Deploying to the cloud

Three reference deployments for a full NovoMCP stack on AWS, GCP, and Azure. Each is opinionated but minimal: single VM or small cluster, the engine + a handful of compute services, HTTPS, no managed database (audit logs stay on disk unless you swap in Postgres or DynamoDB).

These are reference recipes, not supported products. Copy, adapt, and own the resulting deployment.

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

- [aws.md](./aws.md)
- [gcp.md](./gcp.md)
- [azure.md](./azure.md)

## What none of these deploy for you

- **Managed database**. Audit logs use the local file sink by default. If you want durable audit for compliance, add RDS Postgres (AWS), Cloud SQL (GCP), or Azure Database for PostgreSQL and set `AURORA_HOST` (env var name is legacy; any Postgres works).
- **Custom domain + TLS**. Each recipe uses the load balancer's default DNS name. Add Route 53 / Cloud DNS / Azure DNS + ACM/Certificate Manager as needed.
- **Compute-service auto-scaling policy**. The K8s recipes ship scale-from-zero for GPU nodes but assume you'll tune the pod-autoscaler thresholds for your traffic.
- **Backup + disaster recovery**. Standard cloud best practices apply; not covered here.

If you need any of those managed, you're past the reference-deployment stage. Talk to a cloud consultant or your platform team.
