# Bookkeeping and bookending in the FEP layer

*The free-energy design documents were reviewed against the standard NovoMCP applies to other methods. Three claims did not hold. None were in the numerics.*

**Draft:** August 2026
**Author:** NovoMCP engineering

---

The free-energy design documents were reviewed against the standard NovoMCP applies to other people's methods. Three claims did not hold.

None of the three were in the numerics. There were no numerics yet. All three sat in a schema and the prose written around it, and all three predate any implementation. The correction layer they describe is dormant in code, and it stays dormant.

The cost of finding them was an afternoon of uncomfortable reading. The cost of not finding them is paid later, by whoever acts on the output.

This piece is written for work whose computed numbers feed a ranking or a go/no-go decision. That work owns the computation and the claim about it as two separate obligations. Work that ends at a model card, with validation owned downstream, carries only the first.

## Two layers, and why they get conflated

**Bookkeeping** is the accounting inside the engine. Which frames belong to which lambda window. How work values are posted and with what sign. How energy terms are summed. What double counts under a careless reverse leg. It is a data management problem, it has a determinate right answer, and it is testable.

**Bookending** is the physics at the endpoints. The alchemical path runs at a cheap level of theory. A higher-level correction is applied at the initial and final states only, on the assumption that the correction largely cancels along the path. That assumption is a physical claim about the system in front of you, and it holds only sometimes.

The two fail differently. Bookkeeping fails loudly: signs do not cancel, closures do not close, and a consistency check catches it. Bookending fails quietly: every number posts correctly, the accounting balances, and the answer is wrong because the reference level was not adequate for the chemistry.

A schema that guarantees correct posting discipline says nothing about whether the endpoint level of theory is valid in the regime of application. The design documentation assumed otherwise.

## Finding one: an accuracy claim scoped narrower than it was stated

The design documentation carried an accuracy target for the corrected pipeline. The number came from systems where the semi-empirical reference level behaves well.

The claim as written did not carry that condition. Read plainly, it asserts an accuracy for the method. What it establishes is an accuracy for the method on chemistry inside the reference level's comfortable range.

Those are two different populations. Transition metal centers, unusual protonation and tautomer states, and strongly polarized systems are where an endpoint correction is reached for in the first place, and they are also where the correction's own reference is least trustworthy. The claim is weakest exactly where it will be invoked.

**The standard: a target ships with the chemical scope it was established on, and the service declines to quote it for systems outside that scope.** A number that refuses to be quoted where it does not hold is more useful than one that does not refuse.

## Finding two: a guarantee that widened between two sections

The documentation stated the scope of the posting guarantees in two places, and the two statements were not equivalent. One described internal consistency of the accounting. The other read as a statement about the physical result.

The second was not written deliberately. It is what happens when a specification gets summarized in prose during a second editing pass, and the summary is written to be readable rather than to be exact.

**The standard: a guarantee is stated once, in the specification, and referenced everywhere else rather than restated.** Restating a guarantee in prose is the mechanism by which it widens.

## Finding three: a materiality gate that inherits the uncertainty it bounds

This is the substantive finding, caught before implementation.

The design includes a pre-screen. When the estimated uncertainty on an edge suggests the endpoint correction will not move the result materially, the correction is skipped and the compute is saved. The gate is sound in principle, and the payoff is real.

The estimate of sigma comes from the same sampling the gate is deciding about. When sampling is thin, sigma is estimated poorly, and a poorly estimated sigma can be small. So the gate is least reliable precisely where it fires most consequentially. Its characteristic failure is a false negative: the correction that would have mattered is the one skipped, and nothing downstream reports the absence, because the skipped correction is the only thing that would have revealed it was needed.

The error does not stay local. A skipped edge feeds a result that feeds a ranking, and the pre-screen retains no record of having skipped.

**The standard: the gate requires a minimum effective sample size before it is permitted to fire at all, and every skip is recorded in the result rather than dropped.** A run reports how many corrections it declined and on what basis. These are design decisions on a layer that is not built.

## The pattern, and why tooling misses it

The three together are one failure repeated.

**Scope.** The computation is correct on the tested set, the claim is stated without conditions, and the regime of validity was dropped in the writing.

**Guarantee.** The computation is correct, the claim is wider than the specification, and a prose summary outran the spec.

**Gate.** The computation is correct given its inputs, but one of its inputs is not adequately estimated, and that uncertainty propagated into the decision.

Correct computation and warranted claim are different properties. Almost every tool checks the first. Version control, unit tests, provenance traces, reproducible environments, audit logs: they all answer whether the computation happened the way it was described. That is a good question. It is not the question these three findings failed.

The question they failed is whether the conclusion follows from the output. The sharpest evidence that these are separable properties is that all three findings predate any code. There was nothing to test.

So this is not a class of bug. Better testing does not catch it. It is caught by asking, of each written claim, what would have to be true for it to hold, and whether that thing was established or assumed.

## The layer stays dormant

The correction layer sits on top of the equilibrium MM layer, and that layer has not passed its own validation gate. Its most recent verdict was a partial failure. This is research, not a shipped capability, stated plainly here rather than discovered on a call.

So the bookending layer is dormant in code, by design, and it stays so. That is the one piece of discipline in this account that worked as intended: a layer whose base has not validated does not ship, however complete the layer above it looks.

Dormancy is also what made the review possible. Reviewing claims in a schema is cheap. Reviewing them after they are load-bearing in a running service is not, and by then the incentive to find them has inverted.

## How you would know this is wrong

The argument is that warrant failures are a distinct and under-checked class, not a restatement of ordinary carelessness. That argument is untested.

The experiment: take a set of computational results with known experimental outcomes, some that held and some that did not. Apply the checks implied above. Scope stated. Guarantee not restated. Gate inputs adequately estimated. If the flagged results failed against experiment at a materially higher rate than the unflagged ones, the distinction is measuring something real. If the rates match, the framework is a style guide, and the three findings were ordinary sloppiness with a framework wrapped around it.

The first is the more likely outcome. The data to settle it does not exist yet, and that is stated rather than implied otherwise.

## The smaller point

These three were in a schema and its documentation. Not in the solver, not in the integrator, not in the estimator. Numerics-only review covers the half that was probably fine.

Reviewing design documents against the standard applied to other people's methods takes an afternoon. It is uncomfortable in a way that reading someone else's work is not. It is worth the afternoon.
