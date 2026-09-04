# Omics data pack

Ships the omics evidence layer as portable SQLite so `target_discovery`,
`validate_target`, and `stratify_patients` work with **no database server** —
install a pack and the engine routes `omics.*` reads to it (see
`orchestrator/src/novomcp/core/db_helper.py`).

Two license-distinct packs:

| Pack | Tables | Powers | License |
|---|---|---|---|
| **omics-core** | `omics_targets`, `omics_perturbation`, `omics_resistance` | `target_discovery`, `validate_target` | CC-BY-4.0 ([NOTICE](NOTICE-omics-core.md)) |
| **omics-pgx** | `omics_pgx` | `stratify_patients` (PGx layer) | CC-BY-SA-4.0 + ODbL ([NOTICE](NOTICE-omics-pgx.md)) — opt-in |

The core pack is enough for target discovery; the PGx pack is separate because
its sources (PharmGKB, gnomAD) impose ShareAlike terms.

## Install

```bash
# core only (target discovery):
python install_omics_pack.py omics-core.sqlite.gz
# add the PGx layer (patient stratification):
python install_omics_pack.py omics-core.sqlite.gz omics-pgx.sqlite.gz
```

Merges into `~/.novo/omics/omics.db` (override with `NOVOMCP_OMICS_DB`). The
engine's omics tools light up automatically once a pack is present — no env flag,
no restart of anything but the engine.

## Build the packs (maintainers)

Export from a Postgres holding the `omics` schema (Aurora or self-hosted). Only
the columns the engine reads are exported; Postgres array/JSONB columns are
serialized to JSON text so the engine decodes them back to the same shape.

```bash
NOVOMCP_DB_HOST=... DB_PASSWORD=... python export_omics.py --out ./dist --gzip
# -> dist/omics-core.sqlite(.gz), dist/omics-pgx.sqlite(.gz)
```

The packs are distributed as release assets / dataset downloads, **not committed
to this Apache-2.0 repo** — omics-pgx's CC-BY-SA/ODbL terms must not mix into the
code license.

## Open items

- **`omics_perturbation` license** — carries a per-row `license_tag`; confirm
  aggregate terms (or filter/hold) before publishing omics-core. See its NOTICE.
- **gnomAD/ODbL** — if population frequencies can be recomputed from CPIC (CC0),
  the PGx pack drops to a single ShareAlike license.
