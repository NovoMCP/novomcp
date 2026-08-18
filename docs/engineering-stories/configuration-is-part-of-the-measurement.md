# Configuration can produce a confident wrong answer

*Validating one trajectory diagnostic: three of the findings that mattered most were about how the measurement was set up, not about what it measured.*

**Draft:** August 2026
**Author:** NovoMCP engineering, with Dal Marsters ([@dmarsters](https://github.com/dmarsters))

---

`analyze_admet_trajectory` reads an ADMET property across an ordered series of molecules — a homologous series, a synthetic route, analogs from one repeating modification — and classifies how each endpoint moves. The label that carries the most weight is `FROZEN`: an endpoint that moved early and then plateaued. It says a liability is saturated and cannot be tuned further by continuing this modification. A single-molecule prediction cannot make that claim; a series read can.

A claim like that has to be validated against something that runs, so we pre-registered the test ([issue #36](https://github.com/NovoMCP/novomcp/issues/36)): does an axis labelled `FROZEN` on a prefix move less over a held-out tail than one labelled climbing or descending? It does, and it holds on the path callers actually run.

That is not the part worth recording. Three of the study's findings were not about `FROZEN` at all. They were about how the measurement was configured, and each one could have produced a confident wrong answer that passed every test we had aimed at the label.

---

## Three findings about the setup, not the label

### A storage layout

The corpus is partitioned by molecular weight. Homologation adds about 14 Da per step, so a long series walks across band boundaries, and a series that begins near a band edge runs off it and is cut short. The part that gets cut is the end of the series — the held-out tail the study measures.

Analysed band by band, the primary — read in its pre-registered B2 (moved-then-plateaued) frame — reads **+0.016**, confidence interval spanning zero: indistinguishable from a detector that fires whenever the prefix happens to be quiet. (The primary *contrast* band by band is +0.083, CI excluding zero; it is the pre-registered reporting frame that reverses, not the contrast — itself a small instance of the same lesson.) Pooled across bands — same series, same code, same thresholds — it reads **z = 12.4**. Opposite conclusions from one filing decision.

### An input convention

The validation standardised each axis by its corpus standard deviation before classifying. The shipped tool does not; it passes raw values. That one difference moved the frozen population by a factor of two: **532 axes against 257**, on identical series.

It took two days to find. The reason is the useful part. Both implementations were internally consistent, both produced stable numbers, and neither had cause to suspect the other, because nothing was broken on either side. They were answering slightly different questions and getting correct answers to each.

### A threshold that was two thresholds

Under both sits a single constant: `flat_abs = 0.10`, the range an axis must exceed to be eligible for any label other than `flat`. It is compared against the raw range of every endpoint. On aqueous solubility (SD ≈ 0.87) and on cyp2d6 inhibition (SD ≈ 0.11), that is not one threshold. It is two, under one name.

It does more than mis-scale endpoints against each other. It decides whether one of the pre-registered baselines can run. On raw input the gate sends 129,970 pairs to `flat` before they can be considered frozen; the always-flat stratum the baseline depends on collapses from 1,854 to 45, and the baseline degenerates to an identity — its moved-then-plateaued contrast becomes numerically equal to the overall contrast and carries no independent information. We reached this from two directions, the homologation primary and a halogenation secondary, and did not recognise it as one finding until the two label sets were reconciled against a fixed spec. It is tracked as [issue #58](https://github.com/NovoMCP/novomcp/issues/58).

---

## A series diagnostic has more configuration surface than a scorer

A tool that scores one molecule has a small surface. One structure in, one number out; most ways to get it wrong are bugs — things that are incorrect.

A tool that reads a series inherits a larger surface, and the dangerous part is not bugs. It is decisions that are each defensible and together set the answer:

- how the data is partitioned — the storage layout truncated the tail;
- what units reach the classifier — raw versus standardised moved the population 2×;
- how a threshold defined in one endpoint's units behaves in another's — one constant, two effective gates.

None of these are claims about the phenomenon. Not one is a statement about whether `FROZEN` means anything. Each can invert the result. They do not look like part of the experiment, so they do not draw the scrutiny the experiment draws, and they sit underneath it setting the answer.

If you ship a diagnostic that reads a series, that surface is the part to review first. It is not where the interesting science is, which is exactly why it is where the wrong answers hide.

---

## What made them visible

Two of the three surfaced from process, not insight.

The pre-registration ([#36](https://github.com/NovoMCP/novomcp/issues/36), posted publicly before any result was in) required **pooling across weight bands before the answer was known**. Written after the band-by-band nulls were seen, "analyse per band" would have looked like the conservative choice, and it would have buried the effect. Fixing the aggregation before the result is what kept the truncation from winning.

The raw/standardised gap surfaced only because two independent implementations were **reconciled against a written spec** rather than compared for agreement. That distinction is the transferable part, and the one line here worth defending under any edit: two implementations agreeing tells you very little. Ours agreed on direction throughout — both said `FROZEN` axes move less — while differing twofold on the population being measured. Agreement on the headline hid a disagreement about what was being counted. It was the written spec — an external artifact neither implementation could quietly conform to the other — that made the gap visible. This is the same discipline as [spec is the source of truth](spec-source-of-truth.md), applied to a result instead of a threshold.

---

## The result

`FROZEN` validated. On the production path — a per-axis flat gate (`flat_abs = 0.5·corpus-SD` per endpoint, the fix for the third trap above) — the effect is **+0.1352 SD** beyond a step-order null that no permutation in 500 reached (**z ≈ 14.6**), on 54,312 real homologous series. The headline number moves with the configuration — not just the gate, but three separate axes of it. Standardised input reads +0.108 SD; raw input with the absolute gate reads +0.153 on the study's first (8-shard) SD basis and +0.1491 once the SD basis is unified across the full corpus; the per-axis gate then brings it to +0.1352. The gate costs about −0.014 and the basis change about −0.004; attributing both to the gate would be the exact error this piece warns against. That the validated figure moves with input, gate, and basis is the whole thesis, applied to its own result — so it is quoted with all three named, not as a bare number. The one question the study could not settle is whether `FROZEN` is separable from trivial rail-detection: a probability pinned at 0 or 1 cannot move, so "it did not move" is guaranteed rather than informative. That is a limitation of the data, not a weakness of the label — the corpus does not contain enough of the right series to reach significance on the conditional.

Fixing the gate produced one more configuration lesson, this one about the statistic we read while choosing its parameter. Sweeping the gate, we watched `perms ≥ obs` — the count of null permutations that beat the observed effect. On the 54,312-series dataset that count was 0 of 500 at *every* setting, which reads like the effect surviving any gate. It doesn't: a permutation count is censored at 1/N, and the weakest configuration here sits at z = 10.28 — p ≈ 4×10⁻²⁵, a number no 500-permutation budget can resolve. The statistic had saturated, not the effect. Reading a graded statistic instead — z, or excess-over-null, both already in the same table — the effect degrades smoothly as the gate worsens, in step across datasets that agree at k_gate 0.40 to a tenth of a percent (they coincide at 0.50 by construction — it is the normalisation point). The lesson is narrow and checkable: a permutation-count statistic is censored at 1/N_perm; confirm it varies across a sweep before using it to select a parameter, and switch to a graded statistic when it doesn't. It hides well, because `0/500` reads as strength — and a result that flatters the method draws less scrutiny than one that inconveniences it, which is exactly backwards.

The full analysis — tables, nulls, and the two rejected explanations — lives with the validation record next to the tool ([`analysis/frozen_study/`](../../orchestrator/src/novomcp/analysis/frozen_study/)). The analysis machinery (`corpus_stream_mine`, `corpus_merge_series`, `exp20`) lives in the [companion research repository](https://github.com/dmarsters/frozen-validation) — anonymous S3 corpus access, no credentials, `trajectory_diagnostic` deliberately not vendored so the validation can't drift from the tool it validates. The two cross-link.

---

## Where else this holds

The pattern applies to anything that reads a sequence rather than a point.

**Time-series monitoring.** A rule that reads a window inherits the window's boundaries, its resampling, its timezone. A latency regression that straddles a downsampling boundary vanishes the way our tails did, and "no alert fired" reads as "nothing happened."

**Cohort analytics.** A per-cohort metric depends on how cohorts are bucketed and on how a threshold set for one cohort's scale behaves on another's. Bucket by signup week under a fixed count threshold and it means one thing for a large cohort and another for a small one — the same one-constant-two-gates problem.

**Any normalise-then-classify pipeline.** The raw/standardised split is not specific to us. Every pipeline that standardises before a downstream step has a shadow copy that does not, and if validation runs on one and production on the other, both can be internally correct and jointly wrong.

---

## The discipline

Configuration is a hypothesis that does not announce itself. How the data is partitioned, what units reach the classifier, how a threshold scales across inputs — none of these read as experimental choices, and each can produce a confident wrong answer that survives every test aimed at the phenomenon.

So the tests cannot only be aimed at the phenomenon. Decide the aggregation before the result, so the partition cannot be chosen to flatter it. Reconcile independent implementations against a written spec, not against each other, so a shared blind spot cannot ratify itself. Treat every constant that touches inputs of different scale as a claim to be checked on each scale, not a number that happened to work on the one you looked at.

Make the configuration loud and it stops being able to lie quietly. That is the rule the rest of this series keeps arriving at, here applied one layer under the result — in the setup nobody thought to review.
