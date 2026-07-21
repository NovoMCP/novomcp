# Troubleshooting

Common install/boot issues and their fixes. If your issue isn't here, [open an issue](https://github.com/NovoMCP/novomcp/issues) — good bug reports become new entries on this page.

## Install

### psycopg2 install fails on macOS

**Symptoms:** `pip install -r requirements.txt` fails with a compile error mentioning `libpq-fe.h` or `Error: pg_config executable not found`.

**Cause:** `psycopg2-binary` needs Postgres client headers to compile from source when the pre-built wheel isn't available for your Python + macOS combination.

**Fix:**

```bash
brew install postgresql@16
pip install psycopg2-binary
```

If the pre-built wheel exists, `pip` picks it up and the Homebrew install isn't strictly needed. On fresh venvs where the wheel is missing, the compile-from-source path needs the headers.

**Not blocking a boot:** the engine runs fine without `psycopg2-binary` when `NOVOMCP_DB_HOST` is unset (which is the OSS default). Only wire it in when you're ready to load the omics data pack.

### Python 3.9 install fails

**Symptoms:** `pip install -r requirements.txt` fails, or `python main_https.py` exits immediately with a Python-version error.

**Cause:** NovoMCP requires Python 3.10+. Python 3.9 hit end-of-life October 2025 and several transitive dependencies (`python-multipart>=0.0.30` for CVE-2024-53981, current starlette + fastapi) require 3.10+.

**Fix:** install a supported Python version.

```bash
# macOS (Homebrew)
brew install python@3.11

# Linux (apt)
sudo apt install python3.11 python3.11-venv

# Then recreate the venv
rm -rf .venv
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main_https.py
```

## Boot

### Engine boots but tools/list returns fewer tools than expected

**Symptoms:** You expected the full 67-tool catalog, but `tools/list` shows only 11 (or some small number).

**Cause:** This is by design. Tools whose service or data dependencies aren't wired locally are hidden from `tools/list` — no "unavailable" errors on tools you can't run. See [tool availability](tool-availability.md) for the full map of what unlocks what.

**Fix:** either configure the service (see [deploying services](deploying-services/README.md)) or turn on debug mode to see everything:

```bash
export NOVOMCP_SHOW_HIDDEN_TOOLS=1
python main_https.py
```

In debug mode, unwired tools are visible but calls to them return structured "service unavailable" errors.

### `NOVOMCP_DB_HOST` is set but omics tools still don't appear

**Symptoms:** You set `NOVOMCP_DB_HOST` but `target_discovery` / `validate_target` / `stratify_patients` don't show up in `tools/list`.

**Cause:** the engine reads env at boot. Set the variable, then restart the engine (Ctrl+C, then `python main_https.py`). MCP clients also cache the tool list — reconnect the MCP connection to see the new tools.

**Fix:**

```bash
# Set the env var
export NOVOMCP_DB_HOST=postgresql://novomcp:password@localhost:5432/novomcp

# Restart the engine
python main_https.py

# In your MCP client (Claude Desktop / Cursor / etc.), reconnect the NovoMCP
# connection — the client caches tools/list until reconnect.
```

## External APIs

### `search_biorxiv` / `search_chembl` / `audit_system` return "connection error"

**Symptoms:** These three tools sometimes fail with transient connection errors even when other tools work fine.

**Cause:** these tools hit external public APIs (`api.biorxiv.org`, `www.ebi.ac.uk/chembl`, `data.rcsb.org`) directly. When those services rate-limit, hit maintenance, or your network drops packets to them, the tool fails. No config change on your side helps — retry in a minute.

**Fix:** retry. If it keeps failing on your network specifically, check whether your firewall / VPN is blocking these hosts.

## Updates

### The boot-time update check spams the log

**Symptoms:** every boot logs about a newer version being available.

**Cause:** the check caches the result for 30 days but if you never update, it keeps reporting the same "newer version available" line.

**Fix:** either update the engine (see [updating](updating.md)) or disable the check:

```bash
export NOVOMCP_NO_UPDATE_CHECK=1
```

### Update check fails behind a corporate firewall

**Symptoms:** boot log shows nothing about updates and `get_platform_info(info_type='update')` returns `update_check_enabled: true` but no `latest_version` field.

**Cause:** the check makes one HTTPS GET to `api.github.com`. Corporate firewalls sometimes block GitHub API traffic even when they allow GitHub HTTPS.

**Fix:** either allow `api.github.com` outbound, or disable the check:

```bash
export NOVOMCP_NO_UPDATE_CHECK=1
```
