# Third-party notices

This project depends on third-party open-source software. Apache-2.0 §4(d) requires attribution when redistributing. This document lists every runtime dependency and its license.

The full license text for each dependency is available in the respective package (via `pip show <package>` or `npm view <package> license`) or on the package's homepage. This document summarizes for audit and attribution purposes.

## Audit method

Regenerate this report:

```bash
# Python (orchestrator/)
cd orchestrator
pip install pip-licenses
pip-licenses --format=csv > /tmp/orch-licenses.csv

# Node.js (frontend-nextjs/)
cd frontend-nextjs
npx license-checker --production --json > /tmp/frontend-licenses.json
```

Last regenerated: 2026-07-20 against the current mirror at commit main HEAD.

## License compatibility summary

**NovoMCP top-level:** Apache-2.0 (`LICENSE`)
**Orchestration core (`orchestrator/mcp/`):** BSL 1.1 → Apache-2.0 change date 2029-07-12 (`LICENSE.core`)

**No AGPL, GPL, MPL-with-modification, or other non-compatible licenses in the shipped tree.** Every dependency is either:

- **Permissive** (MIT, Apache-2.0, BSD, ISC, PSF, CC0, 0BSD) — no attribution requirements beyond preserving the license text with the source
- **Weak copyleft** (LGPL-3.0, MPL-2.0) — compatible with Apache-2.0 for library use (dynamic linking / file-scoped copyleft). We do not modify these libraries; we consume them as published.

## Python dependencies (orchestrator/)

Counts as of 2026-07-20:

| License | Count |
|---|---|
| MIT / MIT-CMU / MIT-0 | 44 |
| Apache-2.0 / Apache Software License | 44 |
| BSD-3-Clause / BSD-2-Clause / BSD | 32 |
| Python Software Foundation / PSF-2.0 | 4 |
| ISC | 1 |
| CC0-1.0 | 1 |

**Weak copyleft (LGPL / MPL) — named individually for attribution:**

| Package | License | Notes |
|---|---|---|
| `GridDataFormats` | LGPL-3.0-or-later | MDAnalysis file-format library. Consumed unmodified. |
| `MDAnalysis` | LGPL-3.0-or-later | Trajectory analysis. Consumed unmodified. |
| `jwcrypto` | LGPL-3.0-or-later | JWT/JWS/JWE crypto library. Consumed unmodified. |
| `psycopg2-binary` | LGPL-3.0 (+ OpenSSL exception) | Postgres driver. Consumed unmodified. |
| `certifi` | MPL-2.0 | Root CA certificate bundle. Consumed unmodified. |
| `tqdm` | MPL-2.0 AND MIT | Progress bars. Consumed unmodified. |

## Node.js dependencies (frontend-nextjs/)

Counts as of 2026-07-20 (production dependencies only, 95 total):

| License | Count |
|---|---|
| MIT | 82 |
| Apache-2.0 | 5 |
| ISC | 3 |
| BSD-3-Clause | 1 |
| 0BSD | 1 |
| CC-BY-4.0 | 1 |
| LGPL-3.0-or-later | 1 |

**Weak copyleft (LGPL) — named individually for attribution:**

| Package | License | Notes |
|---|---|---|
| `@img/sharp-libvips-darwin-arm64` | LGPL-3.0-or-later | Native libvips binary bundled with Next.js's `sharp` image-processing library. Consumed unmodified. |

**Non-license attribution:**

| Package | License | Notes |
|---|---|---|
| (varies) | CC-BY-4.0 | Data license; attribution required. Preserved in the package's own README/attribution files. |

## Chrome + Word sideload extensions

The `novomcp-chrome-sideload` and `novomcp-word-sideload` repos (separate from the OSS engine repo) have their own `package.json` license fields (Apache-2.0) and their own dependency graphs. Audit those repos separately at release time.

## Adding a new dependency

Before merging a PR that adds a runtime dependency:

1. Run the pip-licenses / license-checker commands above
2. Confirm the new dependency's license is on the compatible list
3. Update this file with the new license row and package name if it's a weak-copyleft add

**Never add** AGPL, GPL-3.0, or dual-licensed packages whose commercial-license terms would encumber the Apache-2.0 top-level.

## Attribution requirements

For Apache-2.0 §4(d), NovoMCP's own `NOTICE` file is redistributed with every copy of the software. This third-party-notices document is provided as additional attribution for the dependencies listed above. Users redistributing NovoMCP should include both `LICENSE` and this file in their distribution.

## License text

Every listed dependency's full license text is available:

- Python packages: `pip show <package>` shows the license summary; the full text ships with the package
- npm packages: `npm view <package> license` for the summary; the full LICENSE file ships with the package in `node_modules/<package>/LICENSE`

For a signed audit report, run:

```bash
pip-licenses --format=markdown --with-license-file --with-notice-file > python-full-licenses.md
license-checker --production --out node-full-licenses.txt
```
