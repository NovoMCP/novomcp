# When to pause and when to continue

*The fatigue discipline NovoMCP's results are produced under*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

NovoMCP produces its research-engineering results under an explicit fatigue discipline: pause before fresh-eyes work, continue through loaded-context work. The discipline sorts every step by which failure modes fatigue introduces and which it does not. It defers the artifact-reading, the convention-reconciling, the multi-layer integration debug to a fresh mind, and it pushes through the mechanical wire-up while the mental model is loaded. The state at every pause boundary is pinned in writing so the pause is cheap to resume from.

Research-engineering sessions are long, because the work has a particular shape: build a primitive, validate it, compose it with others, run it against real data, iterate on what surfaces. Each step's context is expensive to build and expensive to lose. Some steps are wire-up work that tolerates fatigue, mechanical and well-defined. Some are fresh-eyes work that punishes fatigue: reading unfamiliar artifacts, resolving ambiguous data, integration debug where the symptom sits multiple layers from the cause. Continuing through a fresh-eyes boundary while tired is where subtle bugs enter the system and stay. This is the discipline, its evidence, and the standard the engine's results are produced under.

---

## The two shapes of work

Engineering work carries two distinct fatigue profiles.

**Loaded-context work benefits from continuation.** With three primitives just built and validated, wiring them together is loaded-context work: the mental model of how the primitives compose is fresh, the tests to write are obvious, the code changes are mechanical. Losing the mental model between "primitives validated" and "primitives wired" costs real time, because the model has to be rebuilt before the wiring changes are safe. Continuing captures the loaded state.

**Fresh-eyes work punishes continuation.** When the next step reads an artifact whose structure is not fully known, reconciles a numbering convention or a serialization format that could be off in a subtle way, or debugs an integration failure whose symptom is downstream of multiple layers, that is fresh-eyes work. Doing it tired risks an off-by-one, a wrong index, a silent format assumption the code fails to catch because the same person wrote both the assumption and the check. These bugs surface much later, after they have compounded through several rounds of "the primitive tests pass."

The two shapes look alike from the outside. Both are code work at a desk. They differ in where the failure modes live and how fatigue reaches them.

---

## Two results

**Result one: a wire-up done rested.** Three primitives were validated, a topology-marking function, a topology-scaling function, and a molecular-dynamics multi-simulation runner. Each was individually correct on unit tests. The next step wired them into an equilibration path in a larger simulation function, with a handoff from one replica's output to the next stage's input.

The wire-up was ready to start at the end of a long day. The discipline paused it. The pause turned on one small step: reconciling a residue numbering convention between what the topology-marking function saw and what the trajectory-reading function would read. That reconciliation required inspecting a real preprocessed topology file, not a fixture, understanding the numbering scheme a specific tool imposed, and mapping it to the locked residue list predeclared in the spec.

That inspection is fresh-eyes work. Reading a topology file for numbering conventions while tired is where a wrong convention gets locked in with confidence, and the confidence is what ships the bug.

The session resumed the next morning. Cold-reading the three primitives as a composed sequence, before touching wire-up code, surfaced a composability bug the unit tests had missed: the marking function's residue-number matching was not scoped to a specific `[moleculetype]` block, so on a real solvated topology with multiple blocks, water molecules sharing a per-block residue number with the protein pocket would be marked wrong. The unit test's fixture had a single block. The cold-read caught it in fifteen minutes.

Wired tired the night before, the bug would have shipped past unit tests, past integration smoke, and surfaced only at the first real GPU run, where the wrong-atom marking would have produced a plausible-looking but incorrect result. Fresh eyes found it before it cost real compute.

**Result two: a wire-up done tired, on purpose.** A few weeks later, another wire-up composed the same three primitives, now fixed and validated, into the equilibration function, and tested the composition end-to-end on a small system.

The end of the day arrived again. The discipline continued. The composition step was not fresh-eyes work. Every primitive was individually validated. The unit tests for the wire-up were mechanical: mock the invocations, verify the sequence and arguments. The integration smoke was a straightforward end-to-end run on a small fixture. No unfamiliar artifact to read, no numbering convention to reconcile, no cross-tool assumption to verify. The mental model of how the primitives composed was fresh, losing it overnight would have cost hours to rebuild, and the failure modes of continuing were bounded.

The wire-up landed clean. The integration smoke passed on the first try.

Two continuations, two outcomes. The difference was the character of the specific step, fresh-eyes work in result one, loaded-context mechanical work in result two, and the discipline that tells them apart.

---

## The rule

**Pause before fresh-eyes work. Continue through loaded-context work.**

Fresh-eyes work carries three properties that make it fatigue-sensitive:
1. The relevant information is not fully in your head. It has to be read from an artifact, an unfamiliar codebase, or a data format that is not fully known.
2. The failure modes are subtle. Wrong indices, wrong conventions, wrong assumptions about what a tool preserves versus what it silently changes.
3. The failures surface far from the boundary where they were introduced. They compound through downstream stages until they emerge as an implausible physical result or an integration error.

Loaded-context work carries the opposite properties:
1. The relevant information is currently in your head. You built the primitives and you know how they compose.
2. The failure modes are noisy. Type errors, missing imports, obvious integration mismatches the tests catch.
3. The failures surface immediately when the code runs.

