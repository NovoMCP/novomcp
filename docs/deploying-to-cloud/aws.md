# Deploying to AWS

Three tiers, cheapest to most complex.

## Tier 1: single EC2 with docker compose

Fastest way to a running engine. One VM, one command.

### Prerequisites

- An AWS account with an IAM user or role you can `aws configure` as
- The AWS CLI installed locally
- ~$40/month at us-east-1 on a `t3.large` (2 vCPU, 8 GB RAM) for the CPU-only stack

### Deploy

```bash
# 1. Pick a region and AMI (Amazon Linux 2023, latest)
export REGION=us-east-1
export AMI=$(aws ssm get-parameter \
  --region "$REGION" \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query 'Parameter.Value' --output text)

# 2. Create a key pair (skip if you already have one)
aws ec2 create-key-pair --region "$REGION" --key-name novomcp-key \
  --query 'KeyMaterial' --output text > ~/.ssh/novomcp-key.pem
chmod 400 ~/.ssh/novomcp-key.pem

# 3. Create a security group allowing SSH + engine port
SG_ID=$(aws ec2 create-security-group --region "$REGION" \
  --group-name novomcp-sg --description "NovoMCP engine" \
  --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
  --protocol tcp --port 8018 --cidr 0.0.0.0/0

# 4. User-data script: install docker + clone repo + docker compose up
cat > /tmp/user-data.sh <<'EOF'
#!/bin/bash
dnf update -y
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user
# docker compose plugin
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
# clone + boot
cd /home/ec2-user
sudo -u ec2-user git clone https://github.com/NovoMCP/novomcp.git
cd novomcp
sudo -u ec2-user docker compose up -d
EOF

# 5. Launch
aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" \
  --instance-type t3.large \
  --key-name novomcp-key \
  --security-group-ids "$SG_ID" \
  --user-data file:///tmp/user-data.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=novomcp-engine}]'
```

Wait a few minutes for user-data to finish, then find your instance's public IP and hit `http://<public-ip>:8018/health`.

### Adding a GPU service

Swap `t3.large` for `g5.xlarge` (A10G, ~$1/hr on-demand or ~$0.30 spot). Add to the user-data script before `docker compose up -d`:

```bash
# NVIDIA Container Toolkit
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.repo | \
  tee /etc/yum.repos.d/nvidia-container-toolkit.repo
dnf install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker
```

Then uncomment the GPU service blocks (e.g. `autodock-gpu`) in `docker-compose.yml`.

### Cost estimate

- **CPU-only (`t3.large`)**: ~$60/month on-demand, ~$20/month spot
- **Single GPU (`g5.xlarge`)**: ~$730/month on-demand, ~$220/month spot
- Data transfer: usually negligible for API traffic

## Tier 2: EKS with GPU node group

For team deployment. Engine + spine on CPU nodes; GPU services on a scale-from-zero GPU node group. Requires familiarity with EKS.

### High-level

```
novomcp-eks (EKS cluster, us-east-1)
├── novomcp-svcs nodegroup (t3.large × 2, always-on)
│   ├── engine deployment
│   ├── chem-props
│   ├── addie-models
│   └── faves-compliance
└── novomcp-gpu nodegroup (g5.xlarge × 0-4, scale-from-zero)
    ├── autodock-gpu
    ├── gromacs-md
    └── openfold3
```

The Novo internal deployment used a similar shape. A reference `k8s/` manifest set is planned but not yet shipped in the this repository. In the meantime:

1. Create the cluster: [eksctl](https://eksctl.io) is the fastest path
2. Install the [AWS Load Balancer Controller](https://kubernetes-sigs.github.io/aws-load-balancer-controller/) for ALB ingress
3. Install [Cluster Autoscaler](https://github.com/kubernetes/autoscaler/tree/master/cluster-autoscaler) or Karpenter for GPU scale-from-zero
4. Deploy the engine as a `Deployment` with a `LoadBalancer` service on port 8018
5. Deploy each compute service the same way, expose via `ClusterIP`, wire engine env vars to `http://<service>.<namespace>.svc.cluster.local:<port>`

### Cost estimate

- **Baseline (CPU nodes + control plane)**: ~$150/month
- **Add GPU pool that runs 20 hrs/day**: ~$500/month (g5.xlarge spot)

## Tier 3: Fargate spine + on-demand GPU

Cheapest at low steady-state, most complex to configure.

### High-level

- Engine on **ECS Fargate** (no EC2 to manage, ~$0.04/hr for 1 vCPU / 2 GB)
- Compute services on **EC2 GPU instances** launched on-demand by SQS-triggered Lambda (or a queue-based autoscaler)
- Audit logs to S3 (via a custom `AuditSink` implementation)
- Aurora Serverless v2 if you want durable audit + credit accounting

### Cost estimate

- **Steady-state (engine only, no calls)**: ~$30/month
- **Per docking call**: ~$0.02 (30 seconds of a g5.xlarge spot)
- **Per MD simulation**: ~$0.30 (10 min on g5.xlarge spot)

This tier is not scripted end-to-end here. The engine's `Spine` interfaces (see `novomcp/mcp/spine.py`) are the extension points for a custom audit sink and metering backend.
