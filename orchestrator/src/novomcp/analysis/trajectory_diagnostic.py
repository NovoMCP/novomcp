"""
trajectory_diagnostic.py — decompose an optimization SERIES into per-axis structure.

Property predictors (ADMET, descriptors, docking scores) score ONE molecule at a time. But a
lead-optimization campaign is a SERIES: you make a repeating modification — add a CH2, a halogen,
an N-methyl — and watch a whole property vector move. This module reads the TRAJECTORY and tells
you, for each property axis, whether ALONG THIS modification it is:

  FROZEN     — moved early then plateaued: structurally saturated. You CANNOT tune it further by
               continuing this modification (a dead-end liability). VALIDATED: on 54,312 real
               CH2-homologous series from the open corpus, axes labelled FROZEN on a 60% prefix
               moved measurably less over the held-out tail than climbing/descending/complex ones
               — +0.153 SD beyond a step-order null that no permutation in 500 reached (z=17.0).
               Measured on the production (raw-input) path this module actually runs; an earlier
               figure (+0.108 SD, z=12.4) was computed on standardised input — a path no caller
               performs — and understated the effect. The effect survives a flat-prefix baseline,
               so it is not merely "the prefix barely moved". Quote the excess-over-null (+0.153),
               not the unadjusted contrast (+0.254): the null is not zero, because FROZEN
               preferentially selects low-variance axes. See issue #36.
  CLIMBING   — monotone up with the modification: you are actively DRIVING it.
  DESCENDING — monotone down.
  CLIFF      — a single-step threshold jump: a discontinuity to watch.
  FLAT       — never moves: this modification is irrelevant to this property.
  COMPLEX    — non-monotone; none of the above.

The thing a per-molecule predictor cannot give you: which liabilities are dead-ends, which you are
worsening, and which you can ignore — ALONG A GIVEN OPTIMIZATION AXIS. The decomposition is
modification-SPECIFIC: homologation and halogenation lock and tune DIFFERENT axes, which is what
makes it useful and what shows it reads real structure rather than imposing a pattern.

Substrate-general: `values` is any [n_steps, n_axes] matrix over an ordered series. Deps: numpy +
scipy.stats.spearmanr. Pure, no I/O. Apache-2.0 (top-level tree; this module lives outside the
BSL-1.1 `mcp/` orchestration core).

--------------------------------------------------------------------------------------------------
ENDPOINT CONTRACT (what the analyzer assumes about its input)
--------------------------------------------------------------------------------------------------
`analyze_optimization_trajectory` assumes a FIXED endpoint set across the whole trajectory: `values`
is a COMPLETE, rectangular [n_steps, n_axes] matrix, column j is the SAME endpoint at every step,
labelled by `axis_names[j]`. It classifies each column independently; it never imputes. It raises
`ValueError` on a ragged/mismatched shape, on fewer than `MIN_STEPS` steps, or on any non-finite
(NaN/inf) entry — a missing prediction must be resolved BEFORE analysis, not silently interpolated.

Per-molecule predictors, however, can return a DIFFERENT set of endpoints per molecule (a head fails,
a model is absent for one input). `align_series` is the explicit bridge for that case: given one
{endpoint: value} record per step, it keeps an endpoint ONLY if it is present and numeric at EVERY
step, drops any endpoint missing at ANY step for the WHOLE trajectory, and returns the dropped names
so the caller can report them. That is the deliberate contract for "a missing endpoint mid-series":
the axis is dropped from the trajectory and named — never a ragged matrix, never a guessed value.

Example — wrapping a NovoMCP ADMET (addie-models) series:

    from novomcp.analysis.trajectory_diagnostic import align_series, analyze_optimization_trajectory

    preds = [row["predictions"] for row in addie_results]   # one {endpoint: value} dict per step
    values, axis_names, dropped = align_series(preds)        # drop endpoints not present every step
    if dropped:
        log.warning("endpoints dropped from trajectory (missing at >=1 step): %s", dropped)
    out = analyze_optimization_trajectory(values, axis_names)
    # out["summary"] -> {"frozen": [...], "climbing": ["cyp3a4_inhibitor_probability"], ...}
    # reading: chain elongation can't fix hepatotoxicity (frozen) but is driving CYP3A4 up (climbing).
"""
from dataclasses import dataclass, asdict

import numpy as np
# scipy is an optional dependency (Spearman rank correlation) — imported lazily in
# classify_axis so this module imports, and the engine boots, without it.
# Install it only to use the trajectory analysis: see analysis/requirements.txt.

