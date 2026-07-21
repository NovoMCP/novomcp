# Deploying to Azure

Three tiers, cheapest to most complex. Azure is the natural pick if you're already on Entra ID, Azure OpenAI, or an Azure-committed spend agreement.

## Tier 1: single Azure VM with docker compose

### Prerequisites

- An Azure subscription
- The `az` CLI installed and logged in (`az login`)
- ~$50/month at `eastus` on a `Standard_D2s_v5` for the CPU-only stack

### Deploy

```bash
export RG=novomcp-rg
export LOCATION=eastus

az group create --name "$RG" --location "$LOCATION"

# Cloud-init installs docker + clones + boots
cat > /tmp/cloud-init.yml <<'EOF'
#cloud-config
package_update: true
packages: [docker.io, git]
runcmd:
  - systemctl enable --now docker
  - curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64
      -o /usr/local/lib/docker/cli-plugins/docker-compose
  - chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  - cd /opt && git clone https://github.com/NovoMCP/novomcp.git
  - cd /opt/novomcp && docker compose up -d
EOF

az vm create \
  --resource-group "$RG" \
  --name novomcp-engine \
  --image Ubuntu2404 \
  --size Standard_D2s_v5 \
  --admin-username azureuser \
  --generate-ssh-keys \
  --custom-data /tmp/cloud-init.yml \
  --public-ip-sku Standard

# Open port 8018
az vm open-port --resource-group "$RG" --name novomcp-engine --port 8018
```

Get the public IP and hit `http://<ip>:8018/health`.

### Adding a GPU service

Recreate with a T4 (`Standard_NC4as_T4_v3`) or A100 (`Standard_NC24ads_A100_v4`) size. Use the Ubuntu HPC image for pre-installed NVIDIA drivers:

```bash
az vm create \
  --resource-group "$RG" \
  --name novomcp-gpu \
  --image "Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest" \
  --size Standard_NC4as_T4_v3 \
  --admin-username azureuser \
  --generate-ssh-keys \
  --custom-data /tmp/cloud-init.yml
```

You'll need to install the NVIDIA Container Toolkit inside the VM before the GPU service containers can see the T4. Add to `cloud-init.yml`:

```yaml
  - distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
  - curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey | apt-key add -
  - curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list
      | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  - apt-get update && apt-get install -y nvidia-container-toolkit
  - nvidia-ctk runtime configure --runtime=docker
  - systemctl restart docker
```

### Cost estimate

- **CPU-only (`Standard_D2s_v5`)**: ~$70/month
- **T4 (`Standard_NC4as_T4_v3`)**: ~$380/month on-demand, ~$130/month spot
- **A100 (`Standard_NC24ads_A100_v4`)**: ~$2400/month on-demand, ~$800/month spot

## Tier 2: AKS with GPU node pool

Managed Kubernetes on Azure. The engine + spine sit on CPU nodes; GPU services on a scale-from-zero GPU pool.

### Deploy sketch

```bash
az aks create \
  --resource-group "$RG" \
  --name novomcp-aks \
  --node-count 2 \
  --node-vm-size Standard_D2s_v5 \
  --enable-cluster-autoscaler \
  --min-count 2 --max-count 5 \
  --generate-ssh-keys

# Add a GPU node pool that scales from zero
az aks nodepool add \
  --resource-group "$RG" \
  --cluster-name novomcp-aks \
  --name gpupool \
  --node-count 0 \
  --min-count 0 --max-count 4 \
  --enable-cluster-autoscaler \
  --node-vm-size Standard_NC4as_T4_v3

az aks get-credentials --resource-group "$RG" --name novomcp-aks
```

Then apply the same K8s manifests as the AWS EKS recipe. GPU pods need `nodeSelector: { agentpool: gpupool }` and `resources.limits: { nvidia.com/gpu: "1" }`.

### Cost estimate

- **Baseline (CPU nodes + control plane)**: ~$140/month
- **Add T4 pool 20 hrs/day**: ~$260/month

## Tier 3: Container Apps + on-demand GPU VMs

Azure Container Apps is the serverless spine equivalent. Scales to zero, ~$0.024/vCPU-hour when active.

### High-level

- Engine on **Azure Container Apps** (serverless, HTTPS-terminated, custom domain)
- CPU compute services on **Container Apps** with min-replicas=0
- GPU services on **Container Instances** launched on-demand via a queue-triggered function

### Cost estimate

- **Engine idle**: ~$5/month (Container Apps scale to zero)
- **Per docking call**: ~$0.03 (T4 container instance for ~40 seconds)
- **Per MD simulation**: ~$0.40 (T4 for 10 min)

## Native Entra ID integration

If you're already on Entra ID (Azure AD), the engine's spine supports a custom `AuthGate` that validates Entra tokens. See `novomcp/mcp/spine.py` for the interface. Write an `EntraAuthGate` class that validates OIDC tokens against your tenant and set `NOVO_AUTH=custom`.
