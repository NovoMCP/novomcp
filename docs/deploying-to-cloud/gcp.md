# Deploying to GCP

Three tiers, cheapest to most complex. GCP is usually the least expensive path for a small-team deployment thanks to per-second billing and cheap T4 GPUs.

## Tier 1: single Compute Engine VM with docker compose

### Prerequisites

- A GCP project with billing enabled
- The `gcloud` CLI installed and authenticated
- ~$50/month at `us-central1` on an `e2-standard-2` for the CPU-only stack

### Deploy

```bash
export PROJECT=your-project-id
export ZONE=us-central1-a

gcloud config set project "$PROJECT"

# Startup script installs docker + clones repo + boots
cat > /tmp/startup.sh <<'EOF'
#!/bin/bash
apt-get update
apt-get install -y docker.io docker-compose-plugin git
systemctl enable --now docker
usermod -aG docker $(logname 2>/dev/null || echo root)
cd /opt
git clone https://github.com/NovoMCP/novomcp.git
cd novomcp
docker compose up -d
EOF

gcloud compute instances create novomcp-engine \
  --zone="$ZONE" \
  --machine-type=e2-standard-2 \
  --image-family=debian-12 --image-project=debian-cloud \
  --metadata-from-file=startup-script=/tmp/startup.sh \
  --tags=novomcp-engine

# Allow inbound to port 8018
gcloud compute firewall-rules create novomcp-engine-8018 \
  --allow=tcp:8018 --target-tags=novomcp-engine
```

Wait a few minutes, then get the external IP:

```bash
gcloud compute instances describe novomcp-engine --zone="$ZONE" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

Hit `http://<external-ip>:8018/health`.

### Adding a GPU service

Recreate with a GPU attached (T4 is cheapest, works for docking + short MD + AIMNet2):

```bash
gcloud compute instances create novomcp-gpu \
  --zone="$ZONE" \
  --machine-type=n1-standard-4 \
  --accelerator=type=nvidia-tesla-t4,count=1 \
  --image-family=common-cu124-debian-11 --image-project=deeplearning-platform-release \
  --maintenance-policy=TERMINATE \
  --metadata="install-nvidia-driver=True" \
  --metadata-from-file=startup-script=/tmp/startup.sh \
  --tags=novomcp-engine
```

The Deep Learning VM image ships CUDA + NVIDIA drivers preinstalled. Uncomment the GPU service blocks in `docker-compose.yml` and `docker compose up -d` again.

### Cost estimate

- **CPU-only (`e2-standard-2`)**: ~$50/month on-demand, ~$20/month with sustained-use discount
- **T4 GPU (`n1-standard-4` + T4)**: ~$260/month on-demand, ~$110/month spot
- **A100 40GB (`a2-highgpu-1g`)**: ~$2600/month on-demand, ~$800/month spot

## Tier 2: GKE Autopilot with GPU node pool

GKE Autopilot removes node management. You pay per pod-second at CPU and per GPU-second at GPU.

### Deploy sketch

```bash
gcloud container clusters create-auto novomcp \
  --region=us-central1

# Deploy the engine
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata: { name: engine }
spec:
  replicas: 1
  selector: { matchLabels: { app: engine } }
  template:
    metadata: { labels: { app: engine } }
    spec:
      containers:
        - name: engine
          image: ghcr.io/novomcp/engine:latest
          ports: [{ containerPort: 8018 }]
          resources:
            requests: { cpu: "500m", memory: "1Gi" }
---
apiVersion: v1
kind: Service
metadata: { name: engine }
spec:
  type: LoadBalancer
  selector: { app: engine }
  ports: [{ port: 80, targetPort: 8018 }]
EOF
```

For GPU services, use pod-spec node selectors:

```yaml
spec:
  nodeSelector:
    cloud.google.com/gke-accelerator: nvidia-tesla-t4
  containers:
    - name: autodock
      image: ghcr.io/novomcp/autodock-gpu:latest
      resources:
        limits: { nvidia.com/gpu: "1" }
```

Autopilot spins up a GPU node when the pod is scheduled and tears it down when the pod exits or scales to zero.

### Cost estimate

- **Engine only (~1 vCPU steady)**: ~$30/month
- **Add T4 pool that runs 20 hrs/day**: ~$150/month

## Tier 3: Cloud Run for engine + on-demand GPU

Cloud Run charges per request-second. For a low-traffic engine, this can be under $10/month.

### High-level

- Deploy the engine to Cloud Run (auto-scaling, HTTPS-terminated at the front door)
- Compute services on Cloud Run for CPU (chem-props, addie-models, faves-compliance)
- GPU services on GKE Autopilot with pod-triggered scale-from-zero, or on Vertex AI custom endpoints

### Cost estimate

- **Steady-state (engine only, no calls)**: <$5/month (Cloud Run scales to zero)
- **Per docking call**: ~$0.02
- **Per MD simulation**: ~$0.30

Deployment is not scripted end-to-end here. Cloud Run's constraint is a 60-minute request timeout, which means MD simulations should return job IDs immediately (the engine already does this via the async job pattern).