# Minimum series length: FROZEN needs an early half AND a late move, so we need at least an early
# point, a mid point and an endpoint. Below this there is no trajectory to read.
MIN_STEPS = 3


@dataclass(frozen=True)
class Thresholds:
    """Every classification cutoff, surfaced as a parameter with a defaulted rationale so nobody
    has to reverse-engineer a magic number. Defaults are tuned for probabilities in [0, 1]; for
    endpoints in other units (e.g. logS in log mol/L, clearance in mL/min/kg) pass a Thresholds
    with `flat_abs` scaled to that axis — the rank/normalized cutoffs are unit-free and carry over."""

    flat_abs: float = 0.10
    # An axis is FLAT if its raw value range across the series is below this. 0.10 on a [0,1]
    # probability is within typical ADMET model noise, so a smaller swing is "did not move".

    mono_rho: float = 0.70
    # |Spearman rank correlation| at or above this counts the axis as monotone (CLIMBING/DESCENDING).
    # Rank-based, so it ignores step spacing and tolerates a local wiggle while still requiring a
    # clearly directional trend.

    freeze_early: float = 0.30
    # To be eligible for FROZEN an axis must first MOVE: its normalized range over the first half
    # of the series must be at least this. Below it the axis never really engaged (stays FLAT/COMPLEX).

    freeze_late_frac: float = 0.30
    # ...and then PLATEAU: late-half movement must be below this FRACTION of the early-half range.
    # 0.30 = "late motion is under a third of the early motion" = effectively settled.

    cliff_min_jump: float = 0.50
    # CLIFF = one dominant single-step jump. The largest normalized step must exceed this (half the
    # full normalized range in a single step) before a discontinuity is even considered.

    cliff_dominance: float = 2.0
    # ...AND that largest step must be at least this multiple of the second-largest step, so a smooth
    # ramp (many comparable steps) is NOT called a cliff — only a genuine single discontinuity is.

    def __post_init__(self):
        for k, v in asdict(self).items():
            if not (isinstance(v, (int, float)) and v > 0):
                raise ValueError(f"Thresholds.{k} must be a positive number, got {v!r}")


DEFAULT = Thresholds()


def classify_axis(v, positions, thresholds=DEFAULT):
    """Classify one property axis's behavior along the series.

    v          : raw values per step (length n_steps).
    positions  : x per step (real quantities — logP, #Cl — if the series is unevenly spaced).
    thresholds : a `Thresholds` instance (see its field docs for every cutoff's rationale).
    Returns {"class", "raw_range", "monotonicity", "early_range", "late_range", "late_move",
    "cliff_step"}. FROZEN is decided by `late_range` (both halves measured as ranges);
    `late_move` is the legacy endpoint delta, reported for back-compatibility only.
    """
    t = thresholds
    v = np.asarray(v, dtype=float)
    n = len(v)
    raw_range = float(v.max() - v.min())
    if raw_range < t.flat_abs:
        return {"class": "flat", "raw_range": round(raw_range, 3), "monotonicity": 0.0,
                "early_range": 0.0, "late_move": 0.0, "late_range": 0.0,
                "cliff_step": None}

    try:
        from scipy.stats import spearmanr
    except ImportError as e:  # optional dep — only the trajectory analysis needs it
        raise ImportError(
            "trajectory_diagnostic needs scipy. Install it with "
            "`pip install -r orchestrator/analysis/requirements.txt` (or `pip install scipy`)."
        ) from e

    vn = (v - v.min()) / raw_range                        # normalized shape in [0, 1]
    rho = float(spearmanr(positions, vn).correlation)
    half = n // 2
    early_range = float(vn[:half + 1].max() - vn[:half + 1].min())
    # Both halves must be measured the SAME way. Scoring the late half by its ENDPOINT difference
    # (abs(vn[-1] - vn[half])) reads a half that swings out and RETURNS as ~0 — "plateaued" —
    # however violently it moved. Against a step-order shuffle null on 538 real CH2-homologous
    # series that let FROZEN fire on 31.4% of pure noise; measuring a RANGE on both sides drops it
    # to 6.0%. `late_move` is retained in the output for back-compatibility, reported only.
    late_range = float(vn[half:].max() - vn[half:].min())
    late_move = float(abs(vn[-1] - vn[half]))             # endpoint delta, reported not used
    steps = np.abs(np.diff(vn))
    order = np.argsort(steps)
    biggest = float(steps[order[-1]])
    second = float(steps[order[-2]]) if len(steps) > 1 else 0.0
    cliff_step = int(order[-1]) + 1

    # Order matters: a CLIFF and a FROZEN-after-rise are BOTH monotone, so they must be ruled out
    # before the monotone climb/descend test, or they would be mislabeled "climbing".
    if biggest > t.cliff_min_jump and biggest > t.cliff_dominance * second:
        cls = "cliff"                                      # one dominant step = discontinuity
    elif early_range >= t.freeze_early and late_range < t.freeze_late_frac * early_range:
        cls = "frozen"                                     # moved early, plateaued late
    elif abs(rho) >= t.mono_rho:
        cls = "climbing" if rho > 0 else "descending"      # keeps moving, monotone
    else:
        cls = "complex"
    return {"class": cls, "raw_range": round(raw_range, 3), "monotonicity": round(rho, 2),
            "early_range": round(early_range, 2), "late_move": round(late_move, 2),
            "late_range": round(late_range, 2),
            "cliff_step": cliff_step if cls == "cliff" else None}


