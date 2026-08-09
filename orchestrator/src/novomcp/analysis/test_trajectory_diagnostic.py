"""
Eval for trajectory_diagnostic — Ari's ask #3: the motifs and their expected decompositions
are CHECKED IN as assertions, not described in prose.

Two kinds of case:
  1. CANONICAL SYNTHETIC shapes — the six classes on hand-built series (ground truth by
     construction). These pin the classifier's core semantics.
  2. REAL ADMET MOTIFS — four repeating chemical modifications (homologation, chlorination,
     fluorination, polyol/hydroxylation), each a frozen matrix of addie-models predictions
     (addie 64-head panel, snapshot rev bb9a3cc3). We pin the DIAGNOSTIC's decomposition of
     these frozen inputs — a deterministic property of the pure function, independent of the
     model version. The test validates the trajectory read, NOT the model's chemical accuracy.
     The headline finding — different modifications lock and tune DIFFERENT axes — is asserted
     directly (test_modification_specificity), which is the whole reason the tool is useful.
"""
import numpy as np
import pytest

from novomcp.analysis.trajectory_diagnostic import (
    Thresholds, analyze_optimization_trajectory, align_series, classify_axis, MIN_STEPS,
)

# ---- shared ADMET axis set for the real motifs (order = fixture column order) ----
AXES = ["cyp3a4_inhibitor_probability", "hepatotoxicity_probability", "cardiotoxicity_10d_probability", "carcinogenicity_probability", "ames_mutagenicity_probability", "overall_toxicity_score", "herg_blocker_probability", "aqueous_solubility_log_mol_L"]

