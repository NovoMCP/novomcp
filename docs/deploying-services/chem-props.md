# chem-props

CPU-only molecular property calculator. RDKit-based descriptors, Morgan fingerprint similarity, BOILED-Egg permeability, and 2D-descriptor computation.

## Pre-reqs

- Docker (any OS)
- ~200 MB RAM
- No GPU

## Deploy

**Via docker-compose (default):**

Uncomment the `chem-props` block in `docker-compose.yml`, then:
```bash
docker compose up chem-props
```

**Standalone:**

```bash
docker run -d \
  --name novomcp-chem-props \
  -p 8003:8003 \
  --restart unless-stopped \
  ghcr.io/novomcp/chem-props:latest
```

## Wire into the engine

```bash
export CHEM_PROPS_URL=http://localhost:8003
```

Or in `docker-compose.yml`, uncomment `CHEM_PROPS_URL` under `engine.environment`.

## Verify

```bash
curl -s http://localhost:8003/health
# {"status":"healthy","service":"chem-props"}

curl -s -X POST http://localhost:8003/api/v1/calculate \
  -H 'Content-Type: application/json' \
  -d '{"smiles":"CC(=O)Oc1ccccc1C(=O)O"}'
# {molecular_weight, logp, tpsa, ... }
```

## Tools that light up

- `calculate_properties`
- `get_molecule_profile` (partial, needs `addie-models` for full ADMET)
- `search_similar`
- Property fields inside `check_compliance` outputs

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8003` | HTTP listen port |
| `WORKERS` | `2` | Uvicorn workers |
| `LOG_LEVEL` | `info` | Log verbosity |