def analyze_optimization_trajectory(values, axis_names, positions=None, thresholds=DEFAULT):
    """
    values      : [n_steps, n_axes] — a property vector measured along an ordered series.
    axis_names  : length n_axes; labels the columns (the fixed endpoint set — see ENDPOINT CONTRACT).
    positions   : optional x per step (default 0..n-1); pass real quantities (logP, #Cl) if the
                  series is not evenly spaced.
    thresholds  : a `Thresholds` instance.
    Returns {"n_steps", "axes": {name: classification}, "summary": {class: [names]}, "thresholds": {...}}.
    Raises ValueError on a ragged shape, fewer than MIN_STEPS steps, a name/column mismatch, or any
    non-finite entry (see ENDPOINT CONTRACT — resolve missing values with align_series first).
    """
    values = np.asarray(values, dtype=float)
    if values.ndim != 2:
        raise ValueError(f"values must be 2-D [n_steps, n_axes], got shape {values.shape}")
    n_steps, n_axes = values.shape
    if n_steps < MIN_STEPS:
        raise ValueError(f"need at least {MIN_STEPS} steps to read a trajectory, got {n_steps}")
    if n_axes != len(axis_names):
        raise ValueError(f"axis_names has {len(axis_names)} entries but values has {n_axes} columns")
    if not np.isfinite(values).all():
        raise ValueError("values contains NaN/inf — a missing prediction must be resolved before "
                         "analysis (see align_series); the analyzer never imputes")
    if positions is None:
        positions = np.arange(n_steps)
    else:
        positions = np.asarray(positions, dtype=float)
        if positions.shape != (n_steps,):
            raise ValueError(f"positions must have length {n_steps}, got {positions.shape}")
        if not np.isfinite(positions).all():
            raise ValueError("positions contains NaN/inf")

    axes, summary = {}, {}
    for j, name in enumerate(axis_names):
        c = classify_axis(values[:, j], positions, thresholds)
        axes[name] = c
        summary.setdefault(c["class"], []).append(name)
    return {"n_steps": n_steps, "axes": axes, "summary": summary, "thresholds": asdict(thresholds)}


def align_series(step_records, *, require="all"):
    """Turn per-step {endpoint: value} records (what a per-molecule predictor returns) into a
    rectangular matrix for `analyze_optimization_trajectory`, applying the ENDPOINT CONTRACT.

    step_records : list of dicts, one per ordered step; keys are endpoint names, values numeric.
    require      : "all" (only supported) — keep an endpoint iff it is present AND numeric at EVERY
                   step; drop (for the whole trajectory) any endpoint missing/non-numeric at any step.
    Returns (values [n_steps, n_kept], kept_names (sorted), dropped_names (sorted)).
    Raises ValueError on fewer than MIN_STEPS records or if no endpoint survives.
    """
    if require != "all":
        raise ValueError("only require='all' is supported (the safe fixed-endpoint-set contract)")
    records = list(step_records)
    if len(records) < MIN_STEPS:
        raise ValueError(f"need at least {MIN_STEPS} step records, got {len(records)}")

    def numeric(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool) and np.isfinite(x)

    per_step_numeric = [{k for k, val in r.items() if numeric(val)} for r in records]
    kept = sorted(set.intersection(*per_step_numeric)) if per_step_numeric else []
    seen_anywhere = set().union(*per_step_numeric) if per_step_numeric else set()
    dropped = sorted(seen_anywhere - set(kept))
    if not kept:
        raise ValueError("no endpoint is present and numeric at every step; nothing to analyze")
    values = np.array([[float(r[k]) for k in kept] for r in records], dtype=float)
    return values, kept, dropped