# ---- REAL addie-models predictions, frozen. Each row is one step in the modification. ----
# Generated from the live addie service; see scripts note in the PR. Values are the model's,
# rounded to 4 dp; the assertions below are on the DIAGNOSTIC applied to exactly these numbers.
FIXTURES = {
    "homologation": {
        "smiles": ["CCO", "CCCO", "CCCCO", "CCCCCO", "CCCCCCO", "CCCCCCCO", "CCCCCCCCO", "CCCCCCCCCO", "CCCCCCCCCCO", "CCCCCCCCCCCO", "CCCCCCCCCCCCO"],
        "values": [
            [0.0748, 0.2688, 0.3858, 0.7754, 0.132, 0.4633, 0.0861, 1.1946],
            [0.22, 0.8849, 0.1971, 0.3575, 0.1319, 0.5194, 0.0927, 0.8203],
            [0.2193, 0.0845, 0.2929, 0.8706, 0.1022, 0.5064, 0.1132, 0.2738],
            [0.1083, 0.0396, 0.6507, 0.1888, 0.0688, 0.4269, 0.1168, -0.3791],
            [0.1258, 0.0423, 0.7012, 0.071, 0.0599, 0.4058, 0.1258, -1.0859],
            [0.1794, 0.0423, 0.7012, 0.071, 0.053, 0.4067, 0.142, -1.4975],
            [0.3266, 0.0423, 0.7012, 0.071, 0.0518, 0.4068, 0.1444, -2.5051],
            [0.3907, 0.0423, 0.7012, 0.071, 0.0439, 0.4065, 0.1479, -2.9758],
            [0.344, 0.0423, 0.7012, 0.071, 0.0414, 0.407, 0.1561, -3.4924],
            [0.3547, 0.0423, 0.7012, 0.071, 0.0439, 0.4075, 0.158, -3.8967],
            [0.3114, 0.0423, 0.7012, 0.071, 0.0376, 0.4077, 0.1668, -4.4688],
        ],
        # expected class per axis (regression snapshot of the pure diagnostic):
        "expected": {"cyp3a4_inhibitor_probability": "climbing", "hepatotoxicity_probability": "frozen", "cardiotoxicity_10d_probability": "frozen", "carcinogenicity_probability": "frozen", "ames_mutagenicity_probability": "flat", "overall_toxicity_score": "frozen", "herg_blocker_probability": "flat", "aqueous_solubility_log_mol_L": "descending"},
    },
    "chlorination": {
        "smiles": ["c1ccccc1", "Clc1ccccc1", "Clc1ccccc1Cl", "Clc1ccc(Cl)cc1Cl", "Clc1cc(Cl)c(Cl)cc1Cl", "Clc1cc(Cl)c(Cl)c(Cl)c1Cl", "Clc1c(Cl)c(Cl)c(Cl)c(Cl)c1Cl"],
        "values": [
            [0.0261, 0.5284, 0.2535, 0.3017, 0.0618, 0.4537, 0.1134, -2.4381],
            [0.1127, 0.9364, 0.401, 0.4692, 0.1458, 0.5437, 0.1208, -2.2097],
            [0.2022, 0.9978, 0.6553, 0.0138, 0.1196, 0.5053, 0.1184, -2.9439],
            [0.295, 0.9952, 0.7535, 0.1031, 0.0919, 0.4925, 0.1288, -3.7321],
            [0.2757, 0.1927, 0.6239, 0.12, 0.0505, 0.4278, 0.1316, -5.0107],
            [0.1452, 0.9855, 0.857, 0.8954, 0.0542, 0.5986, 0.1326, -5.7251],
            [0.0596, 0.9893, 0.787, 0.9364, 0.048, 0.6042, 0.1385, -7.0196],
        ],
        # expected class per axis (regression snapshot of the pure diagnostic):
        # Two labels corrected when FROZEN began measuring both halves as ranges.
        # hepatotoxicity CRASHES 0.995 -> 0.193 and rebounds to 0.986 — the most violent
        # movement in the series — and was called "frozen" (i.e. "saturated, cannot be tuned
        # further this way") only because the final point lands near the midpoint value.
        # cardiotoxicity_10d rises 0.254 -> 0.787 and is still moving late; "climbing" fits.
        "expected": {"cyp3a4_inhibitor_probability": "complex", "hepatotoxicity_probability": "complex", "cardiotoxicity_10d_probability": "climbing", "carcinogenicity_probability": "complex", "ames_mutagenicity_probability": "flat", "overall_toxicity_score": "complex", "herg_blocker_probability": "flat", "aqueous_solubility_log_mol_L": "descending"},
    },
    "fluorination": {
        "smiles": ["c1ccccc1", "Fc1ccccc1", "Fc1ccccc1F", "Fc1cc(F)cc(F)c1", "Fc1cc(F)c(F)cc1F", "Fc1cc(F)c(F)c(F)c1F", "Fc1c(F)c(F)c(F)c(F)c1F"],
        "values": [
            [0.0261, 0.5284, 0.2535, 0.3017, 0.0618, 0.4537, 0.1134, -2.4381],
            [0.0491, 0.8926, 0.3149, 0.005, 0.3467, 0.5289, 0.1158, -1.4046],
            [0.0552, 0.9532, 0.126, 0.001, 0.2028, 0.447, 0.0766, -1.7333],
            [0.0917, 0.9873, 0.2973, 0.0515, 0.1299, 0.5205, 0.113, -2.3137],
            [0.0617, 0.9086, 0.2334, 0.0053, 0.1293, 0.4381, 0.1045, -2.5433],
            [0.0185, 0.9846, 0.1921, 0.038, 0.133, 0.4333, 0.1012, -2.8954],
            [0.0143, 0.983, 0.2603, 0.0417, 0.0891, 0.5065, 0.1049, -3.2011],
        ],
        # expected class per axis (regression snapshot of the pure diagnostic):
        # cardiotoxicity_10d was "frozen" until FROZEN measured both halves as ranges. Its
        # late half oscillates over the FULL normalized range (0.91, 0.57, 0.35, 0.71) and
        # only ends near the midpoint — a swing, not a plateau. "complex" is correct.
        "expected": {"cyp3a4_inhibitor_probability": "flat", "hepatotoxicity_probability": "cliff", "cardiotoxicity_10d_probability": "complex", "carcinogenicity_probability": "cliff", "ames_mutagenicity_probability": "frozen", "overall_toxicity_score": "flat", "herg_blocker_probability": "flat", "aqueous_solubility_log_mol_L": "descending"},
    },
    "polyol": {
        "smiles": ["CCO", "OCCO", "OCC(O)CO", "OCC(O)C(O)CO", "OCC(O)C(O)C(O)CO", "OCC(O)C(O)C(O)C(O)CO"],
        "values": [
            [0.0748, 0.2688, 0.3858, 0.7754, 0.132, 0.4633, 0.0861, 1.1946],
            [0.2148, 0.8951, 0.2289, 0.8039, 0.1635, 0.5447, 0.0726, 1.4194],
            [0.2544, 0.6632, 0.1202, 0.0028, 0.2023, 0.2286, 0.056, 1.0843],
            [0.0818, 0.1925, 0.1217, 0.003, 0.1287, 0.235, 0.0542, 0.5632],
            [0.0567, 0.3073, 0.205, 0.0009, 0.1049, 0.2575, 0.0655, 0.7481],
            [0.0508, 0.9055, 0.2958, 0.0002, 0.0874, 0.3107, 0.0705, 0.5849],
        ],
        # expected class per axis (regression snapshot of the pure diagnostic):
        "expected": {"cyp3a4_inhibitor_probability": "frozen", "hepatotoxicity_probability": "complex", "cardiotoxicity_10d_probability": "complex", "carcinogenicity_probability": "cliff", "ames_mutagenicity_probability": "descending", "overall_toxicity_score": "cliff", "herg_blocker_probability": "flat", "aqueous_solubility_log_mol_L": "frozen"},
    },
}


