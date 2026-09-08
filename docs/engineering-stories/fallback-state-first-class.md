# Fallback state as a first-class diagnostic field

*The peer field that makes a defensive default declare which path it took*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

NovoMCP applies a single convention to a diagnostic that carries a defensive default: the value travels with a peer field that names the path it came from. Provenance arrives in the same return record, and a consumer reads the source before it trusts the number. A verdict harness that requires a locked measurement raises the moment a fallback fires. The pattern is one string field, and it converts a silent fallback into a caught one.

The mechanism is general. Any code path with a defensive default is a candidate for a peer field that reports which path was taken. This is the standard NovoMCP applies as it hardens each fallback-capable return point. Adoption is rolling, not universal: some diagnostics, `analyze_optimization_trajectory` among them, do not expose the field today. A consumer checks for the provenance field where a diagnostic documents it, rather than assuming every return record carries one.

---

## The situation

The diagnostic computes a physical property from a molecular dynamics trajectory: the root-mean-square fluctuation of specific backbone atoms across a set of pocket residues. A locked list in a spec document specifies the residue set. Those residues define the pocket geometry the measurement targets.

The function reads, in pseudocode:

```
def compute_pocket_flexibility(trajectory, locked_residues):
    selection = build_selection_string(locked_residues)
    matched_atoms = trajectory.select(selection)

    if len(matched_atoms) < MIN_ATOMS_THRESHOLD:
        # Fallback: pose-dependent, but produces a number
        selection = build_selection_string_from_cutoff(trajectory, cutoff=6.0)
        matched_atoms = trajectory.select(selection)

    rmsf = compute_rmsf(matched_atoms)
    return rmsf
```

The function has a defensive fallback. When the locked residue selection matches too few atoms, for example because the residue numbering on this trajectory diverges from the locked list, the function selects residues within a distance cutoff of the ligand instead. The fallback produces a number. The number is plausible enough that a caller unaware of the fallback would use it.

The fallback earns its place. The locked list is sometimes temporarily wrong during a refactor or during an experiment on a different system, and returning `None` or throwing an exception breaks more callers than the fallback does. The fallback keeps the diagnostic functional in edge cases where the locked list cannot resolve.

The number the fallback returns is a different measurement than the caller expects. The fallback selection is pose-dependent. Two simulations of the same system, with slightly different equilibrated poses, select different residue sets under the cutoff, because residues near the cutoff boundary drift in and out. The fallback number is not comparable across runs. A caller that treats the fallback number as the primary measurement anchors a downstream decision on a non-reproducible quantity.

---

## The pattern that caught it

The fix makes the fallback state visible. The function's return value becomes a small record:

```
{
    "value": 0.886,
    "source": "runtime_cutoff_WARN_nonreproducible",
    "matched_atom_count": 8,
    "primary_selection_matched": 0
}
```

The function returns the number and a field describing which code path produced it. The caller carries no memory of the fallback and no knowledge of the diagnostic's internals. The caller checks whether `source == "locked"`.

Downstream code inspects the source field before trusting the value. Any source other than `"locked"` is refused when the caller requires a locked measurement, or wrapped with a warning when the caller tolerates the fallback. The verdict harness that consumes this number carries a predeclared rule: locked source only, else raise. The fallback fired, the harness read the source string, and the harness raised.

The bug was contained. The two ambiguous numbers stayed out of the downstream decision. The audit of the fallback path surfaced the residue-numbering mismatch that triggered it, the underlying selection code was fixed, and the diagnostic reran. The second run reported source `"locked"`, the numbers made physical sense, and the downstream decision proceeded on real data.

---

## The funnel rule

**Every fallback-capable return value declares its provenance in a peer field.** Any code with an `if X missing use Y` branch carries a candidate for that field. The peer field is small, often a single string. It records which path was taken. Downstream code that cares about the distinction inspects it. Downstream code that does not care ignores it. The distinction is available in the return value instead of erased at the return boundary.

