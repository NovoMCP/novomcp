# When to pause and when to continue

*Fatigue discipline in long research-engineering sessions*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

Research-engineering sessions are long. Not because the work is inefficient, but because the work has a particular shape: build a primitive, validate it, compose it with others, run it against real data, iterate on what surfaces. Each step's context is expensive to build in your head and expensive to lose. You want to keep going because the context is loaded.

The problem is that some of the steps are wire-up work that benefits from being tired-tolerant (mechanical, well-defined, unlikely to require judgment). And some of them are fresh-eyes work that punishes fatigue disproportionately (reading unfamiliar artifacts, resolving ambiguous data, integration debug where the symptom is multiple layers away from the cause). Continuing through a fresh-eyes-work boundary while tired is where subtle bugs enter the system and don't come out.

We've been developing a working discipline about when to pause and when to continue. This is the current state of it.

---

## The two shapes of work

Not all engineering work has the same fatigue profile.

**Loaded-context work** benefits from continuation. If you've just built and validated three primitives, wiring them together is loaded-context work, the mental model of how the primitives compose is fresh, the tests you'd need to write are obvious, the code changes are mechanical. Losing the mental model between "primitives validated" and "primitives wired" costs real time, because you have to rebuild it before you can safely make the wiring changes. Continuing captures the loaded state.

**Fresh-eyes work** punishes continuation. If the next step involves reading an artifact whose structure you don't fully know, resolving a numbering convention or a serialization format that could be off in a subtle way, or debugging an integration failure where the symptom is downstream of multiple layers, that's fresh-eyes work. Doing it tired risks introducing an off-by-one, a wrong index, a silent format assumption that the code will fail to catch because you were the one who wrote both the assumption and the check. These bugs don't surface until much later, at which point they've compounded through several rounds of "well, the primitive tests pass."

The two shapes look similar from the outside. Both are "code work at your desk." The difference is where the failure modes live and how tired matters.

---

## Two stories

**Story one: a wire-up we did rested.** We had validated three primitives, a topology-marking function, a topology-scaling function, and a molecular-dynamics multi-simulation runner. Each was individually correct on unit tests. The next step was wiring them into an equilibration path in a larger simulation function, with a handoff from one replica's output to the next stage's input.

We were at the end of a long day when the wire-up was ready to start. The question was: continue and land the wiring tonight, or pause and start fresh in the morning?

We paused. The choice was almost specifically because of one small step in the wire-up: reconciling a residue numbering convention between what the topology-marking function saw and what the trajectory-reading function would read. That reconciliation required inspecting a real preprocessed topology file (not a fixture), understanding the numbering scheme that a specific tool imposed, and mapping it to the locked residue list we'd predeclared in the spec.

That inspection is fresh-eyes work. Reading a topology file for numbering conventions while tired is where you convince yourself that the convention is one thing when it's another, and the confidence is what makes the bug ship.

We picked up the next morning. Cold-reading the three primitives as a composed sequence, before touching wire-up code, surfaced a composability bug we hadn't caught in the unit tests: the marking function's residue-number matching wasn't scoped to a specific `[moleculetype]` block, so on a real solvated topology with multiple blocks, water molecules with the same per-block residue number as the protein pocket would be marked wrong. The unit test hadn't caught it because the fixture had a single block. Cold-read caught it in fifteen minutes.

If we'd wired it tired the night before, the bug would have shipped past unit tests, past integration smoke, and would have surfaced only at the first real GPU run, where the wrong-atom marking would have produced a plausible-looking but incorrect result. Fresh eyes found it before it cost real compute.

**Story two: a wire-up we did tired, on purpose.** A few weeks later, we had another wire-up: composing the same three primitives (now fixed and validated) into the equilibration function, and testing the composition end-to-end on a small system.

We were tired again. The question was the same: pause or continue?

We continued. The reason: the composition step wasn't fresh-eyes work. Every primitive was individually validated. The unit tests for the wire-up were mechanical (mock the invocations, verify the sequence and arguments). The integration smoke was a straightforward end-to-end run on a small fixture. There was no unfamiliar artifact to read, no numbering convention to reconcile, no cross-tool assumption to verify. The mental model of how the primitives composed was fresh; losing it overnight would have cost hours to rebuild, and the failure modes of continuing were bounded.

The wire-up landed clean. The integration smoke passed on the first try.

Two continuations, two outcomes. The difference wasn't luck. It was the character of the specific step, fresh-eyes work in story one, loaded-context mechanical work in story two, and the willingness to distinguish them.

---

## The rule that emerged

**Pause before fresh-eyes work. Continue through loaded-context work.**

Fresh-eyes work has three properties that make it fatigue-sensitive:
1. The relevant information isn't fully in your head, you have to read an artifact, or an unfamiliar codebase, or a data format you don't fully know.
2. The failure modes are subtle. Wrong indices, wrong conventions, wrong assumptions about what a tool preserves versus what it silently changes.
3. The failures don't surface at the boundary where they were introduced. They compound through downstream stages until they emerge as an implausible physical result or an integration error.

Loaded-context work has the opposite properties:
1. The relevant information is currently in your head, you built the primitives, you know how they compose.
2. The failure modes are noisy. Type errors, missing imports, obvious integration mismatches that the tests will catch.
3. The failures surface immediately when the code runs.

The distinction is which failure modes are cheap to catch and which are expensive. Cheap-to-catch failures survive fatigue because the feedback loop is tight. Expensive-to-catch failures don't, because a tired engineer will write code that silently obscures the failure mode until it's expensive to trace.

---

## The mechanics of the pause

A pause is not "stop working forever." It's "stop until you can do this specific class of work well." Sometimes that's overnight. Sometimes it's an hour with lunch. Sometimes it's a walk around the block. The right length is the length that gets you back to the mental state where fresh-eyes work is not disproportionately risky.

