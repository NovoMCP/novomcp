# Contributing to NovoMCP

Thanks for taking an interest. NovoMCP is a **reference-quality open-source project**, not a supported commercial product. The bar is: contributions that clearly improve the engine, its wrappers, or its documentation are welcome; there is no support commitment, and no promise that PRs will be reviewed on any particular timeline.

## Where to start

- **Bug reports**, open an issue with a minimal reproducer. Include your Python version, the exact env vars set (`NOVO_AUTH`, `NOVO_METER`, `NOVO_AUDIT`), and the tool call that failed.
- **Feature requests**, open an issue that describes the *problem*, not just the *feature*. A well-framed problem tends to attract the person best suited to solve it.
- **Docs improvements**, probably the most impactful place to help early. If the quickstart fails on your machine, that's a bug.
- **New service wrappers**, the pattern is documented in [`docs/deploying-services/README.md`](../docs/deploying-services/README.md). Thin wrappers over open compute primitives are the sweet spot.

## Workflow

1. Fork the public repo.
2. Create a topic branch off `main`.
3. Make your change. Keep it focused, one PR = one thing.
4. Run the smoke test locally (`python novomcp/main_https.py` boots and answers a request).
5. Open a PR against `main`. Describe *what* changed and *why*.

## What we look for in a PR

- **Passes CI.** No linter or test breakage.
- **Stays self-contained.** PRs that add hooks or dependencies on external hosted services (rather than the local engine) will be closed. If your idea only works against a hosted backend, it belongs elsewhere.
- **Preserves the seam.** The `AuthGate` / `CreditMeter` / `AuditSink` interfaces in `orchestrator/mcp/spine.py` are the engine's extension points. Changing them requires a discussion in an issue first.
- **Documents its own reasoning.** Non-obvious code gets a one-line comment explaining *why*, not *what*.

## Attribution

Merged PRs get credited in the `CHANGELOG` for the next release. Contributors also land in the top-level `CONTRIBUTORS` file.

## Licensing your contributions

By opening a PR against this repo you agree that your contribution is licensed under:

- **Apache-2.0** if it lands in a path governed by the top-level `LICENSE`
- **Business Source License 1.1 → Apache-2.0 (change date 2029-07-12)** if it lands in `novomcp/mcp/` (governed by `LICENSE.core`)

If either is a blocker, tell us in the PR description and we'll work something out.

## What NOT to contribute

- **Do not upload weights or datasets to this repo.** Model weights live in companion repos with their own MIT / CC-BY licenses. Datasets live on an open-data host.
- **Do not add managed backend features here.** Metering, auth, billing consoles, admin dashboards, those belong in the managed backend, which is not this project.
- **Do not integrate closed-source dependencies.** If your patch requires a proprietary library, the patch doesn't fit here.

## Security

If you find a security issue, do NOT open a public issue. Email `security@novomcp.com` with details. We'll acknowledge within 72 hours and coordinate a fix + disclosure timeline.

## Code of Conduct

Be direct, be technical, be kind. Personal attacks, harassment, or disrespect toward contributors or maintainers will result in a ban. Substantive disagreement is welcome.
