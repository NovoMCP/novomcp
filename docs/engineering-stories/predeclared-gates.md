# Predeclared gates in research engineering: why we run more experiments than we need

*The one extra run that turned a headline result into a footnote*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

We were validating a molecular dynamics protocol on a flexible kinase target. The setup was expensive, each run took several hours on an A100 GPU, produced a single-number verdict on whether a physics-side intervention improved reproducibility, and had to be interpreted against a locked baseline from earlier runs. Standard practice for anyone who works with binding free energies at production scale.

The team had predeclared a validation gate before the experiment began. Two criteria, both required for pass: a primary criterion on the run-to-run reproducibility of the binding free energy, and a secondary criterion on the reproducibility of a boundary-condition sanity signal, a physical quantity we expected the intervention to tighten if it was working. The gates were predeclared at *n=3*: three independent runs per arm, with the pass/fail decision computed against the full set of three.

The first two runs of the restrained arm landed. On the secondary criterion, the two runs showed a dramatic 7× tightening compared to the unrestrained baseline. Effect size huge, both runs consistent, and the primary criterion was, well, ambiguous but suggestive.

The temptation was obvious: call it. The intervention worked on the secondary criterion. The math on the primary criterion was already unreachable, no third run could rescue it from being classified as a partial-pass rather than a clean-pass. Running the predeclared third run wouldn't change the top-line verdict on the primary criterion, and the secondary criterion looked like a slam dunk at n=2. What would n=3 add?

We ran the third one anyway. That's what "predeclared" means.

The third run refuted the secondary criterion. The 7× tightening we'd observed at n=2 dropped to a 2× tightening at n=3, modest but real, not the dramatic intervention-worked signal the first two runs had suggested. The third result was neither an outlier nor a fluke: it landed exactly where the physical intuition said it should for an intervention that was doing part of its job but not all of it.

If we'd stopped at n=2, we would have published a false positive on the secondary criterion. The intervention would have looked like a home run on the boundary-condition signal (7× tightening!), and someone reading the writeup a year later would have taken it as strong evidence for a mechanism that, at n=3, is only weakly supported.

That's the whole story. Everything below is why it generalizes.

---

## What predeclaration actually protects

The naive framing of predeclared gates is that they prevent p-hacking, you commit to a statistical test before you see the data so you can't retrofit the analysis to a positive result. That framing is correct but incomplete. In research engineering, the more common failure isn't retrofitting a test after seeing results. It's *stopping* the experiment when the interim result looks unambiguous.

The reason this failure is so easy to make is that stopping-when-obvious feels like efficiency, not corruption. You have limited compute, the outcome looks settled, and the discipline of running the last predeclared trial reads as ritual, you already know the answer.

But the interim result is only unambiguous if the underlying distribution is what you think it is. The whole reason the third run is predeclared is that you don't know the distribution yet. The two-sample effect size is measured against a variance you're estimating from those same two samples. At n=2, you have one degree of freedom on the variance estimate, which is another way of saying you don't have an estimate, you have a point.

The 7× tightening at n=2 wasn't measuring what we thought it was measuring. It was measuring what happens when two draws from the underlying distribution happen to land close together relative to the reference distribution. The third draw wasn't nearby, and the "7×" collapsed to "2×." Neither number is the truth, the truth is the underlying distribution, which we'd only start to characterize at n≥3 and would still be uncertain about at n=3.

The predeclared gate isn't a statistical formality. It's a bound on how confident you're allowed to become from a small number of samples, and the bound is calibrated against the specific kind of failure mode small samples produce.

---

## The discipline as evidence for itself

The thing that made this experience durable for us wasn't the specific bug it caught. It was the recognition that the discipline had produced new information *at the cost of one additional run*. Not a philosophical case for predeclaration. Not "we might have been wrong." Actually wrong, actually caught, actually cheap.

That's the argument that survives the next time someone in the team is deciding whether to short-circuit a predeclared gate. It's not "you should follow the discipline because it's the discipline", that argument loses to compute cost every time. It's "the last time we ran the extra trial anyway, the verdict flipped, and here's what would have shipped otherwise."

