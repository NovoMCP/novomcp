# Bookkeeping and bookending in the FEP layer

*Reviewing design documents against the standard I apply to other people's methods. Three claims did not hold — and none of them were in the numerics.*

**Draft:** August 2026
**Author:** NovoMCP engineering

---

I reviewed my own free energy design documents against the standard I apply to other people's methods. Three claims did not hold.

None of the three were in the numerics. There were no numerics yet. All three were sitting in a schema and the prose written around it, and all three predate any implementation. The correction layer they describe is dormant in code, and it stays dormant.

The cost of finding them was an afternoon of uncomfortable reading. The cost of not finding them gets paid later, by whoever acts on the output.

If your work ends at a model card and someone downstream owns the validation, this will not be useful to you. It is written for people whose computed numbers feed a ranking or a go/no-go decision, which means they own the computation and the claim about it as two separate obligations.

## Two layers, and why they get conflated

**Bookkeeping** is the accounting inside the engine. Which frames belong to which lambda window. How work values are posted and with what sign. How energy terms are summed. What gets double counted if you are careless about a reverse leg. It is a data management problem, it has a determinate right answer, and you can test it.

**Bookending** is the physics at the endpoints. The alchemical path runs at a cheap level of theory. A higher level correction is applied at the initial and final states only, on the assumption that the correction largely cancels along the path. That assumption is a physical claim about the system in front of you, and it is not always true.

The two fail differently. Bookkeeping fails loudly: signs do not cancel, closures do not close, and a consistency check catches it. Bookending fails quietly: every number posts correctly, the accounting balances, and the answer is wrong because the reference level was not adequate for the chemistry.

A schema that guarantees correct posting discipline says nothing about whether the endpoint level of theory is valid in the regime of application. I knew that. I wrote documentation that assumed otherwise anyway.

## Finding one: an accuracy claim scoped narrower than it was stated

The design documentation carried an accuracy target for the corrected pipeline. The number came from systems where the semi-empirical reference level behaves well.

The claim as written did not carry that condition. Read plainly, it asserts an accuracy for the method. What it establishes is an accuracy for the method on chemistry inside the reference level's comfortable range.

Those are not the same population. Transition metal centers, unusual protonation and tautomer states, and strongly polarized systems are where you reach for an endpoint correction in the first place, and they are also where the correction's own reference is least trustworthy. The claim is weakest exactly where it will be invoked.

**What changes:** the target ships with the chemical scope it was established on, and the service declines to quote it for systems outside that scope. A number that refuses to be quoted where it does not hold is more useful than one that does not refuse.

## Finding two: a guarantee that widened between two sections

The documentation stated the scope of the posting guarantees in two places, and the two statements were not equivalent. One described internal consistency of the accounting. The other read as a statement about the physical result.

I did not write the second one deliberately. It is what happens when a specification gets summarized in prose during a second editing pass, and the summary is written to be readable rather than to be exact.

**What changes:** the guarantee is stated once, in the specification, and referenced everywhere else rather than restated. Restating a guarantee in prose is the mechanism by which it widens.

## Finding three: a materiality gate that inherits the uncertainty it is meant to bound

This is the substantive one, and it was caught before implementation.

The design includes a pre-screen. If the estimated uncertainty on an edge suggests the endpoint correction will not move the result materially, the correction is skipped and the compute is saved. The gate is sound in principle, and the payoff is real.

The estimate of sigma comes from the same sampling the gate is deciding about. When sampling is thin, sigma is estimated poorly, and a poorly estimated sigma can be small. So the gate is least reliable precisely where it fires most consequentially, and its characteristic failure is a false negative: the correction that would have mattered is the one skipped, and nothing downstream reports the absence, because the skipped correction is the only thing that would have revealed it was needed.

The error does not stay local. A skipped edge feeds a result that feeds a ranking, and the pre-screen retains no record of having skipped.

**What changes:** the gate requires a minimum effective sample size before it is permitted to fire at all, and every skip is recorded in the result rather than dropped. A run reports how many corrections it declined and on what basis. Neither of these exists yet. They are design decisions on a layer that is not built.

## The pattern, and why tooling misses it

Put the three together and they are one failure repeated.

**Scope.** The computation is correct on the tested set, the claim is stated without conditions, and the regime of validity was dropped in the writing.

**Guarantee.** The computation is correct, the claim is wider than the specification, and a prose summary outran the spec.

**Gate.** The computation is correct given its inputs, but one of its inputs is not adequately estimated, and that uncertainty propagated into the decision.

Correct computation and warranted claim are different properties, and almost every tool I have checks the first one. Version control, unit tests, provenance traces, reproducible environments, audit logs. They all answer the same question: did the computation happen the way I said it did. That is a good question. It was not the question I was failing.

The question I was failing is whether the conclusion follows from the output. The sharpest evidence that these are separable is that all three findings predate any code. There was nothing to test.

So this is not a class of bug. It is not caught by better testing. It is caught by asking, of each written claim, what would have to be true for it to hold, and whether that thing was established or assumed.

## The layer stays dormant

The correction layer sits on top of the equilibrium MM layer, and that layer has not passed its own validation gate. Its most recent verdict was a partial failure. This is research, not a shipped capability, and I would rather say that here than have someone find out on a call.

So the bookending layer is dormant in code, by design, and it remains so. That is the one piece of discipline in this account that worked as intended: a layer whose base has not validated does not ship, regardless of how complete the layer above it looks.

It is also the reason the review was possible at all. Reviewing claims in a schema is cheap. Reviewing them after they are load bearing in a running service is not, and by then the incentive to find them has inverted.

## How you would know this is wrong

The argument is that warrant failures are a distinct and under checked class, rather than a restatement of ordinary carelessness. I have not tested that.

The experiment: take a set of computational results with known experimental outcomes, some that held and some that did not. Apply the checks implied above. Scope stated, guarantee not restated, gate inputs adequately estimated. If the flagged results failed against experiment at a materially higher rate than the unflagged ones, the distinction is measuring something real. If the rates match, what I have is a style guide, and the three findings were ordinary sloppiness with a framework wrapped around it.

I think the first is more likely. I do not have the data, and I would rather say so than imply otherwise.

## The smaller point

These three were in a schema and its documentation. Not in the solver, not in the integrator, not in the estimator. If you have been reviewing your numerics and not your claims, you have been reviewing the half that was probably fine.

Reviewing your own design documents against the standard you apply to other people's methods takes an afternoon, and it is uncomfortable in a way that reading someone else's work is not. I would suggest it.