# ----------------------------------------------------------------------------------------------
# 1. CANONICAL SYNTHETIC SHAPES — ground truth by construction.
# ----------------------------------------------------------------------------------------------
def test_canonical_shapes():
    n = 11
    shapes = {
        "climbing":   0.1 + 0.08 * np.arange(n),
        "descending": 0.9 - 0.08 * np.arange(n),
        "frozen":     np.concatenate([np.linspace(0.1, 0.7, 5), np.full(6, 0.7)]),
        "cliff":      np.concatenate([np.full(7, 0.05), np.full(4, 0.9)]),
        "flat":       np.full(n, 0.4) + np.array([0, 1, -1, 1, -1, 0, 1, -1, 1, -1, 0]) * 0.005,
        "complex":    np.array([0.5, 0.2, 0.7, 0.3, 0.6, 0.25, 0.65, 0.35, 0.55, 0.3, 0.6]),
    }
    res = analyze_optimization_trajectory(np.column_stack(list(shapes.values())), list(shapes))
    got = {k: res["axes"][k]["class"] for k in shapes}
    assert got == {k: k for k in shapes}, got


def test_cliff_reports_its_step():
    # cliff between step index 6 and 7 (0-based) -> cliff_step == 7
    v = np.concatenate([np.full(7, 0.05), np.full(4, 0.9)])
    c = classify_axis(v, np.arange(len(v)))
    assert c["class"] == "cliff" and c["cliff_step"] == 7


def test_smooth_ramp_is_not_a_cliff():
    # a uniform ramp has no dominant single step -> climbing, never cliff
    v = np.linspace(0.0, 1.0, 11)
    assert classify_axis(v, np.arange(11))["class"] == "climbing"


# ----------------------------------------------------------------------------------------------
# 2. REAL ADMET MOTIFS — frozen predictions, pinned decompositions (Ari's ask #3).
# ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("motif", list(FIXTURES))
def test_real_motif_decomposition_snapshot(motif):
    fx = FIXTURES[motif]
    res = analyze_optimization_trajectory(np.array(fx["values"]), AXES)
    got = {ax: res["axes"][ax]["class"] for ax in AXES}
    assert got == fx["expected"], f"{motif}: {got}"


def test_real_motif_headlines():
    """The chemically meaningful, threshold-robust claims (not boundary calls)."""
    def classes(motif):
        res = analyze_optimization_trajectory(np.array(FIXTURES[motif]["values"]), AXES)
        return {ax: res["axes"][ax]["class"] for ax in AXES}

    homo, chlor, fluor, poly = (classes(m) for m in ("homologation", "chlorination", "fluorination", "polyol"))

    # Homologation drives lipophilicity: solubility falls hard, CYP3A4 inhibition climbs.
    assert homo["aqueous_solubility_log_mol_L"] == "descending"
    assert homo["cyp3a4_inhibitor_probability"] == "climbing"
    # Chlorination does NOT drive CYP3A4 the same way (modification-specific).
    assert chlor["cyp3a4_inhibitor_probability"] != "climbing"
    assert chlor["aqueous_solubility_log_mol_L"] == "descending"
    # Halogenation introduces discontinuities (structural-alert cliffs) homologation does not.
    assert "cliff" in set(fluor.values())
    assert "cliff" not in set(homo.values())
    # Polyols stay hydrophilic: adding OH does not send solubility into free-fall like adding C/Cl.
    assert poly["aqueous_solubility_log_mol_L"] != "descending"


def test_modification_specificity():
    """The load-bearing claim: different modifications lock/tune DIFFERENT axes. If homologation and
    chlorination produced the same decomposition the tool would be reading a universal artifact."""
    homo = FIXTURES["homologation"]["expected"]
    chlor = FIXTURES["chlorination"]["expected"]
    differing = [ax for ax in AXES if homo[ax] != chlor[ax]]
    assert homo != chlor
    assert len(differing) >= 3, differing


