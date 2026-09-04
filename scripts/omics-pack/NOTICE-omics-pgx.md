# omics-pgx data pack — sources & attribution

The **omics-pgx** pack (`omics-pgx.sqlite`) powers the pharmacogenomics layer of
`stratify_patients` (metabolizer phenotypes + population coverage). It is a
**separate, opt-in pack** because it carries ShareAlike terms its sources impose.

**Pack license: CC-BY-SA-4.0** (PharmGKB's ShareAlike governs the combined work),
**plus ODbL** obligations for the gnomAD-derived population frequencies. If you
redistribute this pack or a derivative, you must keep it under these same terms
and retain this attribution — you cannot relicense it as permissive/Apache.

| Column(s) | Source | License |
|---|---|---|
| `cpic_level`, `gene_function`, `clinical_implications`, `key_alleles` | **CPIC** | CC0 |
| drug–gene relationships, clinical annotations | **PharmGKB** | **CC-BY-SA-4.0** (ShareAlike) |
| `population_frequencies`, `variant_count_gnomad` | **gnomAD v4** | **ODbL** (ShareAlike for databases) |

**Why it's split out:** the engine is Apache-2.0 and omics-core is CC-BY, but
PharmGKB (CC-BY-SA) and gnomAD (ODbL) require ShareAlike. Keeping the PGx layer
in its own pack isolates those copyleft terms from the permissive core, so
installing only omics-core keeps everything attribution-only. `stratify_patients`
runs without this pack — its PGx lookup simply returns empty.

**Open cleanup (from scoping):** if the gnomAD population frequencies can be
recomputed from **CPIC (CC0)** allele-frequency tables, the ODbL dependency
drops and this pack reduces to a single ShareAlike license (CC-BY-SA-4.0).
