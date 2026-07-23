# Verification doesn't transitively propagate

*Two bugs, same shape, and the meta-rule we now check against*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

We caught two bugs in the same molecular dynamics project inside a few weeks of each other. They looked unrelated: one was in topology-editing code, the other in a diagnostic that reads simulation output. Different files, different code paths, different tools. But when we sat with them, they were the same bug.

Both were instances of a class we hadn't named before, and once we named it, we started finding more instances in other systems. This is what the class looks like, why it's easy to make, and the rule we now check for.

---

## Bug one: the moleculetype-scoping defect

We had a function that marked specific atoms in a GROMACS topology file. The topology needed to be preprocessed by the standard `grompp -pp` command, which flattens included files into a single self-contained `.top`. The flattened output contains multiple `[moleculetype]` blocks, one for the protein, one for the water model, one for the ions.

The marking function took a list of residue numbers and marked the atoms belonging to those residues. It read the `[atoms]` sections of the topology, matched atoms by residue number, and applied the marking.

The function had a unit test. The test built a small fixture topology with a single protein moleculetype block, called the marking function with a residue list, and asserted that the correct atoms were marked. The test passed. The function shipped. And then we started running it on real solvated systems.

Real solvated systems have thousands of water molecules. Water molecules, in GROMACS's numbering scheme within a `[moleculetype]` block, are numbered *per-moleculetype*, they start at residue 1 within the water block. So a water molecule can have the same "residue number" as a protein residue, when both are read as integers.

The marking function was matching on residue number *across all `[moleculetype]` blocks*. If the target residue list included residue 930 (a specific pocket residue on the protein), the function would also mark water molecules whose per-block residue number happened to be 930. On a system with 5000 water molecules, that's a real number of wrongly-marked atoms.

The unit test never caught it because the fixture had only one block. The bug was invisible until the function was run on a fixture with the *structure* of a real solvated topology, multiple blocks, per-block numbering conventions, resnr collisions across blocks.

We fixed the marking function to be block-scoped (match by moleculetype name first, then by residue number within that block). We shipped the fix with a new regression fixture: a multi-block topology with a deliberate resnr collision between the protein pocket and a water molecule, and an assertion that only the protein-block atom got marked. The old test would have shipped the bug forever; the new test fails immediately if the block-scoping regresses.

Then we did what turned out to be the important step. We asked: which other functions in the pipeline assume "residue numbers are unique across the topology"? Two other primitives touched the same topology data. We audited both against the same lens. Both were clean, one operated on atoms already marked by the fixed function, the other read residue names from a different data source that wasn't per-block-scoped in the first place.

The bug was one instance. The lens was the discovery.

---

## Bug two: the residue-numbering-across-artifacts defect

A few weeks later, on the same project, we were running validation experiments that used a physical diagnostic, a measurement computed from the trajectory of a simulation. The diagnostic selected a set of pocket residues by residue number and computed a property (backbone flexibility) over those residues.

The residue selection was hardcoded to a specific range from the crystal structure: residues 901 through 1041. That range was locked into a spec document, carefully chosen, reviewed, and pinned so the diagnostic would be reproducible across runs.

We ran two reference experiments to calibrate a downstream gate. Both experiments completed cleanly. The MBAR analysis converged. The diagnostic returned numbers. And the numbers were physically nonsensical, a restrained system showed *more* flexibility than an unrestrained system, which is physically backwards.

The diagnostic had a fallback path. If the primary residue selection matched fewer than a threshold number of atoms, the code fell back to a runtime cutoff (pocket residues within some angstrom cutoff of the ligand, computed at analysis time). That fallback produced a *number*, but a pose-dependent, non-reproducible one. And critically, the fallback surfaced its state through a peer field, a `source` string on the metric that said "runtime_cutoff_WARN_nonreproducible", which is how we caught it.