Without the peer field, the return value is a funnel: multiple internal paths converge to a single external value, and downstream code cannot tell which path was taken. Funnels serve the callers who do not care, which is most callers most of the time. Every funnel is also a place where a caller who does care about the path cannot ask.

The peer-field pattern adds one bit, or a few bits, of information to the return value: the provenance of the value. The value itself stays fixed. Callers that ignored the funnel behavior keep ignoring it. Callers that need to distinguish gain the ability to do so, mechanically, in code, from the return record rather than from memory of internals.

---

## Where the pattern generalizes

**Config loading.** A config-loading library that returns `config.database.host` reads from an environment variable, a config file, a secret manager, or a hardcoded default. The value is a string in all four cases. The caller behaves differently depending on the source: refusing to start when the value came from the hardcoded default, or logging a warning when it came from a file where a secret manager was expected. A peer field on the returned value records the source and lets the caller enforce those rules mechanically.

**Feature flags.** A feature flag lookup returns `True` because the flag is enabled for the user, because a lockfile forced the value, or because the flag service was unreachable and the code fell back to a default. Same return value, different provenance. A peer field lets the caller distinguish a user genuinely in the experiment from a fail-open default.

**Database reads with cache layers.** A read that hits a cache, a read that hits the primary database, and a read that fell back to a stale replica return the same shape of value. When freshness matters, a peer field on the value or the query result records the source and lets the caller decide whether to trust the freshness.

**External API responses with local fallbacks.** A service that queries an external API and falls back to a cached response when the API is down returns the same-shaped payload in both cases. A peer field on the response, `"source": "live"` versus `"source": "cache_fallback_2h_stale"`, lets downstream code decide whether the response is fresh enough for its purpose.

**Diagnostics with pose-dependent selections.** The NovoMCP case. Any measurement with a primary path and a fallback path where the fallback is not semantically identical to the primary carries a source field, so callers can tell whether the number they got is the number they asked for.

---

## The design constraint that makes it work

The peer field carries weight only when downstream code inspects it. A source field that no one reads is decorative. Two design commitments make the pattern structural.

**Consumers are required to check.** The downstream code that consumes the value carries, as part of its predeclared behavior, a rule about which source values are acceptable. In the NovoMCP case, the verdict harness rule is: source must be `locked` or the harness raises. The rule is enforced at the boundary between the diagnostic and the harness, independent of the caller's judgment on any given day. Predeclared consumers make the source field structural rather than advisory.

**Fallback state names are informative and stable.** The string `"runtime_cutoff_WARN_nonreproducible"` names the specific fallback path (`runtime_cutoff`), warns about the property that makes the fallback problematic (`nonreproducible`), and holds stable across releases. Consumers grep for the label, enumerate the acceptable sources, and pattern-match. A stable name is what makes downstream code reliable against it. Names carry meaning, and the meaning stays put.

---

## The distinction from logging

A source field and a log line are different instruments, and both belong in the system.

A log line is downstream of the decision. The value has already flowed through the return boundary by the time the log is written. The caller has already made whatever decision it was going to make. Someone reading the log after the fact notices, but a reader of logs is a hope, not a control flow.

The peer field is upstream of the decision. It arrives at the caller with the value, in the same return record, at the same moment. Code that inspects it enforces a rule at the boundary where the value is received. That is a control flow.

Both exist for their own purpose. Logs serve post-hoc analysis when something is wrong. Peer fields serve enforcement at the moment the value is used.

---

## The standard NovoMCP runs

Every diagnostic on the team that carries a fallback path now returns a peer field naming the source. The names are informative and stable. Downstream consumers carry predeclared rules about which sources they accept, enforced in code at the boundary. A fallback fire produces a value plus a name for the path that produced it, and the name determines what happens next.

The change is a tuple instead of a scalar at every fallback-capable return point, and a check at every consumer boundary. It buys visibility of fallback paths at the moment they matter, ahead of any log line, and ahead of any experiment anchored on the wrong number.
