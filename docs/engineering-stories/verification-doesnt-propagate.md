# Verification doesn't transitively propagate

*One named class, two GROMACS bugs, and the check the engine now runs*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

The NovoMCP pipeline verifies a property on the specific artifact its code reads, not on the artifact upstream that produced it. Verification of a property on one artifact does not verify the property on any other artifact, even a derived one. This is a named class: verification doesn't transitively propagate. Naming it converts a mental shortcut into a question the team asks in code review, and the question catches the next instance before it ships.

Two bugs in one molecular dynamics project, caught within a few weeks of each other, established the class. They looked unrelated. One lived in topology-editing code, the other in a diagnostic that reads simulation output. Different files, different code paths, different tools. They were the same bug. Once the class was named, more instances surfaced across other systems.

---

## Bug one: the moleculetype-scoping defect

A function marked specific atoms in a GROMACS topology file. The topology was preprocessed by the standard `grompp -pp` command, which flattens included files into a single self-contained `.top`. The flattened output contains multiple `[moleculetype]` blocks: one for the protein, one for the water model, one for the ions.

The marking function took a list of residue numbers and marked the atoms belonging to those residues. It read the `[atoms]` sections of the topology, matched atoms by residue number, and applied the marking.

The function had a unit test. The test built a small fixture topology with a single protein moleculetype block, called the marking function with a residue list, and asserted that the correct atoms were marked. The test passed. The function shipped. Then it ran on real solvated systems.

Real solvated systems have thousands of water molecules. In GROMACS's numbering scheme within a `[moleculetype]` block, water molecules are numbered per-moleculetype: they start at residue 1 within the water block. A water molecule can carry the same residue number as a protein residue when both are read as integers.

The marking function matched on residue number across all `[moleculetype]` blocks. A target list including residue 930, a specific pocket residue on the protein, also marked water molecules whose per-block residue number happened to be 930. On a system with 5000 water molecules, that is a real number of wrongly-marked atoms.

The unit test never caught it because the fixture had one block. The bug was invisible until the function ran on a fixture with the structure of a real solvated topology: multiple blocks, per-block numbering conventions, resnr collisions across blocks.

The fix made the marking function block-scoped: match by moleculetype name first, then by residue number within that block. The fix shipped with a new regression fixture, a multi-block topology with a deliberate resnr collision between the protein pocket and a water molecule, and an assertion that only the protein-block atom got marked. The old test would have shipped the bug forever. The new test fails immediately if the block-scoping regresses.

Then came the step that mattered. Which other functions in the pipeline assume residue numbers are unique across the topology? Two other primitives touched the same topology data. Both were audited against the same lens. Both were clean: one operated on atoms already marked by the fixed function, the other read residue names from a different data source that was not per-block-scoped in the first place.

The bug was one instance. The lens was the discovery.

---

## Bug two: the residue-numbering-across-artifacts defect

A few weeks later, on the same project, validation experiments used a physical diagnostic, a measurement computed from the trajectory of a simulation. The diagnostic selected a set of pocket residues by residue number and computed backbone flexibility over those residues.

The residue selection was hardcoded to a specific range from the crystal structure: residues 901 through 1041. That range was locked into a spec document, carefully chosen, reviewed, and pinned so the diagnostic would be reproducible across runs.

Two reference experiments ran to calibrate a downstream gate. Both completed cleanly. The MBAR analysis converged. The diagnostic returned numbers. The numbers were physically nonsensical: a restrained system showed more flexibility than an unrestrained system, which is physically backwards.

The diagnostic had a fallback path. When the primary residue selection matched fewer than a threshold number of atoms, the code fell back to a runtime cutoff, pocket residues within an angstrom cutoff of the ligand, computed at analysis time. That fallback produced a number, but a pose-dependent, non-reproducible one. The fallback surfaced its state through a peer field, a `source` string on the metric reading `runtime_cutoff_WARN_nonreproducible`, which is how the bug surfaced.

The root cause names cleanly. The trajectory file the diagnostic reads, a `.tpr`, GROMACS's compiled run input, loaded via MDAnalysis, uses a different residue numbering scheme than the processed topology the earlier bug concerned. The crystal numbering, residues 901 through 1041, was preserved by `pdb2gmx` in the processed topology, verified. It was silently renumbered to a 1-based scheme by `grompp` when it built the `.tpr`. MDAnalysis, reading the `.tpr`, saw residues 1 through N. The locked selection resolved to nothing. The fallback fired. The diagnostic ran on a completely different residue set on the two experiments, because the fallback was pose-dependent, and the resulting numbers had no meaning.

---

## The shape both bugs share

Both bugs are the same. In the first, the verified property was "residue numbering is unique on this topology," verified on a single-block fixture, silently assumed to hold on the multi-block real topology. In the second, the verified property was "the crystal numbering 901-1041 is preserved by pdb2gmx," verified on the processed topology, silently assumed to hold on the `.tpr`.