The root cause was worth naming clearly. The trajectory file that the diagnostic reads (a `.tpr`, GROMACS's compiled run input, loaded via MDAnalysis) uses a different residue numbering scheme than the processed topology that the earlier bug had been about. The crystal numbering, residues 901 through 1041, was preserved by `pdb2gmx` in the processed topology (we had verified this). It was silently renumbered to a 1-based scheme by `grompp` when it built the `.tpr`. MDAnalysis, reading the `.tpr`, saw residues 1 through N. The locked selection resolved to nothing. The fallback fired. The diagnostic ran on a completely different residue set on the two experiments (because the fallback was pose-dependent), and the resulting numbers had no meaning.

---

## The shape both bugs share

Both bugs are the same. In the first, the property we verified was "residue numbering is unique on this topology", verified on a single-block fixture, silently assumed to hold on the multi-block real topology. In the second, the property we verified was "the crystal numbering 901–1041 is preserved by pdb2gmx", verified on the processed topology, silently assumed to hold on the `.tpr`.

In both cases, verification of a property was performed on one artifact and then implicitly extended to a downstream artifact that derived from the first. In both cases, the derivation broke the property. In both cases, no one had asked the question "does this property hold on the artifact I'm now reading?", they had inherited the check from a sibling artifact.

**Verification of a property on one artifact does not verify the property on any other artifact, even derived ones.**

Every site the assumption reaches needs its own check. If your code reads residue numbers from a topology, verify the numbering on that topology. If your code reads residue numbers from a `.tpr`, verify the numbering on that `.tpr`. If your code reads residue numbers from a PDB written by a different program, verify the numbering on that PDB. The fact that `pdb2gmx` preserves numbering does not tell you what `grompp` does. The fact that `grompp` preserves numbering does not tell you what MDAnalysis's TPR parser does. Each tool in the pipeline is free to renumber, and any of them might.

---

## Why the meta-rule is worth naming

The meta-rule sounds almost trivially obvious when stated. Nobody would defend the position "I verified property P on artifact A, therefore P holds on artifact B derived from A." Nobody would write that down.

But that is exactly what people do in practice, because the alternative, verifying every property on every artifact it passes through, sounds like exhausting engineering pedantry. The moment you have a verified property in your head, you stop asking whether it needs re-verification, because re-verifying feels like work you already did.

The class we're describing is a mental shortcut, not a design decision. Nobody chooses to inherit the check. They just don't think to re-verify, because the property is already "known."

The meta-rule is worth naming because naming it converts the shortcut into something you can catch in code review. Once "verification doesn't transitively propagate" is a phrase the team knows, someone will read a piece of code that reads a residue number from a new source and ask: *does anyone know what the numbering convention is on this source?* If the answer is "yeah, `pdb2gmx` preserves it," the next question is: *and is this the artifact `pdb2gmx` produces?* The rule lives in that follow-up question.

---

## What this looks like in practice

We now do three things differently.

**Per-artifact numbering probes.** Any diagnostic or code path that reads residue numbers from a new artifact type ships with a probe that empirically confirms the numbering scheme on that specific artifact. Not "the tool that produced it preserves numbering", that's a claim about the tool, not the artifact. An assertion, in code, that loads a real artifact and checks the residue range against expected. The probe is a test, run in CI on a fixture with the same shape as production data.

**Fallback source fields.** Every diagnostic that can silently fall back to a default exposes the fallback state as a first-class field. This is the pattern that caught the second bug, the `source` string said "runtime_cutoff_WARN_nonreproducible" and the downstream analysis refused to interpret the number. Without the source field, we would have anchored a downstream gate on garbage numbers and never known. This pattern is worth its own writeup; we've done it separately.

**Regression fixtures that reproduce the class, not the instance.** When we fixed the moleculetype-scoping bug, the regression fixture had a deliberate collision between protein and water numbering, not just "test the marking function on a multi-block topology." The specific defect could recur in a hundred different ways; the fixture exercises the *class* of defect. Same for the residue-numbering fix, the probe doesn't just check that the current expected range resolves, it checks that a probe query returns nonzero atoms on a *real* trajectory, not a fixture-shaped mock.

---

## Where else this shows up

Once you have the lens, you see it everywhere. A few instances from other systems on our team:

- **Config parsing across environment boundaries.** A config value verified to be JSON-parseable in the staging environment (where a lint check ran) turned out to be YAML-parseable but not JSON-parseable when read by a different service in the production environment. The verification held on the artifact the lint saw; a different service, reading a different (rendered) artifact, ran into a subtle difference in how the YAML renderer serialized a specific value.

- **Timestamp formats across storage layers.** A timestamp verified to be ISO 8601 by the writer service, stored in a database, and read by a downstream service, where the database driver silently coerced the string to a different timezone convention on read. The writer verified. The reader assumed. The middle layer changed the semantics.

- **Cache-key hashing across service versions.** A hash function verified stable in one service version, used to compute a cache key. A downstream service upgraded to a version of the same library with a different hash implementation. Same input, same function name, different bytes. Verification of the hash function's stability on version A did not verify stability across versions.

Each of these is the same class as the two GROMACS bugs. Verification on one thing, silent inheritance to another.

---

## The line that codifies it

We now have a phrase on the team: **"Verification doesn't transitively propagate."** It appears in code review comments. It appears in design docs when someone is about to write "we already verified X." It appears when someone is designing a pipeline stage that reads from a new source.

The phrase does one thing: it prompts the follow-up question. "Where has this property been verified, and is that the artifact I'm reading?" The answer might be yes. If it's yes, we say so out loud, ideally with a link to the check. If it's no, we add a check.

That's the whole discipline. A named class, a follow-up question, and the willingness to add checks on artifacts that felt like they inherited them from siblings. It costs a small amount of engineering vigilance and prevents an entire category of bug that ships silently because everyone thinks someone else verified it.

The two bugs we caught cost us the discipline. What we bought is the ability to catch the third instance in code review, without spending the runs.
