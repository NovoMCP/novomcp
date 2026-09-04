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
| `omics_perturbation` (Perturb-seq signature reversal) | see the per-row **`license_tag`** column | ⚠ **verify before shipping** |

**⚠ `omics_perturbation` license — open item.** This table carries a per-row
`license_tag` (the pipeline tracked source licensing per signature). Its
aggregate redistribution terms are **not yet confirmed**. Before publishing
omics-core, either (a) confirm every `license_tag` is permissive/attribution and
list the sources here, or (b) filter the export to permissive rows, or (c) hold
`omics_perturbation` out of the first omics-core release (`target_discovery`
degrades gracefully — the perturbation channel is additive to the omics score).

**Not included:** any compliance columns, controlled-substance flags, or
internal fields — only the columns the OSS engine reads are exported.
