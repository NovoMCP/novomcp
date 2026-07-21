# Spec is the source of truth; code reads from it

*Why our validation harness parses its thresholds from a markdown file at runtime*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

The validation harness for one of our computational-physics pipelines reads its pass/fail thresholds from a markdown document at runtime. Not from a Python constants file. Not from environment variables. From a specific `.md` file whose content includes tables of predeclared criteria, and whose format is stable enough for a small parser to extract them.

This decision surprises people. A markdown spec is not a robust configuration format. It's a document meant for human readers, not for parse-time consumption. The tradeoff we made, a runtime dependency on the document's format, is real.

We made the tradeoff on purpose. This is why.

---

## The problem it solves

Any system that couples a specification document to code has an invisible failure mode: the specification and the code drift.

The specification says the threshold is 0.5. The code says the threshold is 0.4. The code was correct when it was written; the spec was updated later and no one updated the constant. Or the spec was correct when it was written; the constant was changed for an experiment and never reverted. Or someone amended the spec, the reviewer approved the amendment, and the code was supposed to change too but nobody remembered to file the second PR.

The failure mode isn't that the drift happens, of course drift happens. The failure mode is that the drift is *silent*. The code runs. The tests pass. The output is a number that looks fine. The spec document, read by a human next month, describes a system that doesn't quite exist. The code, running in production, enforces rules that no one has re-reviewed since the last amendment.

Two documents claim to describe the same rules. When they disagree, neither knows about the disagreement. That's the failure mode.

---

## The pattern

The fix is structural: pick which document is authoritative, and make the other one derive from it, mechanically, so drift becomes impossible.

For our validation spec, we picked the markdown document. Two reasons.

**The spec is where the human review happens.** When a criterion changes, a threshold moves from 0.5 to 0.4, the review is on the markdown document, not the constants file. The reviewer reads the document, notices the change, asks whether it's justified, and approves. If the code carried the authoritative version, the review would have to happen on a Python file, which is less natural and (in our experience) less rigorous. Text tables in a document invite the kind of "does this number make sense given the previous numbers?" scrutiny that scattered Python constants don't.

**The spec has structure the code doesn't.** The markdown document includes the pass/fail thresholds *and* the rationale for each threshold, the reservations attached to specific rows, the decision-trail table showing what was locked when and by whom. That's the audit trail. Putting the authoritative values in a Python file would either lose the audit trail (constants alone) or split it (constants in code, audit in doc, with the drift problem back).

So the spec is authoritative. The code reads from the spec.

At import time, the harness parses the markdown file, extracts the tables of criteria, and loads the values into memory. The parser is small, maybe 60 lines. It reads a specific header ("## Predeclared criteria"), finds the tables beneath, and reads the rows into a dictionary keyed by row ID.

The rest of the harness references thresholds by row ID:

```python
if metric > spec.get_threshold("B4"):
    row_result = "FAIL"
```

Not by hardcoded constants. Every threshold, every gate structure, every pass/fail boundary lives in the spec. The code contains the *logic*, which metric to compute, when to apply which threshold, how to aggregate results, and reads the *values* from the spec at runtime.

---

## What happens when the spec changes

When someone amends the spec, they update the markdown document. That's the entire workflow. The next time the harness runs, it parses the updated document, sees the new thresholds, and enforces them.

There is no second PR. There is no risk of forgetting to update the code. The code doesn't have the values in the first place, so the code cannot drift from them.

The reviewer of the amendment doesn't have to remember to check that the code will pick up the change. Structurally, the code will, that's the only source it reads from.

The failure mode we were guarding against, spec says one thing, code enforces another, is now impossible in the direction that mattered to us. If the spec is wrong, the code is wrong the same way, and the reviewer of the spec is the reviewer of the code's behavior. One review, one source, one truth.

---

## What can still go wrong

The pattern has failure modes. They're not the ones we were guarding against, and they're contained.

