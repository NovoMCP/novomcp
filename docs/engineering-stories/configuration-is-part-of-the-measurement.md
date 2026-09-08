# Configuration is part of the measurement

*A trajectory diagnostic was validated on the production path. The three findings that carried the most weight were about how the measurement was configured, not about what it measured.*

**Draft:** August 2026
**Author:** NovoMCP engineering, with Dal Marsters ([@dmarsters](https://github.com/dmarsters))

---

`analyze_admet_trajectory` reads an ADMET property across an ordered series of molecules: a homologous series, a synthetic route, analogs from one repeating modification. It classifies how each endpoint moves. The label that carries the most weight is `FROZEN`: an endpoint that moved early and then plateaued. `FROZEN` states that a liability is saturated and cannot be tuned further by continuing this modification. A single-molecule prediction cannot make that claim. A series read can.

The claim runs against something that runs. The test was pre-registered ([issue #36](https://github.com/NovoMCP/novomcp/issues/36)) before any result was in: an axis labelled `FROZEN` on a prefix moves less over a held-out tail than one labelled climbing or descending. It does, and it holds on the path callers actually run.

That is the confirmation. The durable output is elsewhere. Three of the study's findings were about how the measurement was configured, not about `FROZEN` at all. Each one could have produced a confident wrong answer that passed every test aimed at the label. This piece names the standard those three findings establish, and the discipline that makes them visible.

---

## Three findings about the setup, not the label

### A storage layout truncates the measured tail

The corpus is partitioned by molecular weight. Homologation adds about 14 Da per step. A long series walks across band boundaries. A series that begins near a band edge runs off it and is cut short. The part that gets cut is the end of the series: the held-out tail the study measures.

Read band by band, the primary in its pre-registered B2 (moved-then-plateaued) frame reads **+0.016**, confidence interval spanning zero. That is indistinguishable from a detector that fires whenever the prefix happens to be quiet. The primary *contrast* band by band is +0.083, CI excluding zero. The pre-registered reporting frame reverses; the contrast does not. That is itself a small instance of the same standard. Pooled across bands, on the same series, the same code, the same thresholds, it reads **z = 12.4**. One filing decision produces opposite conclusions.

### An input convention doubles the population

The validation standardised each axis by its corpus standard deviation before classifying. The shipped tool passes raw values. That one difference moved the frozen population by a factor of two: **532 axes against 257**, on identical series.

The gap took two days to find, and the reason is the point. Both implementations were internally consistent. Both produced stable numbers. Neither had cause to suspect the other, because nothing was broken on either side. They were answering slightly different questions and getting correct answers to each.

### One threshold acts as two

Under both implementations sits a single constant: `flat_abs = 0.10`, the range an axis must exceed to be eligible for any label other than `flat`. It is compared against the raw range of every endpoint. On aqueous solubility (SD ≈ 0.87) and on cyp2d6 inhibition (SD ≈ 0.11), that is two thresholds under one name.

The effect runs past mis-scaling endpoints against each other. It decides whether one of the pre-registered baselines can run. On raw input the gate sends 129,970 pairs to `flat` before they can be considered frozen. The always-flat stratum the baseline depends on collapses from 1,854 to 45. The baseline degenerates to an identity: its moved-then-plateaued contrast becomes numerically equal to the overall contrast and carries no independent information. The finding arrived from two directions, the homologation primary and a halogenation secondary, and resolved into one only when the two label sets were reconciled against a fixed spec. It is tracked as [issue #58](https://github.com/NovoMCP/novomcp/issues/58).

---

## A series diagnostic carries more configuration surface than a scorer

A tool that scores one molecule has a small surface. One structure in, one number out. Most ways to get it wrong are bugs: things that are incorrect.

A tool that reads a series inherits a larger surface, and the dangerous part is configuration, not bugs. Configuration is decisions that are each defensible and together set the answer:

- how the data is partitioned: the storage layout truncated the tail;
- what units reach the classifier: raw versus standardised moved the population 2x;
- how a threshold defined in one endpoint's units behaves in another's: one constant, two effective gates.

None of these are claims about the phenomenon. Not one is a statement about whether `FROZEN` means anything. Each can invert the result. Each sits underneath the experiment, setting the answer, and none of them draws the scrutiny the experiment draws.

**The configuration surface is reviewed first, before the science.** A diagnostic that reads a series holds its wrong answers in the setup, not in the finding. The setup is where the interesting science is absent, which is exactly why it is where the wrong answers hide.

---

## What made them visible

Two of the three surfaced from process, not insight. The process is the standard.

**Aggregation is decided before the result is known.** The pre-registration ([#36](https://github.com/NovoMCP/novomcp/issues/36), posted publicly before any result was in) required pooling across weight bands before the answer was known. Written after the band-by-band nulls were seen, "analyse per band" would have looked like the conservative choice, and it would have buried the effect. Fixing the aggregation before the result is what kept the truncation from winning.

**Independent implementations are reconciled against a written spec, not against each other.** The raw/standardised gap surfaced only because two independent implementations were reconciled against a written spec. Two implementations agreeing tells you very little. These two agreed on direction throughout: both said `FROZEN` axes move less, while differing twofold on the population being measured. Agreement on the headline hid a disagreement about what was being counted. The written spec, an external artifact neither implementation could quietly conform to the other, is what made the gap visible. This is the same discipline as [spec is the source of truth](spec-source-of-truth.md), applied to a result instead of a threshold.

---

## The result

`FROZEN` validated. On the production path, a per-axis flat gate (`flat_abs = 0.5·corpus-SD` per endpoint, the fix for the third finding above), the effect is **+0.1352 SD** beyond a step-order null that no permutation in 500 reached (**z ≈ 14.6**), on 54,312 real homologous series.

The headline number moves with the configuration, and not on one axis but three. Standardised input reads +0.108 SD. Raw input with the absolute gate reads +0.153 on the study's first (8-shard) SD basis and +0.1491 once the SD basis is unified across the full corpus. The per-axis gate then brings it to +0.1352. The gate costs about −0.014 and the basis change about −0.004. Attributing both to the gate would be the exact error this piece warns against. That the validated figure moves with input, gate, and basis is the whole thesis, applied to its own result. It is quoted with all three named, never as a bare number.

One question the study could not settle: whether `FROZEN` is separable from trivial rail-detection. A probability pinned at 0 or 1 cannot move, so "it did not move" is guaranteed rather than informative. That is a limit of the data, not a weakness of the label. The corpus does not contain enough of the right series to reach significance on the conditional.

Fixing the gate produced one more configuration standard, this one about the statistic read while choosing the gate's parameter. Sweeping the gate, `perms ≥ obs` (the count of null permutations that beat the observed effect) was 0 of 500 at *every* setting on the 54,312-series dataset. That reads like the effect surviving any gate. It does not. A permutation count is censored at 1/N. The weakest configuration here sits at z = 10.28, p ≈ 4×10⁻²⁵, a number no 500-permutation budget can resolve. The statistic had saturated, not the effect. Read a graded statistic instead (z, or excess-over-null, both already in the same table) and the effect degrades smoothly as the gate worsens, in step across datasets that agree at k_gate 0.40 to a tenth of a percent. They coincide at 0.50 by construction; that is the normalisation point. **A permutation-count statistic is censored at 1/N_perm. Confirm it varies across a sweep before using it to select a parameter, and switch to a graded statistic when it does not.** It hides well, because `0/500` reads as strength, and a result that flatters the method draws less scrutiny than one that inconveniences it. That is exactly backwards.

The full analysis, with tables, nulls, and the two rejected explanations, lives with the validation record next to the tool ([`analysis/frozen_study/`](../../orchestrator/src/novomcp/analysis/frozen_study/)). The analysis machinery (`corpus_stream_mine`, `corpus_merge_series`, `exp20`) lives in the [companion research repository](https://github.com/dmarsters/frozen-validation): anonymous S3 corpus access, no credentials, `trajectory_diagnostic` deliberately not vendored so the validation cannot drift from the tool it validates. The two cross-link.

---

## Where else this holds

The standard applies to anything that reads a sequence rather than a point.

**Time-series monitoring.** A rule that reads a window inherits the window's boundaries, its resampling, its timezone. A latency regression that straddles a downsampling boundary vanishes the way these tails did, and "no alert fired" reads as "nothing happened."

**Cohort analytics.** A per-cohort metric depends on how cohorts are bucketed and on how a threshold set for one cohort's scale behaves on another's. Bucket by signup week under a fixed count threshold and it means one thing for a large cohort and another for a small one: the same one-constant-two-gates problem.

**Any normalise-then-classify pipeline.** The raw/standardised split generalizes. Every pipeline that standardises before a downstream step has a shadow copy that does not. Run validation on one and production on the other, and both can be internally correct and jointly wrong.

---

## The standard

Configuration is a hypothesis that does not announce itself. How the data is partitioned, what units reach the classifier, how a threshold scales across inputs: none of these read as experimental choices, and each can produce a confident wrong answer that survives every test aimed at the phenomenon.

The tests reach the configuration, not only the phenomenon. Decide the aggregation before the result, so the partition cannot be chosen to flatter it. Reconcile independent implementations against a written spec, not against each other, so a shared blind spot cannot ratify itself. Treat every constant that touches inputs of different scale as a claim to be checked on each scale, not a number that happened to work on the one it was tuned against.

Make the configuration loud and it can no longer lie quietly. That is the standard the rest of this series keeps arriving at, here applied one layer under the result, in the setup nobody thought to review.