We wrote that outcome into the team memory as a first-class artifact. Not the specific numbers, those anchor to a specific target and don't generalize. The *pattern*: the sub-criterion appeared to pass at n=2 with dramatic effect size; the predeclared n=3 refuted it; the read on the underlying mechanism substantively changed. Anyone in the team can reach that story in one search, and they will reach it the next time they're deciding whether the last run is worth the compute.

The reason to document the discipline-payoff *specifically* is that the cost of the discipline is visible on every experiment (an extra run's worth of GPU-hours) while the benefit is invisible unless you catch a story like this and pin it. Left uncaught, the ledger looks lopsided: costs accumulate every week, benefits accumulate never, and the discipline erodes by attrition.

---

## Two failure modes the pattern generalizes to

The pattern isn't specific to molecular dynamics or to sample-size decisions. Two other failure modes have the same structural shape, and the same fix.

**Interim-result decision boundaries.** Any process where you're allowed to stop early on a positive interim signal is vulnerable. Bayesian sequential testing has a formal framework for this; most research engineering doesn't. The informal version is: predeclare not just the final gate but the interim decision boundaries. "If the first two runs show effect size E and the variance is V, we still run the third, the third is a required part of the verdict, not an option we exercise if the first two are ambiguous."

**Cost-asymmetric verifications.** Any verification whose cost is high relative to the marginal information it produces is vulnerable to the "we know the answer" shortcut. Integration tests that take 20 minutes to run, on a feature that "obviously" works. End-to-end smoke tests after a "trivial" refactor. Manual verifications of deploys where the CI is green. The specific verification that catches something meaningful will be the one that felt least worth doing. Predeclared "yes, we run all of these before merge" is what protects against the drift.

**Compute-committed research campaigns.** When a decision cascade is downstream of a validation result, the validation result becomes structurally load-bearing. In our case, a locked baseline number from this validation would feed a threshold for a follow-on experiment, and that threshold would gate the decision on whether a third experiment was worth running at all. Under-characterizing the baseline at n=2 would propagate a false-tightening signal through the entire cascade. The predeclared n=3 wasn't just protecting the current verdict; it was protecting every decision downstream of the current verdict.

---

## The one caveat

Predeclared gates are a discipline for research where the sample cost is bounded and the outcome is one number. Some research doesn't fit that shape.

If you're running an open-ended exploratory sweep, screening 200 conditions to find the two that look promising, predeclaring a gate on each of the 200 is neither cheap nor coherent. The relevant discipline for exploration is different: preregister the criteria you'll use to select the follow-on set, run the exploration, and then *predeclare gates on the follow-on validation* of the selected conditions. Exploration and validation are different phases and want different disciplines.

The failure mode we're describing is specific to validation: you have a claim, you're testing it with a fixed number of trials, and the temptation is to stop when the interim signal looks decisive. That's the shape where predeclared gates earn their keep.

---

## What we actually do

Every validation experiment on the team ships with a predeclared spec, a short markdown document listing the pass criteria, the sample size, the interim decision boundaries, and the fallback thresholds if the primary numbers fall in ambiguous ranges. The spec is authored before any runs are submitted, reviewed by someone other than the author, and locked. The verdict report the harness produces is bit-exact reconcilable against the spec: same row IDs, same thresholds, same denominator.

Amendments to a locked spec are allowed but expensive. Any amendment gets a decision-trail row that names the reason, the affected criteria, and the date. Amendments made in response to interim results, the exact failure mode the discipline exists to prevent, are structurally discouraged by requiring the amendment to justify itself against a rule other than "we saw the data and want to redraw the lines."

That's the whole apparatus. It's not sophisticated. It's a markdown file, a rule about when it gets written, a reviewer who isn't the author, and a report format that matches the spec format. The apparatus doesn't matter, the discipline does. The apparatus is just what makes the discipline hard to bypass on any given afternoon.

The 7× tightening that turned into a 2× tightening cost us one extra GPU-hour. It saved us from a footnote-shaped correction in a follow-on paper. That's the trade, and once you've seen it happen, you don't need to be talked into it again.
