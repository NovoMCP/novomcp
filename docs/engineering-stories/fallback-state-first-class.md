# Fallback state as a first-class diagnostic field

*A single string field that turned a silent bug into a caught one*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

We nearly locked a downstream decision gate against a number that was measured wrong. The measurement completed. The value looked plausible. Two independent runs produced two similar-looking numbers. The math checked out. And the numbers had nothing to do with what we thought they were measuring.

The reason we caught it, instead of anchoring a follow-on experiment on garbage, was that the diagnostic emitting the number had a peer field called `source`. That field said `runtime_cutoff_WARN_nonreproducible` instead of `locked`. We looked at the source, refused to trust the number, and audited the fallback path that had fired silently.

The pattern is small, mechanical, and, once you've felt it save you once, obviously correct. Any code path with a defensive default is a candidate for a peer field that reports which path was taken.

---

## The situation

The diagnostic in question computes a physical property from a molecular dynamics trajectory: the root-mean-square fluctuation of specific backbone atoms across a set of pocket residues. The residue set is specified by a locked list in a spec document, carefully chosen residues that define the pocket geometry we care about.

The function that runs the diagnostic looks something like this, in pseudocode:

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

The function has a defensive fallback. If the locked residue selection matches too few atoms, say, because the residue numbering on this trajectory doesn't match the locked list, it falls back to selecting residues within a distance cutoff of the ligand. That fallback produces a number, and the number is plausible enough that a caller who didn't know about the fallback would use it.

The fallback exists for a good reason. Sometimes the locked list is temporarily wrong (during a refactor, during an experiment on a different system), and returning `None` or throwing an exception would break more callers than the fallback does. The fallback is genuinely defensive: it keeps the diagnostic functional in edge cases where the locked list can't resolve.

The problem is that the number the fallback returns is *not the number the caller expects*. The fallback selection is pose-dependent. Two different simulations of the same system, with slightly different equilibrated poses, will select different sets of residues under the cutoff, because residues near the cutoff boundary drift in and out. The fallback produces a number, but the number is a different measurement than the primary path produces, and comparing fallback numbers across runs is comparing apples to oranges.

If the caller uses the fallback number as if it were the primary measurement, the caller anchors a downstream decision on a non-reproducible quantity. That's what would have happened to us if we hadn't caught it.

---

## The pattern that caught it

The fix was to make the fallback state visible. The function's return value became a small record:

```
{
    "value": 0.886,
    "source": "runtime_cutoff_WARN_nonreproducible",
    "matched_atom_count": 8,
    "primary_selection_matched": 0
}
```

Instead of returning just the number, it returns the number *and* a field describing which code path produced it. The caller doesn't have to remember that a fallback exists. The caller doesn't have to know the internals of the diagnostic. The caller just has to check whether `source == "locked"`.

Downstream code inspects the source field before trusting the value. If the source is anything other than `"locked"`, the number is either refused (if the caller needs a locked measurement) or wrapped with a warning (if the caller can tolerate the fallback). The verdict harness that would have consumed this number had a predeclared rule: locked source only, else raise. When the fallback fired and the harness saw the source string, it raised.

The bug was contained. The two ambiguous numbers didn't corrupt the downstream decision. We audited the fallback path, discovered the residue-numbering mismatch that had triggered it, fixed the underlying selection code, and reran the diagnostic. The second time, the source string said `"locked"`, the numbers made physical sense, and the downstream decision proceeded on real data.

---

## Why this is more than a bug fix

The specific bug, a residue-numbering mismatch triggering a silent fallback, is a story about one particular diagnostic. The pattern that caught it generalizes.

Any code that has an `if X missing use Y` branch has a candidate for a peer field. The peer field is small: often a single string. The peer field records which path was taken. Downstream code that cares about the distinction can inspect it. Downstream code that doesn't care can ignore it. But the distinction is now available, instead of being erased at the return boundary.

Without the peer field, the return value is a *funnel*: multiple internal paths converge to a single external value, and the downstream code can't tell which path was taken. Funnels are convenient for callers who don't care, most of them, most of the time. But every funnel is a place where a caller who *does* care about the path can't ask.

