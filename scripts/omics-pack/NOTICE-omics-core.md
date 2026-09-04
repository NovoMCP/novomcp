# omics-core data pack — sources & attribution

The **omics-core** pack (`omics-core.sqlite`) powers `target_discovery` and the
omics evidence stream in `validate_target`. It is a derived database aggregated
from the public sources below. Distributed under **CC-BY-4.0** (the most
restrictive attribution term among its permissive sources); you must retain this
attribution when you redistribute the pack or a derivative.

Every column traces to its source — the same provenance standard the NovoMCP
Open Corpus is built on.

| Table | Source | License |
|---|---|---|
| `omics_targets` (target–disease associations, scores, tractability) | **Open Targets Platform** | Apache-2.0 |
| `omics_targets` (UniProt/PDB cross-references) | **UniProt** | CC-BY-4.0 |
| `omics_targets` (pathway context) | **Reactome** | CC0 |
| `omics_resistance` (pathogenic/resistance variants) | **ClinVar** (NCBI) | Public domain |
| `omics_perturbation` (signature reversal: TCGA / GTEx / Expression Atlas / SRA) | per-row **`license_tag`**; permissive only (see below) | public / CC0 |

**`omics_perturbation` — allowlist-gated (NonCommercial excluded).** Per the
pipeline's `LICENSE_AUDIT.md`, the permissive sources are TCGA & GTEx
(public/open, summary-level only), Expression Atlas (**CC0**), and SRA/recount3
(open-access). **DisGeNET is CC-BY-NC-SA (NonCommercial)** and must **not** ship
in this commercial-OK pack. The exporter enforces this: `omics_perturbation` is
**skipped by default** and only rows whose `license_tag` is explicitly
allowlisted via `--perturbation-licenses` are exported, after the maintainer
inspects the printed tag distribution. `target_discovery` degrades gracefully if
the channel is omitted.

**Not included:** any compliance columns, controlled-substance flags, or
internal fields — only the columns the OSS engine reads are exported.
