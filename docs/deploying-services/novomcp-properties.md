# novomcp-properties

Trained ML models for physicochemical properties: pKa, aqueous solubility, and bond dissociation energy (BDE). CPU-only.

## Pre-reqs

- Docker
- ~4 GB RAM
- No GPU
- ~500 MB disk for weights (bundled)

## Deploy

```bash
docker run -d \
  --name novomcp-properties \
  -p 8036:8036 \
  --restart unless-stopped \
  ghcr.io/NovoMCP/novomcp-properties:latest
```

## Wire into the engine

```bash
export NOVOMCP_PROPERTIES_URL=http://localhost:8036
```

## Verify

```bash
curl -s http://localhost:8036/health
# {"status":"healthy","models":["pka","solubility","bde"]}
```

## Tools that light up

- `predict_pka`, acidic/basic ionization constants
- `predict_solubility`, LogS (log molar aqueous solubility) with temperature dependence
- `predict_bde`, bond dissociation energies for radical chemistry

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8036` | HTTP listen port |
| `BATCH_SIZE` | `64` | Molecules per batch |

## Notes

- pKa model: Chemprop trained on IUPAC dataset; benchmarked against SAMPL8.
- Solubility model: pre-trained on AqSolDB, fine-tuned on BigSolDB with temperature as an input feature.
- BDE model: alfabet pretrained network.
- All three are stateless, safe to scale horizontally behind a load balancer for high-throughput screening.
