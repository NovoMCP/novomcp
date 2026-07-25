# analysis — reading molecule SERIES

NovoMCP's predictors score **one molecule at a time**. A lead-optimization campaign is a **series**:
you repeat a modification (add a CH₂, a halogen, an N-methyl) and watch a whole property vector move.
`trajectory_diagnostic` reads that trajectory and classifies each property axis along the modification:

| class | meaning | so what |
|---|---|---|
| `frozen` | moved early, then plateaued | structurally saturated — **can't** tune it further this way |
| `climbing` / `descending` | monotone with the modification | you're actively driving it |
| `cliff` | one dominant single-step jump | a discontinuity to watch |
| `flat` | never moves | this modification is irrelevant to this property |
| `complex` | non-monotone | none of the above |

The payoff a per-molecule predictor can't give: **which liabilities are dead-ends, which you're
worsening, and which you can ignore — along a given optimization axis.** The read is
modification-specific: on the four ADMET motifs in the test, homologation, chlorination, fluorination
and hydroxylation lock and tune *different* axes (that's `test_modification_specificity`).

## Install

`scipy` is the only extra dependency (Spearman rank correlation), and it's **not** part of the
engine core — the module imports it lazily, so the engine boots without it. Install it only to use
this analysis:

```bash
pip install -r orchestrator/analysis/requirements.txt   # or: pip install scipy
```

## Contract & knobs (the two things worth knowing before you trust it)

- **Every cutoff is a parameter.** `Thresholds` carries all six classification constants with a
  one-line rationale each and validates them; `analyze_optimization_trajectory` echoes them back in
  its result. Defaults suit probabilities in `[0,1]`; scale `flat_abs` for other units. See
  `test_flat_abs_threshold_flips_herg` for an honest case where the default `flat_abs` reads a small
  real hERG climb as `flat` and a lower cutoff reads it as `climbing`.
- **Endpoint contract.** The analyzer assumes a **fixed endpoint set** across the trajectory — a
  complete rectangular matrix — and **never imputes**: it raises on a ragged/short/NaN input.
  Per-molecule predictors return a *different* endpoint set per molecule, so `align_series` is the
  bridge: it keeps an endpoint only if present-and-numeric at **every** step, drops any missing at
  **any** step for the whole trajectory, and **returns the dropped names** so you can log them.

## Tests

```
cd orchestrator && python -m pytest analysis/test_trajectory_diagnostic.py
```

Covers the six canonical shapes (ground truth by construction), the four real ADMET motifs with
their decompositions pinned as a regression snapshot, the modification-specificity claim, the
threshold parameterization, and the endpoint contract. The motif fixtures are **frozen addie-models
predictions** (64-head panel), so the test pins the *diagnostic*, not the model version.

## Exposing it as an MCP tool (proposed — placement left to maintainers)

Kept out of `mcp/tools.py` deliberately: credits, funnel-id logging and dispatch are the
orchestration core's call, not something to guess at from outside. The wiring is thin — build the
matrix from an existing ADMET path, then analyze:

```python
# executor sketch for a tool like `analyze_admet_trajectory(smiles_series, endpoints=None)`
from analysis.trajectory_diagnostic import align_series, analyze_optimization_trajectory

async def _run(smiles_series, endpoints=None):
    # reuse whatever per-molecule ADMET path the tool layer already uses (addie-models):
    preds = [await predict_admet(s, endpoints=endpoints) for s in smiles_series]   # one dict/step
    values, axis_names, dropped = align_series(preds)     # fixed-endpoint contract, drops+reports
    out = analyze_optimization_trajectory(values, axis_names)
    out["dropped_endpoints"] = dropped
    return out
```

`positions=` accepts a real quantity (logP, #Cl, carbon count) when the series isn't evenly spaced.
