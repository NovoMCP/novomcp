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
