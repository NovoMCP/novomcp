# Spec is the source of truth; code reads from it

*The validation harness parses its thresholds from a markdown file at runtime*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

The NovoMCP validation harness reads its pass/fail thresholds from a markdown document at runtime. Not from a Python constants file. Not from environment variables. From a specific `.md` file whose content includes tables of predeclared criteria, in a format stable enough for a small parser to extract. One document is authoritative. Every other representation derives from it. Drift between spec and code becomes impossible in the direction that matters.

The tradeoff is real and deliberate: a runtime dependency on the document's format, taken on purpose. This is the pattern, the drift it removes, its failure modes, and where else it applies.

---

## The drift it removes

Any system that couples a specification document to code carries an invisible failure mode: the specification and the code drift.

The specification says the threshold is 0.5. The code says the threshold is 0.4. The code was correct when written; the spec was updated later and the constant was not. Or the spec was correct when written; the constant was changed for an experiment and never reverted. Or the spec was amended, the reviewer approved the amendment, the code was supposed to change too, and the second PR was never filed.

The failure mode is not that drift happens. Drift happens. The failure mode is that the drift is silent. The code runs. The tests pass. The output is a number that looks fine. The spec document, read by a human next month, describes a system that does not quite exist. The code, running in production, enforces rules no one has re-reviewed since the last amendment.

Two documents claim to describe the same rules. When they disagree, neither knows about the disagreement.

---

## The pattern: one authoritative document, the rest derived

The fix is structural: pick which document is authoritative, and make the other derive from it mechanically, so drift becomes impossible.

For the validation spec, the markdown document is authoritative. Two reasons.

**The spec is where human review happens.** When a criterion changes, when a threshold moves from 0.5 to 0.4, the review is on the markdown document, not the constants file. The reviewer reads the document, notices the change, asks whether it is justified, and approves. Authoritative values in a Python file would force the review onto a Python file, which is less natural and, in practice, less rigorous. Text tables in a document invite the "does this number make sense given the previous numbers?" scrutiny that scattered Python constants do not.

**The spec carries structure the code does not.** The markdown document includes the pass/fail thresholds and the rationale for each threshold, the reservations attached to specific rows, the decision-trail table showing what was locked when and by whom. That is the audit trail. Authoritative values in a Python file would lose the audit trail with constants alone, or split it into constants in code and audit in doc, which restores the drift problem.

The spec is authoritative. The code reads from the spec.

At import time, the harness parses the markdown file, extracts the tables of criteria, and loads the values into memory. The parser is small, roughly 60 lines. It reads a specific header ("## Predeclared criteria"), finds the tables beneath, and reads the rows into a dictionary keyed by row ID.

The rest of the harness references thresholds by row ID:

```python
if metric > spec.get_threshold("B4"):
    row_result = "FAIL"
```

Not hardcoded constants. Every threshold, every gate structure, every pass/fail boundary lives in the spec. The code contains the logic, which metric to compute, when to apply which threshold, how to aggregate results, and reads the values from the spec at runtime.

---

## What a spec change triggers

An amendment updates the markdown document. That is the entire workflow. The next harness run parses the updated document, sees the new thresholds, and enforces them.

There is no second PR. There is no risk of forgetting to update the code. The code does not hold the values in the first place, so the code cannot drift from them.

The reviewer of the amendment does not have to remember to check that the code will pick up the change. Structurally, the code will. That is the only source it reads from.

The failure mode the pattern guards against, spec says one thing and code enforces another, is now impossible in the direction that mattered. If the spec is wrong, the code is wrong the same way, and the reviewer of the spec is the reviewer of the code's behavior. One review, one source, one truth.

---

## What the pattern still leaves open

The pattern has failure modes. They are not the ones it guards against, and they are contained.

**Parse-time errors.** An amendment that breaks the parser, a table where the parser does not expect one, a changed header, stops the harness from loading. This is by design: a parse error is preferable to running with a stale in-memory value or a default. It also means every spec amendment implicitly touches parser-fragile territory, and edits that look purely editorial, such as renaming a section header, can break the harness. Two mitigations. The parser is deliberately simple: it looks for specific header strings and specific table columns, and it fails with a clear error message naming what it was looking for. When it breaks, the fix is usually obvious. The harness's CI runs a parse-only pass on every PR that touches the spec, so the parser's expectations are checked against the current spec on every change.

**Semantic errors in the spec.** A wrong number in the spec, 0.05 typed for 0.5, gets enforced faithfully. The pattern protects against the spec and code disagreeing, not against typos in the spec. The mitigation is code review of the spec, same as any authoritative document.

**Complex logic that does not fit in a table.** Some behavior cannot be expressed as a threshold. Conditional logic, "if the primary metric is in range X, apply threshold Y, otherwise apply threshold Z," lives in code, not in the spec. The spec references the decision by name, and the code implements the decision. This is the seam: not everything is a value, some things are logic, and logic in code carries the usual drift problems when the spec's description of the logic changes and the code does not. The compromise: the spec describes the logic in prose, and a companion section in the code carries a one-line reference back to the spec section for each branch. An amendment to the logic amends both. Drift can still happen in that layer. It is a narrower surface than "everything is a constant in code," and it is not zero.

---

## Where else the pattern applies

The pattern is general. Any time a specification document and code both encode the same values, one is authoritative and the other derives.

**Config-as-code.** Kubernetes manifests are a familiar instance. The manifest is authoritative; the cluster derives its state from the manifest. Applying a manifest makes drift impossible in the direction that matters, declared state to actual state. Manual `kubectl edit` sessions defeat this by letting actual state drift from declared state, which is why the discipline "always edit the manifest, never the cluster" exists.

**Feature-flag lockfiles.** A feature-flag service that computes flag values from a lockfile, rather than a mutable admin UI, makes the lockfile authoritative. Reviewers of a flag change review the lockfile; the service reads from the lockfile at request time; no admin UI can silently change a flag. Same pattern, applied to flag configuration.

**Database migrations.** Migration files are authoritative; the database schema derives from applied migrations. A schema change requires a migration file. Direct `ALTER TABLE` from a psql session defeats this, which is why every production database eventually enforces "no direct DDL, migrations only." Same pattern, same discipline.

**API specifications.** OpenAPI specifications generated from code annotations make the code authoritative. Hand-written specifications that the server implements against make the spec authoritative. Which one is right depends on where the human review happens. The pattern requires a choice, and the choice must be honored. Hybrid, "both are authoritative," is where drift lives.

**Locked-experiment specifications.** The specific case here. The predeclared thresholds live in a document that gets human review; the code implements the logic and reads the thresholds. This is the research-engineering variant of the general pattern.

---

## The discipline the pattern encodes

The deeper point is not the mechanism, parsing a markdown file, but the discipline it encodes: one document is authoritative; every other representation derives.

Systems that leave this choice implicit end up with distributed authority. The spec kind of says one thing, the code kind of says another, and reconciliation happens by human memory, which is exactly where drift lives, because humans forget. The pattern works because it removes human memory from the reconciliation loop. Once the code reads from the spec, no one needs to remember to keep them in sync.

That is not sophisticated. It is structural. Structural discipline beats vigilance every time, because vigilance decays and structure does not.

The validation harness is a small example. The general pattern applies wherever two documents claim to describe the same rules. Pick one. Make the other derive. Stop trusting anyone, including yourself, to keep them aligned.