**Parse-time errors.** If someone amends the spec in a way that breaks the parser, puts a table where the parser doesn't expect one, changes a header, the harness fails to load. This is by design: a parse error is preferable to running with a stale in-memory value or a default. But it means every spec amendment implicitly touches parser-fragile territory, and edits that look purely editorial (renaming a section header) can break the harness.

Two mitigations. First, the parser is deliberately simple, it looks for specific header strings and specific table columns, and it fails with a clear error message that names what it was looking for. When it breaks, the fix is usually obvious ("you renamed the section, rename it back or update the parser"). Second, the harness's CI includes a parse-only run on every PR that touches the spec, so the parser's expectations are checked against the current spec on every change.

**Semantic errors in the spec.** If someone writes the wrong number in the spec, types 0.05 when they meant 0.5, the harness will happily enforce the wrong number. The pattern doesn't protect against typos in the spec; it only protects against the spec and code disagreeing. The mitigation is code review of the spec, same as any authoritative document.

**Complex logic that doesn't fit in a table.** Some behavior can't be expressed as a threshold. Conditional logic ("if the primary metric is in range X, apply threshold Y; otherwise apply threshold Z") lives in code, not in the spec. The spec references the *decision* by name, and the code implements the decision. This is where the pattern shows its seams: not everything is a value, some things are logic, and logic that lives in code has all the usual drift problems if the spec's description of the logic changes and the code doesn't.

Our compromise: the spec describes the logic in prose, and a companion section in the code has a one-line reference back to the spec section for each branch. When someone amends the logic, they amend both. Drift can still happen in that layer. It's a narrower surface than "everything is a constant in code," but it's not zero.

---

## Where else this pattern shows up

The pattern is general. Any time a specification document and code both encode the same values, one of them should be authoritative, and the other should derive.

**Config-as-code.** Kubernetes manifests are a familiar instance. The manifest is authoritative; the cluster derives its state from the manifest. Applying a manifest is the mechanism that makes drift impossible in the direction that matters (declared state → actual state). Manual `kubectl edit` sessions defeat this, they let the actual state drift from the declared state, which is why the discipline of "always edit the manifest, never the cluster" exists.

**Feature-flag lockfiles.** A feature-flag service that computes flag values from a lockfile (rather than a mutable admin UI) makes the lockfile authoritative. Reviewers of a flag change review the lockfile; the service reads from the lockfile at request time; there's no admin UI where a flag can be silently changed. This is the same pattern applied to flag configuration.

**Database migrations.** Migration files are authoritative; the database schema derives from applied migrations. A schema change requires a migration file. Direct `ALTER TABLE` from a psql session defeats this, which is why every production database eventually enforces "no direct DDL, migrations only." Same pattern, same discipline.

**API specifications.** OpenAPI specifications, when they're generated from code annotations, make the code authoritative. When they're hand-written and the server implements against them, the spec is authoritative. Which one is right depends on where the human review happens. The pattern requires a choice, and the choice must be honored, hybrid ("both are authoritative") is where drift lives.

**Locked-experiment specifications.** Our specific case. The predeclared thresholds live in a document that gets human review; the code implements the logic and reads the thresholds. This is a research-engineering variant of the general pattern.

---

## The discipline the pattern encodes

The deeper point is not the mechanism (parsing a markdown file) but the discipline it encodes: *one document is authoritative; every other representation derives.*

Systems that don't make this choice explicit end up with distributed authority. The spec kind of says one thing, the code kind of says another, and reconciliation happens by human memory, which is exactly where drift lives, because humans forget. The reason the pattern works is that it removes human memory from the reconciliation loop. Once the code reads from the spec, no one needs to remember to keep them in sync.

That's not sophisticated. It's structural. And structural discipline beats vigilance every time, because vigilance decays and structure doesn't.

Our validation harness is a small example. The general pattern applies wherever you're maintaining two documents that claim to describe the same rules. Pick one. Make the other derive. Stop trusting anyone (including yourself) to keep them aligned.
