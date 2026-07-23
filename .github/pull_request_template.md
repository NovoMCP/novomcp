## Summary

<!-- What does this change do, and why? One or two sentences. -->

## Sync discipline

<!-- NovoMCP is developed private-first (nqn-dd/novomcp) and mirrored to the
     public OSS repo (NovoMCP/novomcp). Note where this change originates so the
     other repo stays in sync. -->

- [ ] Originates in **private**; public will be synced from this commit (or N/A — this *is* the public repo)
- [ ] Any public-facing copy passes the engine-first diagnostic (no competitor could ship the same sentence unchanged)

## Checks

- [ ] `smoke` green (fresh-clone boot, health, in-process tool call, audit sink)
- [ ] `docs-check` green if `docs/**` or `mkdocs.yml` touched
- [ ] No hosted-only URLs, secrets, or closed-source leaks introduced