Two mechanical things make the pause cheap to resume from.

**Pin the state at the pause boundary in writing.** Not in Slack, not in a git commit, not in your head, in a specific file that the next session can open first. The pin includes: what was completed, what's the next step, why we're pausing here specifically, and what the resume order is. In our case, the file is a `project_state.md` in team memory, updated on every pause. The first thing the next session does is read it.

**Predeclare the resume order.** Not "figure out where to start when I get back", decide the order now, while you still have the context. If the resume order is "cold-read the three primitives before touching wire-up code, then reconcile residue numbering against a real topology, then wire the equilibration path," write that down. Fresh-you doesn't have to reconstruct the plan; fresh-you executes it.

Both of these are two-minute tasks. They cost nothing at the pause boundary and buy back an hour of context-reconstruction at the resume boundary. If you're going to pause at all, pausing well is cheap.

---

## The failure mode a pause protects against

The failure mode isn't "I made a mistake because I was tired." Tired engineers make normal mistakes at slightly higher rates, and normal mistakes get caught by normal review. The failure mode is specifically the subtle-assumption class: the one where you convinced yourself of a fact about an artifact, wrote code that depended on the fact, and wrote a test that assumed the same fact. All three, the belief, the code, the test, are wrong the same way, and none of them catch the others.

That failure mode is what fresh-eyes work is uniquely bad at while tired. Because the relevant information isn't in your head, you're filling it in from whatever cognitive shortcut is nearest. And because you wrote the test, the test embeds the same shortcut. The bug ships with a green test suite.

Pausing before fresh-eyes work protects specifically against this. You do the artifact-reading with a mind that's actually building the model from the artifact, not from your assumptions about the artifact. And then you write the test against what the artifact actually shows, not against what you decided it must show.

---

## Where this pattern shows up in other domains

**Cross-service integration debug.** When a distributed system fails and the symptom is three services away from the cause, tracing the cause is fresh-eyes work, you're reading logs from services you didn't write, in contexts you don't fully know, with assumptions about their protocols that might be off. Pausing before starting a cross-service trace is often cheaper than pushing through and mis-attributing the cause.

**Database schema migrations.** Designing a migration on a schema you didn't build, or one that has subtle constraints you haven't loaded, is fresh-eyes work. The mistakes are subtle (missing an implicit constraint that a query in another service depended on) and surface late (in production, hours after the migration ran). Pausing to inspect the actual schema and its consumers before writing the migration script prevents an entire class of "the migration succeeded but broke this other thing" outcomes.

**Reading an unfamiliar codebase to make a change.** If the change is small and localized and you know the interface, it's often loaded-context work, you can push through. If the change requires understanding a control flow you've never traced, it's fresh-eyes work. The tell is whether you're sure enough of the surrounding behavior to make the change confidently. If you'd have to trace the caller graph to be confident, that tracing is the fresh-eyes step, and it's what the pause is for.

**Security review of your own code.** Reviewing code you wrote is loaded-context work in the sense that you know the intent. It's fresh-eyes work in the sense that you're specifically trying to find the failure modes you didn't consider when writing it. This is why "code you review right after writing it" tends to catch less than "code you review after a night's sleep", the reviewer needs to be someone who's not you, or a version of you that has forgotten enough of the intent to look at the code with fresh assumptions.

---

## The counter-case

The discipline isn't "always pause when tired." It's "pause before fresh-eyes work when tired." Loaded-context mechanical work is often *better* to push through when tired, because pausing costs the context and gains you nothing on a class of failure the fatigue wasn't going to introduce anyway.

Wiring three validated primitives into a composition function, on a green test suite, with a straightforward integration smoke as the next step, that's the shape you push through. The failure modes are noisy, the tests will catch them, the mental model is loaded, and the cost of losing it is higher than the marginal risk of continuing.

The distinction the discipline requires is telling the two shapes of work apart. That skill is developed by paying attention to when continuing bit you and when pausing did. Over time, the boundaries between shapes become recognizable. Fresh-eyes work has a texture, it feels like "I'm about to read something unfamiliar" or "I'm about to reconcile two conventions I'm not sure I understand." Loaded-context work has a different texture, "I already know how this composes, I just need to type it out."

Learn the textures. Trust them when they show up.

---

## What we actually do

Every long research-engineering session ends with a pause pin. The pin is a written artifact, a specific file with a specific structure, that names the state at the pause, the next step, and the resume order. Any team member can pick up the session by reading the pin.

Fresh-eyes work is deferred to session boundaries where possible. If a wire-up requires reading an unfamiliar artifact, we plan the wire-up to start with the artifact-reading and finish with the wire-up itself, so the tiring part happens first and the fresh-eyes part happens fresh. If that ordering is impossible, the session ends at the fresh-eyes boundary and resumes there.

Loaded-context work is pushed through, deliberately, when the mental model is expensive to rebuild. The distinction is called out, "this is loaded-context work, continuing" or "this is fresh-eyes work, pausing", often out loud, sometimes in the pause pin itself. Naming which shape you're in makes the choice legible to the future you and to anyone else on the team who will pick up the work.

That's the whole discipline. Two shapes of work. Learn to tell them apart. Pause before one, push through the other. Pin the state so pauses are cheap. And when the pause vindicates itself, when you cold-read something in the morning and catch a bug that would have shipped, pin that too, so the case for the discipline is renewed with concrete evidence rather than remembered as a general belief.

The evidence is what keeps the discipline alive over time. Without it, "we should pause when tired" degrades into "we should get more done" the first time compute costs come up in a planning meeting. With it, "here's a bug we caught by pausing" is a specific answer to a specific question. That's what makes the discipline durable.
