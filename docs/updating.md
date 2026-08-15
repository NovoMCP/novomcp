# Installing and updating NovoMCP

## Installation paths

The engine supports two install paths. Both boot it on `http://localhost:8018`; pick the one that fits your setup.

### Path 1 — git clone (primary, works everywhere Python does)

```bash
git clone https://github.com/NovoMCP/novomcp.git
cd novomcp/orchestrator
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main_https.py
```

Requires Python 3.10+. See [troubleshooting.md](troubleshooting.md) if you hit `psycopg2-binary` compile issues on macOS.

### Path 2 — Docker Compose (no Python setup needed)

```bash
git clone https://github.com/NovoMCP/novomcp.git
cd novomcp
docker compose up
```

This builds the engine image locally from `orchestrator/Dockerfile` on the first run (~2-3 min), then boots it. Optional compute services (`chem-props`, `autodock-gpu`, `gromacs-md`, etc.) are commented out in `docker-compose.yml` — uncomment the blocks you want to wire up.

The engine will be reachable at `http://localhost:8018`. The local audit sink persists in a docker volume across restarts.

## On the roadmap

The two paths above cover git-comfortable and docker-comfortable users. There is also a published PyPI package, plus a few paths still on the roadmap:

| Path | Status | Command |
|---|---|---|
| **PyPI package** | available | `pip install novomcp` — installs the engine with a `novomcp` entry point |
| **Prebuilt Docker image (GHCR)** | planned, not yet shipped | `docker pull ghcr.io/novomcp/novomcp:latest` — skips the local build step; ~30 sec to running vs 2-3 min |
| **`novo` CLI** (`npx novo` / `npm i -g novo`) | roadmap, targeted v1.4.5 | `novo dock ...`, `novo funnel run ...` — thin scriptable client, does NOT bundle GPU compute |
| **Homebrew tap** | later, based on demand | `brew install novomcp` |

**None of these are required** — they're conveniences layered on top of the git-clone and docker-compose paths that already work today.

If someone asks "will you dockerize it?" — docker-compose already works (builds the engine locally); the prebuilt GHCR image is planned but not yet shipped.

If someone asks "is there a PyPI package?" — yes, `pip install novomcp` (git clone also works).

If someone asks "is there a CLI?" — `novo` is on the roadmap (targeted v1.4.5); for now the MCP interface (any MCP-compatible AI assistant) or the REST API are the two "call it programmatically" paths.

## How updates work

New versions ship weekly. Your install tells you when there's a new one — check the boot log for a line like:

```
NovoMCP v1.4.3 running. A newer release is available: https://github.com/NovoMCP/novomcp/releases/latest
```

You can also ask the engine at any time via any MCP client:

```
Using NovoMCP, check for updates.
```

That calls `get_platform_info(info_type='update')` and returns the current version + whether a newer release is out.

## Updating, by install method

### git clone

```bash
cd /path/to/novomcp
git pull
pip install -r orchestrator/requirements.txt   # in case deps changed
# then restart the engine
```

### Docker Compose

```bash
cd /path/to/novomcp
git pull
docker compose build --no-cache engine   # rebuild from the updated source
docker compose up -d
```

### `pip install novomcp` (from PyPI)

```bash
pip install --upgrade novomcp
```

### Prebuilt Docker image (when available)

```bash
docker pull ghcr.io/novomcp/novomcp:latest
docker restart novomcp   # or however your compose/k8s manifest names it
```

### Homebrew (later)

```bash
brew upgrade novomcp
```

## After an update

On the first boot after an upgrade, you'll see:

```
NovoMCP upgraded from v1.4.2 → v1.4.3. Release notes: https://github.com/NovoMCP/novomcp/blob/main/docs/changelog.md
```

Then follow the changelog link to read what changed.

## Announcement channels

Pick whichever way you want to hear about new releases:

- **[GitHub Releases](https://github.com/NovoMCP/novomcp/releases)** — the source of truth. Every release with notes. RSS-able at `.atom`.
- **[docs/changelog.md](changelog.md)** — mirrored notes, always current on `main`.
- **[@novomcp on X](https://x.com/novomcp)** and **[NovoMCP on LinkedIn](https://linkedin.com/company/novomcp)** — one post per release with the marketing beat.
- **Email list** — sign up on [novomcp.com](https://novomcp.com) if you want release notes delivered.

## Rolling back

Every release is a git tag. If v1.2.0 breaks something for you, drop back to v1.1.0:

```bash
# git install
git -C /path/to/novomcp checkout v1.1.0
docker compose build --no-cache engine   # if you're on Docker Compose too
docker compose up -d

# pip install
pip install novomcp==1.1.0

# prebuilt docker (when available)
docker pull ghcr.io/novomcp/novomcp:v1.1.0
```

Then [open an issue](https://github.com/NovoMCP/novomcp/issues) so we can fix it forward.

## Opting out of the update check

The boot-time GitHub check is a single anonymous HTTPS GET, 30-day cached, 3-second timeout, silent on any failure. If you'd rather it not run at all:

```bash
export NOVOMCP_NO_UPDATE_CHECK=1
```

Set it in your shell rc or your systemd unit / docker-compose env and the check is a no-op.

## What's in the check

- The engine reads `orchestrator/version.py` for the current version
- It fetches `https://api.github.com/repos/NovoMCP/novomcp/releases/latest` (no auth, no user info sent)
- Caches the result in `~/.novomcp/update_check.json` for 30 days
- Compares tags with a plain semver parse; logs one line if the remote is newer
- Also tracks the last-seen version in `~/.novomcp/last_seen_version` so it can note "you just upgraded" once, after the fact

No telemetry, no usage data, no tokens. Everything is local except one outbound call to GitHub every 30 days.
