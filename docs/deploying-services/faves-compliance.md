# faves-compliance

Regulatory-compliance screening. Structural alerts (1,585 SMARTS via RDKit FilterCatalog), BOILED-Egg permeability classification, controlled-substance scaffold detection, and prior-art disclosure via InChIKey lookup.

The framework is described in the FAVES V4 paper (ChemRxiv). This container ships the reference implementation.

## Pre-reqs

- Docker
- ~2 GB RAM
- No GPU
- Optional: Redis instance for InChIKey cache

## Deploy

```bash
docker run -d \
  --name novomcp-faves \
  -p 8004:8004 \
  --restart unless-stopped \
  ghcr.io/novomcp/faves-compliance:latest
```

## Wire into the engine

```bash
export NOVOMCP_COMPLIANCE_URL=http://localhost:8004
```

## Verify

```bash
curl -s http://localhost:8004/health
# {"status":"healthy","rdkit_version":"2024.9.4","smarts_alerts":1585}

curl -s -X POST http://localhost:8004/api/classify \
  -H 'Content-Type: application/json' \
  -d '{"smiles":"CC(=O)Oc1ccccc1C(=O)O"}'
```

## Tools that light up

- `check_compliance`, full compliance profile: alerts, BOILED-Egg, PK class, prior-art status
- `get_molecule_profile`, fills the `compliance` section (returns empty when unwired)
- `screen_library`, bulk compliance filter for candidate lists

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8004` | HTTP listen port |
| `REDIS_URL` | (unset) | Optional Redis for InChIKey lookup cache |
| `SMARTS_CATALOG` | `rdkit-default` | Alternative catalogs: `rdkit-default`, `pains-a`, `pains-b`, `pains-c`, or a filesystem path |

## What FAVES-compliance is not

This ships the **open reference implementation** of the FAVES framework. It provides:
- Structural-alert flagging (RDKit-standard catalog)
- BOILED-Egg permeability classification
- Scaffold-based controlled-substance detection
- Basic prior-art disclosure checks

It does **not** ship:
- A curated regulatory corpus beyond the RDKit-default alert set
- A maintained-current SMARTS ruleset that tracks regulatory changes
- A certified/indemnified hosted service (21 CFR Part 11-aligned, audit-backed)

Those are operational commitments handled by the hosted FAVES service. If you need certified compliance for a regulated submission, run the reference implementation locally for triage and route the shortlist through the hosted service for the record-of-audit call.

## Notes

- Stateless; scale horizontally.
- The RDKit FilterCatalog is deterministic, same SMILES always produces the same alert set for a given catalog.
- Prior-art lookup requires an internet connection when the local cache misses.