In both cases, verification of a property was performed on one artifact and implicitly extended to a downstream artifact derived from the first. In both cases, the derivation broke the property. In both cases, the question "does this property hold on the artifact I am now reading?" went unasked. The check was inherited from a sibling artifact.

**Verification of a property on one artifact does not verify the property on any other artifact, even derived ones.**

Every site the assumption reaches gets its own check. Code that reads residue numbers from a topology verifies the numbering on that topology. Code that reads residue numbers from a `.tpr` verifies the numbering on that `.tpr`. Code that reads residue numbers from a PDB written by a different program verifies the numbering on that PDB. That `pdb2gmx` preserves numbering says nothing about what `grompp` does. That `grompp` preserves numbering says nothing about what MDAnalysis's TPR parser does. Each tool in the pipeline is free to renumber, and any of them might.

---

## The meta-rule is named because the shortcut is invisible

The meta-rule reads as trivially obvious when stated. No one defends the position "I verified property P on artifact A, therefore P holds on artifact B derived from A." No one writes that down.

That is exactly what happens in practice, because the alternative, verifying every property on every artifact it passes through, reads as exhausting engineering pedantry. A verified property in the head stops the question of whether it needs re-verification, because re-verifying feels like work already done.

The class is a mental shortcut, not a design decision. No one chooses to inherit the check. They do not think to re-verify, because the property is already known.

**Naming the class converts the shortcut into something catchable in code review.** Once "verification doesn't transitively propagate" is a phrase the team knows, a reader of code that reads a residue number from a new source asks: does anyone know the numbering convention on this source? If the answer is "yes, `pdb2gmx` preserves it," the next question is: and is this the artifact `pdb2gmx` produces? The rule lives in that follow-up question.

---

## What the engine now runs

Three things run differently.

**Per-artifact numbering probes.** Any diagnostic or code path that reads residue numbers from a new artifact type ships with a probe that empirically confirms the numbering scheme on that specific artifact. Not "the tool that produced it preserves numbering," which is a claim about the tool, not the artifact. An assertion, in code, that loads a real artifact and checks the residue range against expected. The probe is a test, run in CI on a fixture with the same shape as production data.

**Fallback source fields.** Every diagnostic that can silently fall back to a default exposes the fallback state as a first-class field. This is the pattern that caught the second bug: the `source` string read `runtime_cutoff_WARN_nonreproducible` and the downstream analysis refused to interpret the number. Without the source field, a downstream gate would have anchored on garbage numbers unnoticed. This pattern carries its own writeup, done separately.

**Regression fixtures that reproduce the class, not the instance.** The moleculetype-scoping fixture carries a deliberate collision between protein and water numbering, not just a multi-block topology under test. The specific defect could recur in a hundred different ways; the fixture exercises the class of defect. Same for the residue-numbering fix: the probe checks that a probe query returns nonzero atoms on a real trajectory, not that the current expected range resolves on a fixture-shaped mock.

---

## The class recurs across systems

Once the lens exists, it appears everywhere. Instances from other systems on the team:

- **Config parsing across environment boundaries.** A config value verified JSON-parseable in the staging environment, where a lint check ran, turned out YAML-parseable but not JSON-parseable when read by a different service in production. The verification held on the artifact the lint saw. A different service, reading a different rendered artifact, hit a subtle difference in how the YAML renderer serialized a specific value.

- **Timestamp formats across storage layers.** A timestamp verified ISO 8601 by the writer service, stored in a database, and read by a downstream service, where the database driver silently coerced the string to a different timezone convention on read. The writer verified. The reader assumed. The middle layer changed the semantics.

- **Cache-key hashing across service versions.** A hash function verified stable in one service version, used to compute a cache key. A downstream service upgraded to a version of the same library with a different hash implementation. Same input, same function name, different bytes. Verification of the hash function's stability on version A did not verify stability across versions.

Each is the same class as the two GROMACS bugs. Verification on one thing, silent inheritance to another.

---

## The line that codifies it

The team runs one phrase: **"Verification doesn't transitively propagate."** It appears in code review comments. It appears in design docs when someone is about to write "we already verified X." It appears when someone designs a pipeline stage that reads from a new source.

The phrase prompts the follow-up question: where has this property been verified, and is that the artifact I am reading? The answer might be yes. When it is yes, the team says so out loud, ideally with a link to the check. When it is no, the team adds a check.

That is the whole discipline: a named class, a follow-up question, and the willingness to add checks on artifacts that felt like they inherited them from siblings. It costs a small amount of engineering vigilance and prevents an entire category of bug that ships silently because everyone thinks someone else verified it.

The two bugs bought the class. What the class buys is the ability to catch the third instance in code review, without spending the runs.
