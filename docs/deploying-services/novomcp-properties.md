# novomcp-properties

Trained ML models for physicochemical properties: pKa, aqueous solubility, and bond dissociation energy (BDE). CPU-only. Model weights load from Hugging Face on first start — no cloud credentials required.

## Pre-reqs

- Docker
- ~4 GB RAM
- No GPU
- Network access on first boot (weights download from Hugging Face)
- For the charge-based pKa routes (sulfonamides / aromatic N–H): a running [novomcp-qm](./novomcp-qm.md) service supplying per-atom charges. Without it, those routes are unavailable and pKa is served by the general model only.

## Deploy

```bash
docker run -d \
  --name novomcp-properties \
  -p 8030:8030 \
  --restart unless-stopped \
  ghcr.io/novomcp/novomcp-properties:latest
```

## Wire into the engine

```bash
export NOVOMCP_PROPERTIES_URL=http://localhost:8030
export NOVOMCP_QM_URL=http://localhost:8031   # required for the charge-based pKa routes
```

## Verify

```bash
curl -s http://localhost:8030/health
# {"status":"healthy","service":"novomcp-properties","version":"1.0.0","port":8030,
#  "predictors":{"pka":{"backend":"rdkit-empirical","ready":false,"weights_loaded":false,"empirical_only":true},
#                "solubility":{"backend":"chemprop-aqsoldb","ready":true},
#                "bde":{"backend":"alfabet","ready":true}},
#  "ready":"2/3"}
# pka shows ready:false until you opt in to the NonCommercial weights (HF_PKA_MODEL_REPO); solubility + bde are ready.
```

## Tools that light up

- `predict_pka`, acidic/basic ionization constants
- `predict_solubility`, LogS (log molar aqueous solubility) with temperature dependence
- `predict_bde`, bond dissociation energies for radical chemistry

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8030` | HTTP listen port |
| `STORAGE_BACKEND` | `HF` | Weights backend: `HF` \| `LOCAL` \| `S3` |
| `HF_MODEL_REPO` | `NovoMCP/novomcp-properties` | Hugging Face weights repo (permissive: solubility) |
| `HF_PKA_MODEL_REPO` | – | NonCommercial pKa weights repo, opt-in (e.g. `NovoMCP/novomcp-pka`) |
| `NOVOMCP_QM_URL` | – | novomcp-qm endpoint for per-atom charges (charge-based pKa routes) |
| `BATCH_SIZE` | `64` | Molecules per batch |

## Notes

- **pKa weights are NonCommercial / ShareAlike.** The pKa model is trained primarily on the IUPAC Dissociation Constants (CC-BY-NC-4.0) plus ChEMBL (CC-BY-SA 3.0), so its weights ship separately under CC-BY-NC-SA-4.0 (`NovoMCP/novomcp-pka`) and are opt-in: set `HF_PKA_MODEL_REPO` for **non-commercial** use. Left unset, the pKa endpoints return `503` (solubility and BDE are unaffected). The service code is Apache-2.0; only the pKa weights carry the NonCommercial / ShareAlike terms.
- pKa model: a routed ensemble — a per-atom-charge specialist for sulfonamides / aromatic N–H, and a general model for everything else; each route reports an uncertainty estimate. Benchmarked on SAMPL7.
- Solubility model: pre-trained on AqSolDB, fine-tuned on BigSolDB with temperature as an input feature.
- BDE model: alfabet pretrained network.
- Solubility and BDE outputs are screening-grade ML predictions: dependable for ranking within a comparable series, not as absolute experimental values.
- If weights can't be loaded, the affected predictor reports unavailable and its endpoints return `503` rather than serving a silent fallback.
- All three are stateless, safe to scale horizontally behind a load balancer for high-throughput screening.
