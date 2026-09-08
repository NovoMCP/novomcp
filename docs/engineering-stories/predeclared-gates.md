# Predeclared gates in research engineering

*The predeclared run that turned a headline result into a footnote*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

The NovoMCP validation harness runs every predeclared trial before it renders a verdict. A validation gate declared at n=3 costs three independent runs per arm. The pass/fail decision computes against the full set of three, never against a favorable interim of two. The discipline runs the experiment that can fail the claim, at the cost of one additional run, and it holds on every validation experiment the team ships.

This is the standard, the case that proves it out, and the two failure modes it generalizes to.

---

## The run that flipped the verdict

The experiment validated a molecular dynamics protocol on a flexible kinase target. Each run took several hours on an A100 GPU, produced a single-number verdict on whether a physics-side intervention improved reproducibility, and was interpreted against a locked baseline from earlier runs. Standard practice for anyone working with binding free energies at production scale.

The gate was predeclared before the experiment began. Two criteria, both required for a pass: a primary criterion on the run-to-run reproducibility of the binding free energy, and a secondary criterion on the reproducibility of a boundary-condition sanity signal, a physical quantity the intervention was expected to tighten if it was working. Both gates were predeclared at n=3.

The first two runs of the restrained arm landed. On the secondary criterion, the two runs showed a 7x tightening compared to the unrestrained baseline. Large effect size, both runs consistent. The primary criterion read as ambiguous but suggestive.

The interim result invited a stop. The primary criterion was already unreachable for a clean pass; no third run could rescue it from a partial-pass classification. The secondary criterion read as decisive at n=2. The engine ran the third trial anyway. That is what predeclared means.

The third run refuted the secondary criterion. The 7x tightening at n=2 dropped to a 2x tightening at n=3, modest but real, not the intervention-worked signal the first two runs suggested. The third result was neither an outlier nor a fluke. It landed exactly where the physical intuition placed it for an intervention doing part of its job and not all of it.

A stop at n=2 would have published a false positive on the secondary criterion. The intervention would have read as a home run on the boundary-condition signal at 7x tightening, and a reader a year later would have taken it as strong evidence for a mechanism that, at n=3, is only weakly supported. One predeclared run reclassified a headline result as a footnote.

---

## Predeclared gates bound confidence, not just analysis

Predeclared gates protect against more than p-hacking. Committing to a statistical test before seeing the data prevents retrofitting the analysis to a positive result. That is correct and incomplete. In research engineering the more common failure is stopping the experiment when the interim result looks unambiguous.

Stopping-when-obvious presents as efficiency. Compute is limited, the outcome reads as settled, and the last predeclared trial reads as ritual. The interim result is unambiguous only if the underlying distribution matches expectation. The third run is predeclared precisely because the distribution is unknown. The two-sample effect size is measured against a variance estimated from those same two samples. At n=2 there is one degree of freedom on the variance estimate, which is a point, not an estimate.

The 7x tightening at n=2 measured what happens when two draws from the underlying distribution land close together relative to the reference distribution. The third draw landed elsewhere, and the 7x collapsed to 2x. Neither number is the truth. The truth is the underlying distribution, characterized starting at n>=3 and still uncertain at n=3.

**The predeclared gate is a bound on how much confidence a small number of samples is allowed to produce.** The bound is calibrated against the specific failure mode small samples generate. It is a statistical instrument, not a formality.

---

## The discipline is documented as its own evidence

The durable output was not the specific bug the run caught. It was the recognition that the discipline produced new information at the cost of one additional run. Not a philosophical case for predeclaration. Actually wrong, actually caught, actually cheap.

That is the argument that holds the next time a team member decides whether to short-circuit a predeclared gate. "Follow the discipline because it is the discipline" loses to compute cost every time. "The last time the extra trial ran anyway, the verdict flipped, and here is what would have shipped otherwise" holds.

The outcome is written into team memory as a first-class artifact. Not the specific numbers, which anchor to one target and do not generalize. The pattern: the sub-criterion appeared to pass at n=2 with a large effect size, the predeclared n=3 refuted it, and the read on the underlying mechanism substantively changed. Anyone on the team reaches that story in one search, and reaches it the next time the last run is weighed against its compute.

**The discipline-payoff is documented specifically because the cost is visible on every experiment and the benefit is invisible unless a case is caught and pinned.** The cost is an extra run's worth of GPU-hours, accruing every week. Left undocumented, the ledger reads lopsided: costs accumulate always, benefits accumulate never, and the discipline erodes by attrition. Pinning the payoff keeps the ledger honest.

---

## The pattern generalizes to three failure modes

The shape is not specific to molecular dynamics or to sample-size decisions. Three failure modes share the structure and take the same fix.

**Interim-result decision boundaries.** Any process that permits an early stop on a positive interim signal is vulnerable. Bayesian sequential testing formalizes this; most research engineering does not. The informal version predeclares the interim decision boundaries alongside the final gate. If the first two runs show effect size E and variance V, the third still runs. The third is a required part of the verdict, not an option exercised when the first two are ambiguous.

**Cost-asymmetric verifications.** Any verification whose cost is high relative to the marginal information it produces is vulnerable to the "answer already known" shortcut. Integration tests that take 20 minutes on a feature that obviously works. End-to-end smoke tests after a trivial refactor. Manual verification of deploys where CI is green. The verification that catches something meaningful is the one that felt least worth doing. A predeclared "all of these run before merge" protects against the drift.

**Compute-committed research campaigns.** When a decision cascade sits downstream of a validation result, that result becomes structurally load-bearing. Here a locked baseline number from this validation feeds a threshold for a follow-on experiment, and that threshold gates whether a third experiment is worth running at all. Under-characterizing the baseline at n=2 propagates a false-tightening signal through the entire cascade. The predeclared n=3 protects every decision downstream of the current verdict, not only the current verdict.

---

## The scope the standard covers

Predeclared gates are a validation discipline: the sample cost is bounded and the outcome is one number. Exploration wants a different discipline.

An open-ended exploratory sweep screening 200 conditions to find the two that look promising cannot predeclare a coherent gate on each of the 200. Exploration preregisters the criteria used to select the follow-on set, runs the sweep, and then predeclares gates on the follow-on validation of the selected conditions. Exploration and validation are different phases and want different disciplines.

The failure mode the standard addresses is specific to validation: a claim under test with a fixed number of trials, and the temptation to stop when the interim signal reads decisive. That is the shape where predeclared gates earn their keep.

---

## The apparatus the engine runs

Every validation experiment on the team ships with a predeclared spec: a short markdown document listing the pass criteria, the sample size, the interim decision boundaries, and the fallback thresholds for numbers that land in ambiguous ranges. The spec is authored before any runs are submitted, reviewed by someone other than the author, and locked. The verdict report the harness produces is bit-exact reconcilable against the spec: same row IDs, same thresholds, same denominator.

Amendments to a locked spec are allowed and expensive. Every amendment gets a decision-trail row naming the reason, the affected criteria, and the date. Amendments made in response to interim results, the exact failure mode the discipline exists to prevent, are structurally discouraged: the amendment must justify itself against a rule other than "the data is in and the lines want redrawing."

The apparatus is a markdown file, a rule about when it gets written, a reviewer who is not the author, and a report format that matches the spec format. The apparatus makes the discipline hard to bypass on any given afternoon.

The 7x tightening that turned into a 2x tightening cost one extra GPU-hour. It prevented a footnote-shaped correction in a follow-on paper. That is the trade, and once seen, it does not need arguing again.