# ----------------------------------------------------------------------------------------------
# 3. THRESHOLDS ARE REAL PARAMETERS (Ari's ask #1) — an honest small-signal case.
# ----------------------------------------------------------------------------------------------
def test_flat_abs_threshold_flips_herg():
    """Along homologation, hERG blockade rises monotonically but only ~0.08 in absolute terms —
    below the default flat_abs=0.10 "did it move" floor, so it reads FLAT. Lower the floor to 0.05
    and the same data reads CLIMBING. The cutoff is a surfaced parameter, not a buried constant."""
    vals = np.array(FIXTURES["homologation"]["values"])
    at_default = analyze_optimization_trajectory(vals, AXES, thresholds=Thresholds(flat_abs=0.10))
    at_sensitive = analyze_optimization_trajectory(vals, AXES, thresholds=Thresholds(flat_abs=0.05))
    assert at_default["axes"]["herg_blocker_probability"]["class"] == "flat"
    assert at_sensitive["axes"]["herg_blocker_probability"]["class"] == "climbing"


def test_thresholds_reject_nonpositive():
    with pytest.raises(ValueError):
        Thresholds(mono_rho=-0.1)
    with pytest.raises(ValueError):
        Thresholds(flat_abs=0)


def test_result_echoes_thresholds():
    res = analyze_optimization_trajectory(np.array(FIXTURES["homologation"]["values"]), AXES)
    assert res["thresholds"]["flat_abs"] == 0.10 and res["thresholds"]["cliff_dominance"] == 2.0


# ----------------------------------------------------------------------------------------------
# 4. ENDPOINT CONTRACT (Ari's ask #2).
# ----------------------------------------------------------------------------------------------
def test_analyze_raises_on_nan():
    v = np.array(FIXTURES["chlorination"]["values"], dtype=float)
    v[2, 1] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        analyze_optimization_trajectory(v, AXES)


def test_analyze_raises_on_too_few_steps():
    with pytest.raises(ValueError, match="at least"):
        analyze_optimization_trajectory(np.zeros((MIN_STEPS - 1, 2)), ["a", "b"])


def test_analyze_raises_on_name_column_mismatch():
    with pytest.raises(ValueError, match="columns"):
        analyze_optimization_trajectory(np.zeros((5, 3)), ["a", "b"])


def test_analyze_raises_on_1d():
    with pytest.raises(ValueError, match="2-D"):
        analyze_optimization_trajectory(np.arange(5), ["a"])


def test_align_series_keeps_common_endpoints():
    recs = [
        {"a": 0.1, "b": 0.2, "c": 0.3},
        {"a": 0.2, "b": 0.3, "c": 0.4},
        {"a": 0.3, "b": 0.4, "c": 0.5},
    ]
    values, kept, dropped = align_series(recs)
    assert kept == ["a", "b", "c"] and dropped == []
    assert values.shape == (3, 3)


def test_align_series_drops_endpoint_missing_at_any_step():
    """A missing endpoint mid-series is dropped for the WHOLE trajectory and reported — never
    a ragged matrix, never an imputed value. This is the documented contract."""
    recs = [
        {"a": 0.1, "b": 0.2, "herg": 0.5},
        {"a": 0.2, "b": 0.3},                 # herg absent at this step
        {"a": 0.3, "b": 0.4, "herg": 0.6},
    ]
    values, kept, dropped = align_series(recs)
    assert kept == ["a", "b"] and dropped == ["herg"]
    assert values.shape == (3, 2)
    # the surviving matrix analyzes cleanly
    analyze_optimization_trajectory(values, kept)


def test_align_series_treats_nonfinite_as_missing():
    recs = [
        {"a": 0.1, "b": 0.2},
        {"a": float("nan"), "b": 0.3},
        {"a": 0.3, "b": 0.4},
    ]
    _, kept, dropped = align_series(recs)
    assert kept == ["b"] and dropped == ["a"]


def test_align_series_raises_when_nothing_survives():
    recs = [{"a": 0.1}, {"b": 0.2}, {"c": 0.3}]
    with pytest.raises(ValueError, match="every step"):
        align_series(recs)


