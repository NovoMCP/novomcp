# Frozen-axis study — halogenation series (secondary chemotype)

Source-of-truth dataset for the halogenation secondary analysis discussed in
[issue #36](https://github.com/NovoMCP/novomcp/issues/36). These are the exact
series the frozen-axis classifier
([`trajectory_diagnostic.py`](../trajectory_diagnostic.py)) was run over, exported
so the rail-conditional can be reproduced against the *same* frozen set rather than
a re-derivation.

## `halo_series_cids.csv`

One row per series member, ordered by halogen count.

| column | meaning |
|---|---|
| `series_id` | stable id `S0000`…`S0467` for the series |
| `element` | the single halogen type varied across the series (`F`, `Cl`, `Br`) |
| `parent_smiles` | the de-halogenated parent, RDKit-canonical (see spec) |
| `halogen_count` | number of halogen atoms at this step |
| `cid` | representative PubChem CID for `(parent, count)` |

### Series definition

A series is a run of compounds that differ only by successive addition of one
halogen atom of a single type onto a common parent:

- **single-halogen-type** — a member contains exactly one halogen element (no mixed F/Cl, etc.).
- **parent-canonical** — the parent is obtained by replacing every halogen with H,
  sanitizing, and taking the RDKit canonical SMILES (`MolToSmiles(RemoveHs(...))`).
  This is deliberately **not** a Murcko scaffold: Murcko collapses the acyclic
  perfluoroalkyl chains into one bucket, which would make "frozen" indistinguishable
  from "a different series."
- **formula-successor** — members are grouped by `(element, parent)` and one
  representative is kept per halogen count.
- **n ≥ 10** — only the longest consecutive halogen-count run of length ≥ 10 is kept.

### Counts (reproduction check)

- **468** series — **F = 422 / Cl = 33 / Br = 13**
- **5,978** member molecules

### Caveat: representative CID

Each row is one representative CID per `(parent, halogen_count)` level; positional
isomers at a given count collapse to that level and the representative is the first
one encountered in the corpus scan (not a deterministic pick). A deterministic
selection (e.g. min CID per level) can be substituted if a byte-reproducible set is
needed; it is not expected to change the rail-conditional.

---

## Validation result — rail separation is unresolved at the available sample size

The narrative and the transferable lesson are in the engineering story
[*Configuration can produce a confident wrong answer*](../../../../../docs/engineering-stories/configuration-is-part-of-the-measurement.md).
This is the specific result, kept with the tool it validates.

`FROZEN` can be hollow in one specific way: an ADMET probability pinned at 0 or 1
cannot move, so "it did not move in the held-out tail" is guaranteed rather than
informative. Under perfluorination this is a live concern, because exhaustively
halogenated small molecules are exactly the input that saturates a probability.
The pre-registered rail baseline exists to test it.

Conditioning on non-rail frozen axes leaves the effect essentially intact but does
not clear its null:

| cell | n | Δ | null | perms ≥ obs |
|---|---:|---:|---|---:|
| all frozen | 250 | +0.320 | +0.092 ± 0.117 | 8/500 |
| minus rail-pinned | 192 | +0.313 | +0.197 ± 0.094 | 46/500 |
| minus rail + disconnected | 134 | +0.274 | +0.218 ± 0.117 | 162/500 |
| minus rail + disc. + exhausted | 71 | +0.246 | +0.132 ± 0.137 | 100/500 |

Read the effect column and the null column separately; they say different things.
The effect barely moves across three strippings — +0.320 → +0.246, a 23% decline.
Were `FROZEN` merely rail-detection, removing the rail axes would collapse it; it
drops 2%. What changes is the noise floor: the step-order null more than doubles once
rails are excluded (+0.092 → +0.197). Significance is lost because the null rises to
meet a roughly fixed effect, not because the effect disappears.

Two candidate explanations were tested and rejected. **Multi-fragment parents** —
110 of 422 F-series are mixture or salt records scored as single compounds — cost
some effect but do not account for it. **Chemical exhaustion** — 43% of series run
until every hydrogen is substituted — is a non-factor: exhausted series give +0.259,
non-exhausted +0.246.

The corpus cannot answer the question. Reaching significance on the non-rail
conditional needs roughly twice the series, and they do not exist: mining the full
corpus for clean single-halogen-type series at n ≥ 10 yields 468, of which only ~46
are the Cl/Br-on-a-ring halogenation a medicinal chemist would recognise. The
sparsity that forced this study onto the perfluorination chemotype is the same wall
that limits its power.

So caveat 3 is a limitation of the data, not a weakness of the label. `FROZEN` is not
reducible to rail-detection here — and it is also not demonstrably separable from it.
Both readings are unsupported by this sample.

### Objections raised and resolved

Two objections were raised during review and resolved against the data. They are
recorded here rather than smoothed out, because a rejected explanation is part of
why the result is trustworthy.

- **CF₂-homologation.** That these series measure chain elongation (CF₂
  homologation) rather than perfluorination of a fixed skeleton. Falsified by the
  export itself: `parent_smiles` is constant across all 468 series, so carbon count
  cannot grow within a series — every step adds one halogen to the same skeleton.
- **Chemical exhaustion.** That series running out of substitutable hydrogens (43%
  do) drive the effect, the plateau being the ladder ending rather than the property
  saturating. Rejected by the data: exhausted series give +0.259, non-exhausted
  +0.246 — indistinguishable.

---

## Per-axis flat gate — resolution of the gate ([#58](https://github.com/NovoMCP/novomcp/issues/58))

The single absolute `flat_abs = 0.10` gated each endpoint by a different number of SDs
(0.0053 SD on `clearance_hepatocyte`, ~0.9 SD on `cyp2d6`). It is replaced by a per-axis
gate, `flat_abs[axis] = k_gate · corpus_SD[axis]` with **k_gate = 0.5**, so "did it move"
means 0.5 corpus-SD on every axis. SDs are measured on the full 122M corpus; gate and effect
size are both denominated in those units.

**Why per-axis (the barred-axes result, replicated on both chemotypes).** Under the absolute
gate, wide-SD endpoints were *structurally barred from ever being flat* — flat rate exactly
0.000 for `aqueous_solubility_log_mol_l` on the halogenation secondary, and for
`clearance_hepatocyte`, `aqueous_solubility_log_mol_l` and `ld50_log_mol_kg` on the
homologation primary. The per-axis gate unbars them (aqsol 0.000 → ~0.03–0.08; clearance
0.000 → 0.015) and pulls `cyp2d6` off its ceiling. Same artifact, two independent rosters and
chemotypes — that, not the effect size, is the case for the change, and it holds at any k_gate.

**Why k_gate = 0.5, on non-outcome grounds.** Neither dataset's effect size could pin it: the
underpowered secondary binds on noise (z ~2 across the viable range), the powered primary
saturates (0/500 at every k_gate). So k_gate is chosen deliberately, not from the effect —
0.5 is the secondary's best population match (268 vs 257 frozen), effectively tied on the
primary (baseline 34,678 between 0.5's 33,983 and 0.4's 35,370), unbars the barred axes more
than 0.4, and returns the median probability axis to ~0.10 so axes the absolute gate already
handled keep their behavior. The rejected alternative — minimising per-axis flat-rate spread —
is a dead end (it is confounded by the flat level, and equal flat rates are not even desirable:
axes *should* respond differently to a modification).

**B2 retired.** The pre-registered flat-prefix baseline (B2) thresholded pre-window range at a
fixed 0.25 SD; the per-axis gate at k_gate ≥ 0.25 is strictly stricter and removes the
always-flat stratum before FROZEN is considered, so B2 becomes a no-op (identical n, Δ, z to
the unstratified rows). The same gate earlier *governed whether B2 could function at all*; its
function has now moved into the gate. B2 is stood down deliberately and recorded here — not
dropped silently — as a pre-registered frame and the guard that caught the band-truncated dry
run.

**Restated figure.** The headline moves with the configuration (the study's own lesson):
+0.153 SD (single absolute gate) → **+0.1352 SD, z ≈ 14.6** (per-axis, k_gate 0.5), still
0/500. Carried with its configuration named on every surface.

**Footnote — corpus vs sample units.** Gating on the 122M SD equalises the gate in *corpus*
units. In the primary's 8-shard subset, per-axis dispersion runs 0.74–1.03 of the corpus SD,
so in that sample's own units the effective gate still varies ~1.4× — down from 192× under the
absolute gate. "Equalised by construction" is exact only in corpus units.

> Co-authored with Dal Marsters ([@dmarsters](https://github.com/dmarsters)); the
> analysis machinery (`corpus_stream_mine`, `corpus_merge_series`, `exp20`, `exp20d`) lives in
> the [companion research repository](https://github.com/dmarsters/frozen-validation),
> which cross-links back here.
