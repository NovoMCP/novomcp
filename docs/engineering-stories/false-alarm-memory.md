# False-alarm resolution as institutional memory

*The bugs we didn't have, and why writing them down matters*

**Draft:** July 2026
**Author:** NovoMCP engineering

---

Most engineering memory is a record of things that went wrong. Bug fixes, post-mortems, incident timelines. The team writes those down because they're expensive to relearn, if we forget how we fixed the auth leak in Q1, the next auth leak takes longer to diagnose because we've lost the shape of it.

Not much team memory is a record of things that *seemed* wrong but weren't. False alarms. Bugs we chased and turned out to be user error, misleading logs, test-code mistakes rather than production-code mistakes. Those get closed and forgotten. And every few months, someone in the team walks into the same false alarm from scratch, spends the same day chasing it, and closes it with the same "oh, that was actually fine" comment.

We've started keeping a specific kind of institutional memory: the *resolution of the false alarm*, not just the fact that it was one. Two examples, both from a molecular-dynamics engineering effort in the summer of 2026, illustrate the pattern.

---

## The 90-minute-rebuild-that-wasn't-necessary

We had a container image build that took 90 minutes when it failed. Not when it succeeded, success builds took the normal five minutes. But when a build failed partway through, the next attempt started from scratch, including the expensive early stages that had been working fine, and would take a full 90 minutes before failing (or succeeding) at whatever point had actually broken.

At the time, this felt like a fact of the pipeline. The compilation stage was expensive; the image was large; the CI cache was, well, it was probably doing something? We paid the cost, several times, while diagnosing the actual issue we cared about.

The false alarm was: the 90-minute rebuild was a fact of life, so budget for it.

The resolution came when we sat with the pipeline output long enough to notice that failed builds *weren't exporting the buildx cache*. Successful builds cached everything. Failed builds cached nothing. The cache-preservation step was inside a `success()` conditional, which meant a failure at minute 45 threw away the cache from minute 0 through 44.

Fix: move the cache-export step out of the `success()` conditional, so failed builds preserve cache through the point of failure. Now a subsequent build reuses the cached layers up to the point that failed last time, and only re-runs from there. Typical failure-to-retry time dropped from 90 minutes to under 10.

The specific fix is small. The value of writing it down is that it captures a *class* of institutional shortcut: **when a slow operation feels like it should be faster, check whether a caching layer is silently disabled for the failure path.** That question, once written, will get asked in front of the next slow-CI-loop we hit. Without the writeup, we'd re-derive the question, or, more likely, we'd budget for the slow loop and never re-derive it.

There's a companion shortcut we captured at the same time: **validate the pushed artifact in a cheap ephemeral pod, not by re-triggering the full build.** If you're debugging whether a compiled binary works, run the compiled binary in a small pod that pulls the image and executes a smoke test. Don't push a fix, re-run the build, wait for the build to redeploy the image, then check whether the fix worked. The build is upstream of the artifact; the artifact is what you want to test; test the artifact directly.

That habit, once installed, cost us zero to install and saved us a couple of days over the next month. Not because it was a hard insight, it wasn't, but because *nobody had said it out loud*. Once said, it became the default. Not-saying-it-out-loud was the failure mode. Writing it down was the fix.

---

## The feature that wasn't broken