The distinction is which failure modes are cheap to catch and which are expensive. Cheap-to-catch failures survive fatigue because the feedback loop is tight. Expensive-to-catch failures do not, because a tired engineer writes code that silently obscures the failure mode until it is expensive to trace.

---

## The mechanics of the pause

A pause is a stop until this specific class of work can be done well. Sometimes that is overnight, sometimes an hour with lunch, sometimes a walk around the block. The right length restores the mental state where fresh-eyes work is not disproportionately risky. Two mechanical commitments make the pause cheap to resume from.

**Pin the state at the pause boundary in writing.** In a specific file the next session opens first, rather than in Slack, a git commit, or memory. The pin records what was completed, the next step, why the pause lands here specifically, and the resume order. The file is a `project_state.md` in team memory, updated on every pause. The next session reads it first.

**Predeclare the resume order.** Decide the order now, while the context is loaded, rather than reconstructing it on return. When the resume order is "cold-read the three primitives before touching wire-up code, then reconcile residue numbering against a real topology, then wire the equilibration path," write that down. Fresh-you executes the plan instead of rebuilding it.

Both are two-minute tasks. They cost nothing at the pause boundary and buy back an hour of context reconstruction at the resume boundary. Pausing well is cheap.

---

## The failure mode a pause protects against

The protected failure mode is the subtle-assumption class: a fact about an artifact gets accepted, code depends on the fact, and a test assumes the same fact. All three, the belief, the code, the test, are wrong the same way, and none catch the others. The bug ships with a green test suite.

Fresh-eyes work is uniquely bad at that class while tired. The relevant information is not in your head, so it gets filled in from whatever cognitive shortcut is nearest, and the test written alongside embeds the same shortcut. Pausing before fresh-eyes work builds the model from the artifact rather than from assumptions about the artifact, and writes the test against what the artifact actually shows.

---

## Where this pattern shows up in other domains

**Cross-service integration debug.** When a distributed system fails and the symptom is three services away from the cause, tracing the cause is fresh-eyes work: reading logs from services you did not write, in contexts you do not fully know, with assumptions about their protocols that might be off. Pausing before a cross-service trace is often cheaper than pushing through and mis-attributing the cause.

**Database schema migrations.** Designing a migration on a schema you did not build, or one with subtle constraints not yet loaded, is fresh-eyes work. The mistakes are subtle, missing an implicit constraint a query in another service depended on, and surface late, in production hours after the migration ran. Pausing to inspect the actual schema and its consumers before writing the migration script prevents an entire class of "the migration succeeded but broke this other thing" outcomes.

**Reading an unfamiliar codebase to make a change.** A small, localized change on a known interface is often loaded-context work worth pushing through. A change that requires understanding a control flow never traced is fresh-eyes work. The tell is whether the surrounding behavior is understood well enough to make the change confidently. When confidence requires tracing the caller graph, that tracing is the fresh-eyes step, and the pause is for it.

**Security review of your own code.** Reviewing code you wrote is loaded-context in the sense that the intent is known. It is fresh-eyes in the sense that the target is the failure modes not considered when writing it. Code reviewed right after writing catches less than code reviewed after a night's sleep. The reviewer needs to be someone other than you, or a version of you that has forgotten enough of the intent to read the code with fresh assumptions.

---

## The counter-case

The discipline is pause before fresh-eyes work when tired, not always pause when tired. Loaded-context mechanical work is often better to push through when tired, because pausing costs the context and gains nothing on a class of failure the fatigue was not going to introduce.

Wiring three validated primitives into a composition function, on a green test suite, with a straightforward integration smoke as the next step, is the shape to push through. The failure modes are noisy, the tests catch them, the mental model is loaded, and the cost of losing it exceeds the marginal risk of continuing.

The discipline requires telling the two shapes apart. That skill develops by paying attention to when continuing bit and when pausing paid. Over time the boundaries between shapes become recognizable. Fresh-eyes work has a texture: "I am about to read something unfamiliar" or "I am about to reconcile two conventions I am not sure I understand." Loaded-context work has a different texture: "I already know how this composes, I just need to type it out." Learn the textures. Trust them when they show up.

---

## The standard NovoMCP runs

Every long research-engineering session ends with a pause pin, a written artifact in a specific file with a specific structure, naming the state at the pause, the next step, and the resume order. Any team member picks up the session by reading the pin.

Fresh-eyes work is deferred to session boundaries where possible. A wire-up that requires reading an unfamiliar artifact is planned to start with the artifact-reading and finish with the wire-up itself, so the tiring part happens first and the fresh-eyes part happens fresh. When that ordering is impossible, the session ends at the fresh-eyes boundary and resumes there.

Loaded-context work is pushed through, deliberately, when the mental model is expensive to rebuild. The shape is called out, "this is loaded-context work, continuing" or "this is fresh-eyes work, pausing," often out loud, sometimes in the pause pin itself. Naming the shape makes the choice legible to the future session and to anyone else picking up the work.

That is the whole discipline. Two shapes of work. Tell them apart. Pause before one, push through the other. Pin the state so pauses are cheap. When a pause vindicates itself, when a cold-read in the morning catches a bug that would have shipped, pin that too, so the case for the discipline renews on concrete evidence rather than remembered belief.

The evidence keeps the discipline alive. Without it, "pause when tired" degrades into "get more done" the first time compute costs come up in a planning meeting. With it, "here is a bug caught by pausing" is a specific answer to a specific question. The evidence is what makes the discipline durable.