# ----------------------------------------------------------------------------------------------
# GROUND-TRUTH: documented ADMET cliffs (issue #11).
# Frozen addie-models `ames_mutagenicity_probability` trajectories for homolog-lead-in →
# aromatic-amine series. Aromatic amines are a canonical Ames structural alert (Kazius 2005) and
# the amine members here are documented human carcinogens — so the discontinuity is real, not
# constructed. These pin the labeler against real, documented data and lock the validated boundary.
# ----------------------------------------------------------------------------------------------
def test_documented_cliff_4_aminobiphenyl():
    # biphenyl, 4-methyl-, 4-ethyl-, 4-fluoro- (all Ames-negative), then 4-AMINObiphenyl (a
    # documented human bladder carcinogen) introduced and held. Real addie ames, frozen. The amine
    # step dominates a clean benign lead-in, so the labeler correctly reads a cliff at that step.
    # The SMILES sit beside the values so the fixture can be RE-DERIVED against a live addie
    # rather than reconstructed from the prose above — verified 2026-08-09 to regenerate all six
    # values exactly on the 64-head panel.
    smiles = ["c1ccc(-c2ccccc2)cc1",        # biphenyl
              "Cc1ccc(-c2ccccc2)cc1",       # 4-methylbiphenyl
              "CCc1ccc(-c2ccccc2)cc1",      # 4-ethylbiphenyl
              "Fc1ccc(-c2ccccc2)cc1",       # 4-fluorobiphenyl
              "Nc1ccc(-c2ccccc2)cc1",       # 4-AMINObiphenyl — the alert
              "Cc1ccc(-c2ccc(N)cc2)cc1"]    # amine held, methyl added
    ames = [0.214, 0.257, 0.192, 0.362, 0.806, 0.815]
    assert len(smiles) == len(ames)         # keeps the pin load-bearing, not decorative
    c = classify_axis(np.array(ames), np.arange(len(ames)))
    assert c["class"] == "cliff"
    assert c["cliff_step"] == 4          # the step that introduces the amine alert


def test_documented_cliff_reads_climbing_when_model_baseline_noisy():
    # Same real cliff (naphthalene homologs → 2-NAPHTHYLAMINE, a documented human carcinogen), but
    # addie scores the benign fluoro analog high (0.53), so the amine jump is not >2× the next
    # step → the CONSERVATIVE cliff test declines and it reads 'climbing' (still directionally
    # correct). This is the validated boundary from issue #11: cliff reliability is bounded by
    # whether the model holds the non-alert analogs low, NOT by the threshold — a retune to force
    # this case would manufacture false cliffs elsewhere. Kept as a regression guard on that call.
    # SMILES pinned for the same reason as the biphenyl case above, and verified the same way.
    smiles = ["c1ccc2ccccc2c1",             # naphthalene
              "Cc1ccc2ccccc2c1",            # 2-methylnaphthalene
              "CCc1ccc2ccccc2c1",           # 2-ethylnaphthalene
              "Fc1ccc2ccccc2c1",            # 2-fluoronaphthalene — the high benign analog (0.53)
              "Nc1ccc2ccccc2c1",            # 2-NAPHTHYLAMINE — the alert
              "Cc1ccc2cc(N)ccc2c1"]         # amine held, methyl added
    ames = [0.295, 0.478, 0.327, 0.531, 0.847, 0.869]
    assert len(smiles) == len(ames)
    assert classify_axis(np.array(ames), np.arange(len(ames)))["class"] == "climbing"


# ----------------------------------------------------------------------------------------------
# FROZEN measures both halves the same way.
# `early_range` is a RANGE, so the late half must be one too. Scoring the late half by its
# endpoint difference read a half that swings out and RETURNS as "plateaued" however violently it
# moved. Against a step-order shuffle null on 538 real CH2-homologous series that let FROZEN fire
# on 31.4% of pure noise (6.0% once both halves are ranges). These pin the corrected rule.
# ----------------------------------------------------------------------------------------------
def test_frozen_rejects_a_late_half_that_swings_out_and_returns():
    # Traverses the entire normalized range in the late half and lands back on the midpoint. The
    # legacy endpoint delta still reads ~0 ("settled"); the range sees the full swing.
    v = np.array([0.05, 0.30, 0.55, 0.58, 0.05, 0.99, 0.58])
    c = classify_axis(v, np.arange(len(v)))
    assert c["late_move"] < 0.01
    assert c["late_range"] > 0.99
    assert c["class"] != "frozen"


def test_genuine_plateau_still_reads_frozen():
    # The behaviour the label is FOR: moves early, then actually settles. Must be unaffected.
    v = np.array([0.10, 0.35, 0.60, 0.62, 0.61, 0.62, 0.61])
    assert classify_axis(v, np.arange(len(v)))["class"] == "frozen"