The other example is smaller and stranger. We had a piece of software that supported an enhanced-sampling MD protocol (Hamiltonian replica exchange, if you're a molecular dynamics person). We suspected the software's build didn't support this protocol, it took some grepping through source and log messages and thermostat behavior to reach that suspicion. Every check we ran seemed to confirm it.

We were about a day into planning an image rebuild to enable the protocol when someone on the team took another look at how we were checking. The `grep` we'd been using to detect the feature was searching for a symbol that isn't in the built binary even when the feature is present (the symbol is in a source file that's compiled into the binary but not surfaced as a searchable string). The thermostat-behavior check was looking for a log line that had been renamed in a version bump. Each individual check *looked* like it was probing the right thing, and each was wrong for a different subtle reason.

The feature was present. Had been all along. Our tests for detecting it were flawed.

Total time cost of the false alarm: about six hours of investigation and about a day of planning-for-a-rebuild-we-didn't-need. Small in absolute terms; substantial for a project on a timeline.

The resolution's lesson: **when the target seems broken, verify your tests before you verify the target.** Every check we ran during that six hours was probing the wrong artifact or the wrong string. If we'd stopped to ask, at any point, "is this check itself correct?", instead of "what does this check tell us about the target?", the false alarm would have collapsed in fifteen minutes.

That's a hard habit to install. When investigation is producing a coherent story about a broken feature, the pull is toward extending the story, not questioning its inputs. But the pull is exactly where the failure mode lives: coherent-but-wrong stories converge fast, and the way to catch them is to explicitly check the *investigation instruments* before extending the investigation.

Written down, it's a check to add to any debugging workflow: **before I extend this diagnostic, does the diagnostic actually work?** For any grep, does the string I'm greping for correspond to what I think it does? For any log-line check, is that log line still emitted by the current version? For any test, does the test's failure mode correspond to what I'm inferring from it?

---

## The pattern

The two examples are unrelated in surface features. The build-cache case is CI infrastructure; the enhanced-sampling case is molecular dynamics software. Different systems, different failure modes, different fixes.

What they share is a specific memory shape. In both, we ended a diagnostic loop with a resolution that could be stated in one sentence: "when X seems broken, ask whether Y is misleading you." Neither resolution was a bug fix. Neither would appear in a post-mortem. Both are the kind of insight that ends a false alarm cleanly, and that gets forgotten the next day because there's no bug to link the insight to.

Institutional memory for false alarms is different from institutional memory for bugs. Bug memory is "here's what broke, here's what fixed it, don't reintroduce the break." False-alarm memory is "here's what looked broken but wasn't, here's the check that revealed the truth, ask that check next time." The first tells you what to avoid. The second tells you what to *ask*.

Both are useful. Only one usually gets written down.

---

## What the format looks like

We keep false-alarm resolutions in the same document as our regular team memory. The format is short: a title naming the alarm, one paragraph describing what looked wrong, one paragraph naming the resolution, and one sentence generalizing the insight.

For the build-cache case, the entry looks something like:

> **90-minute failed-build loop turned out to be missing cache preservation.**
> Failed CI builds were rebuilding from scratch because the cache-export step was inside a `success()` conditional. Moving the export out of the conditional dropped failed-retry time from 90 minutes to under 10.
> **Lesson:** when a slow operation feels like it should be faster, check whether a caching layer is silently disabled for the failure path.

That's the whole entry. Four lines. Anyone on the team, in six months, hitting a slow-CI failure loop can search for "slow CI" or "build cache" in the memory and reach that entry immediately. The lesson generalizes, it's not specific to this one build, so the next time we hit a slow-and-shouldn't-be operation, the question is queued up.

---

## Why this matters more than it sounds

The reason to write down false-alarm resolutions is that the *not-having-written-them-down* failure mode is invisible.

If we don't write down a bug fix, someone reintroduces the bug, we notice, we get frustrated. The cost is visible. It generates pressure to write things down next time.

If we don't write down a false-alarm resolution, the same false alarm shows up in someone else's investigation six months later. That person spends six hours chasing it and closes it as user error. The cost is invisible, the person who chased it doesn't know that someone else already did, and it never generates pressure to fix the pattern, because there's no shared awareness that a pattern exists.

Institutional memory that only captures bug fixes is asymmetric. It over-weights failure-with-a-fix and under-weights *diagnostic paths that were followed and turned out to be dead ends*. Dead ends are useful information; they mean the next investigator can skip that path. But dead ends don't get written down because they don't feel like they produced anything. They produced an absence of a bug, which is the wrong shape to be a natural artifact.

The fix is to write them down anyway. Not everything, some false alarms are truly one-off and not worth the space. But false alarms whose resolution *generalizes*, where the insight is "here's a class of check that reveals the truth", are worth capturing specifically because the class recurs.

---

## When to write, when to skip

Not every false alarm needs an entry. The heuristic we use:

**Write it down if the resolution is a check you'd want to run first next time.** The build-cache case qualifies: "check whether caching is disabled for the failure path" is a check we'd run first next time. The enhanced-sampling case qualifies: "verify the diagnostic before extending the investigation" is a check we'd install as a habit.

**Skip it if the resolution is idiosyncratic to the specific artifact.** If the false alarm was "we thought the value should be 0.5 but it was actually configured to 0.7 for this specific environment," there's not a generalizable check, you just look it up next time. Idiosyncratic resolutions don't compound; the entry cost isn't paid back.

**Write it down if the false alarm consumed significant investigation time.** Time is a proxy for "the false alarm looked real enough to justify investigation." The next investigator will make the same call. Giving them a shortcut back to the resolution is worth the two minutes of writeup.

**Skip it if you resolved it in five minutes because a colleague noticed the issue in passing.** Fast resolutions from lucky observation aren't teaching anyone a discipline, they're teaching gratitude for the colleague, which doesn't scale.

The pattern doesn't require a formal process. It just requires the willingness, at the end of a false-alarm investigation, to ask: *would writing this down save the next person from repeating my six hours?* If yes, take the two minutes. If no, close the ticket and move on.

---

## What we get

The team memory doc has, at last count, about a dozen false-alarm entries alongside the bug fixes and design decisions. They're a minority of the document. But they've been searched more often than we expected, including a few times where the searcher was one of the entry's original authors, who had forgotten the resolution and re-derived it faster because the entry existed.

The metadata is small; the compounding benefit is real. Once you're in the habit of writing them, they cost almost nothing to add, and every one that gets referenced later, even once, has paid for the entire practice.

The deeper point: **negative-result documentation is what stops rediscovery cycles**. Bug memory is about not making the same mistake twice. False-alarm memory is about not doing the same investigation twice. Both matter. Only one is under-invested-in, and only one has a natural absence-of-artifact problem that makes it easy to forget.

Write the false alarms down. The absence of them being written is invisible until someone else pays the six hours you paid, and by then it's too late for them to know it was avoidable.
