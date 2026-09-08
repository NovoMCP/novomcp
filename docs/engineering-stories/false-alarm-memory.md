# False-alarm resolution as institutional memory

*The resolution of a false alarm is an asset the team keeps*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

NovoMCP keeps the resolution of a false alarm, not just the record that a bug occurred. The team memory doc holds, at last count, about a dozen false-alarm entries alongside the bug fixes and design decisions. Each entry names the alarm, describes what looked wrong, names the resolution, and generalizes the insight into a check to run first next time. The entries have been searched more often than expected, including several times by an entry's original author who had forgotten the resolution and re-derived it faster because the entry existed.

Bug memory records what broke and what fixed it. False-alarm memory records what looked broken but was not, and the check that revealed the truth. Both matter. One is routinely written down and one is routinely lost. NovoMCP writes down both. Two examples from a molecular-dynamics engineering effort in the summer of 2026 establish the pattern.

---

## The false alarm: the 90-minute rebuild

A container image build took 90 minutes when it failed. Successful builds took the normal five minutes. A build that failed partway through restarted from scratch on the next attempt, including the expensive early stages that had been working, and ran a full 90 minutes before failing or succeeding at whatever point had actually broken.

The rebuild cost read as a fact of the pipeline. The compilation stage was expensive, the image was large, and the cost was paid several times while diagnosing the actual issue. The false alarm was the belief that the 90-minute rebuild was a fact of life to budget for.

The resolution came from the pipeline output: failed builds were not exporting the buildx cache. Successful builds cached everything. Failed builds cached nothing. The cache-preservation step sat inside a `success()` conditional, so a failure at minute 45 discarded the cache from minute 0 through 44.

The fix moves the cache-export step out of the `success()` conditional, so failed builds preserve cache through the point of failure. A subsequent build reuses the cached layers up to the point that failed last time and re-runs from there. Typical failure-to-retry time dropped from 90 minutes to under 10.

The entry captures a class of institutional shortcut: **when a slow operation feels like it should be faster, check whether a caching layer is silently disabled for the failure path.** Written down, that question gets asked in front of the next slow-CI loop. Unwritten, the team re-derives it, or budgets for the slow loop and never re-derives it.

A companion shortcut was captured at the same time: **validate the pushed artifact in a cheap ephemeral pod, not by re-triggering the full build.** To debug whether a compiled binary works, run the binary in a small pod that pulls the image and executes a smoke test. The build is upstream of the artifact, the artifact is what needs testing, so test the artifact directly. That habit cost nothing to install and saved a couple of days over the next month, because once said out loud it became the default.

---

## The false alarm: the feature that was present all along

A piece of software supported an enhanced-sampling MD protocol (Hamiltonian replica exchange). The build appeared to lack support for the protocol. Grepping through source, log messages, and thermostat behavior all seemed to confirm the absence. Every check ran to the same conclusion.

Planning for an image rebuild to enable the protocol reached about a day in before a second look at the checks themselves. The `grep` used to detect the feature searched for a symbol absent from the built binary even when the feature is present, because the symbol lives in a source file compiled into the binary but not surfaced as a searchable string. The thermostat-behavior check looked for a log line renamed in a version bump. Each check looked like it probed the right thing, and each was wrong for a different subtle reason.

The feature was present, and had been all along. The tests for detecting it were flawed. The false alarm cost about six hours of investigation and about a day of planning for a rebuild that was never needed. Small in absolute terms, substantial for a project on a timeline.

The resolution generalizes: **when the target seems broken, verify your tests before you verify the target.** Every check run during those six hours probed the wrong artifact or the wrong string. A single explicit question at any point, "is this check itself correct?" instead of "what does this check tell us about the target?", collapses the false alarm in fifteen minutes.

That habit is hard to install. A coherent story about a broken feature pulls toward extending the story, and coherent-but-wrong stories converge fast. The way to catch them is to check the investigation instruments before extending the investigation. Written down, it is a check to add to any debugging workflow: before extending a diagnostic, confirm the diagnostic works. For any grep, the string corresponds to what it is assumed to. For any log-line check, the log line is still emitted by the current version. For any test, the test's failure mode corresponds to what is inferred from it.

---

## The pattern

The two examples share no surface features. One is CI infrastructure, one is molecular dynamics software. Different systems, different failure modes, different fixes. Both end a diagnostic loop with a resolution stated in one sentence: when X seems broken, ask whether Y is misleading you. Neither resolution is a bug fix. Neither appears in a post-mortem. Both are the kind of insight that ends a false alarm cleanly and gets forgotten the next day because there is no bug to link it to.

Bug memory is "here is what broke, here is what fixed it, do not reintroduce the break." False-alarm memory is "here is what looked broken but was not, here is the check that revealed the truth, ask that check next time." The first records what to avoid. The second records what to ask.

---

## The format

False-alarm resolutions live in the same document as regular team memory. The format is short: a title naming the alarm, one paragraph describing what looked wrong, one paragraph naming the resolution, one sentence generalizing the insight. The build-cache entry reads:

> **90-minute failed-build loop turned out to be missing cache preservation.**
> Failed CI builds were rebuilding from scratch because the cache-export step was inside a `success()` conditional. Moving the export out of the conditional dropped failed-retry time from 90 minutes to under 10.
> **Lesson:** when a slow operation feels like it should be faster, check whether a caching layer is silently disabled for the failure path.

Four lines. Anyone on the team, six months later, hitting a slow-CI failure loop searches for "slow CI" or "build cache" and reaches the entry immediately. The lesson generalizes past this one build, so the next slow-and-shouldn't-be operation arrives with the question already queued.

---

## Why the practice compounds

The absence of a written bug fix is visible. Someone reintroduces the bug, the team notices, and the frustration generates pressure to write things down next time. The absence of a written false-alarm resolution is invisible. The same false alarm surfaces in someone else's investigation six months later, that person spends six hours chasing it and closes it as user error, and no shared awareness ever forms that a pattern exists.

Institutional memory that captures only bug fixes is asymmetric. It over-weights failure-with-a-fix and under-weights diagnostic paths that were followed and turned out to be dead ends. Dead ends are useful information: the next investigator skips that path. Dead ends produce an absence of a bug, which is the wrong shape to become a natural artifact, so they go unrecorded unless the team records them deliberately.

The rule scopes what to record. **Write it down when the resolution is a check worth running first next time.** The build-cache case qualifies. The enhanced-sampling case qualifies. **Write it down when the false alarm consumed significant investigation time**, because time is a proxy for how real the alarm looked, and the next investigator will make the same call. **Skip it when the resolution is idiosyncratic to the specific artifact**, because a one-off lookup does not compound. **Skip it when a colleague resolved it in five minutes by noticing the issue in passing**, because a lucky observation teaches gratitude rather than a discipline.

The pattern requires no formal process. It requires a single question at the end of a false-alarm investigation: would writing this down save the next person from repeating the six hours? When yes, the writeup takes two minutes.

---

## What the practice returns

The dozen false-alarm entries are a minority of the memory doc and have been referenced more than expected. The metadata is small and the compounding benefit is real. Once the habit is installed, each entry costs almost nothing to add, and every entry referenced later, even once, pays for the entire practice.

**Negative-result documentation is what stops rediscovery cycles.** Bug memory keeps the team from making the same mistake twice. False-alarm memory keeps the team from doing the same investigation twice. False-alarm memory carries a natural absence-of-artifact problem that makes it easy to lose, which is exactly why NovoMCP writes it down. The absence of a written false alarm stays invisible until someone else pays the six hours, and by then the avoidance is already gone.
