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

> Co-authored with Dal Marsters ([@dmarsters](https://github.com/dmarsters)); the
> analysis machinery (`corpus_stream_mine`, `corpus_merge_series`, `exp20`) lives in
> the companion research repository and cross-links back here.