The peer-field pattern adds one bit (or a few bits) of information to the return value: the provenance of the value. It doesn't change the value itself. Callers that ignored the funnel behavior can keep ignoring it. Callers that need to distinguish gain the ability to do so, mechanically, in code, not by remembering internals.

---

## Where the pattern generalizes

**Config loading.** A config-loading library that returns `config.database.host` might read from an environment variable, from a config file, from a secret manager, or from a hardcoded default. The value is a string in all four cases. The caller might behave differently depending on the source, for example, refusing to start if the value came from the hardcoded default, or logging a warning if it came from a file when a secret manager was expected. A peer field on the returned value that records the source lets the caller enforce those rules mechanically.

**Feature flags.** A feature flag lookup might return `True` because the flag is enabled for the user, because a lockfile forced the value, because the flag service was unreachable and the code fell back to a default. Same return value, different provenance. A peer field lets the caller distinguish "user is genuinely in the experiment" from "we failed open."

**Database reads with cache layers.** A read that hits a cache, a read that hits the primary database, and a read that fell back to a stale replica all return the same shape of value. If freshness matters, a peer field on the value (or on the query result) that records the source lets the caller decide whether to trust the freshness.

**External API responses with local fallbacks.** A service that queries an external API and falls back to a cached response when the API is down returns the same-shaped payload in both cases. A peer field on the response, `"source": "live"` versus `"source": "cache_fallback_2h_stale"`, lets downstream code decide whether the response is fresh enough for its purposes.

**Diagnostics with pose-dependent selections.** Our case. Any measurement that has a primary path and a fallback path where the fallback isn't semantically identical to the primary needs a source field. Otherwise callers can't tell whether the number they got is the number they asked for.

---

## The design constraint that makes it work

The peer field only works if downstream code actually inspects it. A source field that no one reads is decorative.

Two design commitments make the pattern effective:

**Consumers are required to check.** The downstream code that consumes the value has, as part of its predeclared behavior, a rule about which source values are acceptable. In our case: the verdict harness's rule was "source must be `locked` or we raise." That rule is enforced at the boundary between the diagnostic and the harness, not left to the caller's judgment on any given day. Predeclared consumers make the source field structural, not advisory.

**Fallback state names are informative and stable.** The string `"runtime_cutoff_WARN_nonreproducible"` is not a random label. It names the specific fallback path (`runtime_cutoff`), warns about the property that makes the fallback problematic (`nonreproducible`), and is stable across releases. Consumers can grep for the label, can enumerate the acceptable sources, can pattern-match. If the string changed every release, downstream code couldn't be written to rely on it. Names carry meaning; meaning stays put.

---

## The distinction from logging

Someone will read this and say: "we already do this with logs. When the fallback fires, we log a warning."

Logs are downstream of the decision. The value has already flowed through the return boundary by the time the log line is written. The caller has already made whatever decision it was going to make. Someone reading the log after the fact might notice, but "someone reading logs" is not a control flow, it's a hope.

The peer field is upstream of the decision. It arrives at the caller *with the value*, in the same return record, at the same moment. Code that inspects it is enforcing a rule at the boundary where the value is received. That's the difference between a control flow and a hope.

Both should exist. Logs are for post-hoc analysis when something is wrong. Peer fields are for enforcement at the moment the value is used. They're not substitutes for each other.

---

## What we changed

Every diagnostic on the team that has a fallback path now returns a peer field naming the source. The names are informative and stable. Downstream consumers of the diagnostic have predeclared rules about which sources they accept, enforced in code at the boundary. Fallback fires no longer produce a value that flows silently downstream, they produce a value plus a name for the path that produced it, and the name is what determines what happens next.

The change is not architecturally sophisticated. It's a tuple instead of a scalar at every fallback-capable return point, and a check at every consumer boundary. What it buys is that fallback paths become visible at the moment they matter, not in a log line that nobody reads until an experiment has already been anchored on the wrong number.

Which, one time, was almost us. Once was enough.
