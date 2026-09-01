# Connecting MCP clients

Once the engine is running locally on `http://localhost:8018`, any MCP-compatible client can use it as a tool provider. This page walks through the exact config for the most common clients.

**Before you start:** confirm the engine is up with `curl -s http://localhost:8018/health` — expect `{"status":"healthy",...}`. If not, see the [Quickstart](quickstart.md).

### Two ways to connect

NovoMCP speaks MCP over two transports, and clients differ in which they accept:

- **HTTP — the full engine on `:8018`.** All 68 tools. Clients that speak MCP over HTTP (Cursor, Codex, ChatGPT connectors) point straight at `http://localhost:8018/mcp/`. Clients that only accept a stdio *command* (Claude Desktop, Zed) reach the HTTP engine through the tiny `npx mcp-remote` bridge shown in those sections.
- **stdio — a command the client spawns.** Zero networking; the client runs a local process and talks over stdin/stdout. If you installed `novomcp-lite` (the Apache-licensed chem + search subset, `pip install novomcp-lite`), its `novomcp-lite` command *is* a stdio MCP server — drop it into any command-based client (Claude Desktop, Zed, Codex) with no bridge. It exposes a subset of the 68 tools, so it's the fastest start, not the full surface.

Rule of thumb: **want all 68 tools → the HTTP engine** (directly or via `mcp-remote`); **want the quickest start → the `novomcp-lite` command.**

---

## Claude Desktop (macOS / Windows / Linux)

### 0. Prerequisite: Node.js

Claude Desktop's config file supports **stdio** MCP servers only — for HTTP servers like NovoMCP, we use a small stdio-to-HTTP proxy called `mcp-remote`. It runs via `npx` on demand, no separate install, but you need Node.js.

Check:
```
node --version
```

If you get `command not found`, install Node:

=== "macOS (Homebrew)"

    ```
    brew install node
    ```

=== "Ubuntu / Debian"

    ```
    sudo apt install nodejs npm
    ```

=== "Windows"

    Download from [nodejs.org](https://nodejs.org/) or run `winget install OpenJS.NodeJS.LTS`.

### 1. Add NovoMCP to Claude Desktop's config

Two ways — pick whichever you're more comfortable with. Both end at the same result.

=== "Option A — Edit through Claude Desktop (no terminal)"

    **1.** In Claude Desktop, go to **Settings → Developer → Edit Config**. A Finder (macOS) or Explorer (Windows) window opens showing `claude_desktop_config.json`.

    **2.** Right-click the file → **Open With → TextEdit** (macOS) or **Notepad** (Windows). Don't double-click — that opens a preview, not an editor.

    **3.** What to paste depends on the file's current state:

    - **If the file is empty or brand new**, replace everything with:

        ```json
        {
          "mcpServers": {
            "novomcp": {
              "command": "npx",
              "args": [
                "-y",
                "mcp-remote",
                "http://localhost:8018/mcp/",
                "--header",
                "Authorization:Bearer x"
              ]
            }
          }
        }
        ```

    - **If the file already has other MCP servers**, add the `"novomcp"` block inside the existing `"mcpServers"` object, comma-separated from the others:

        ```json
        {
          "mcpServers": {
            "some-other-server": { "...existing...": "..." },
            "novomcp": {
              "command": "npx",
              "args": [
                "-y",
                "mcp-remote",
                "http://localhost:8018/mcp/",
                "--header",
                "Authorization:Bearer x"
              ]
            }
          }
        }
        ```

    **4.** Save (Cmd+S / Ctrl+S). Close the editor. Close the Finder / Explorer window.

=== "Option B — One command in Terminal (fastest)"

    Open your terminal and paste one line. It reads your existing config, adds NovoMCP wired through `mcp-remote`, writes it back — no manual JSON editing.

    === "macOS / Linux"

        ```
        python3 -c "import json,os;p=os.path.expanduser('~/Library/Application Support/Claude/claude_desktop_config.json');os.makedirs(os.path.dirname(p),exist_ok=True);c=json.load(open(p)) if os.path.exists(p) and os.path.getsize(p)>0 else {};c.setdefault('mcpServers',{})['novomcp']={'command':'npx','args':['-y','mcp-remote','http://localhost:8018/mcp/','--header','Authorization:Bearer x']};json.dump(c,open(p,'w'),indent=2);print('done — NovoMCP added')"
        ```

    === "Windows"

        ```
        python -c "import json,os;p=os.path.expandvars('%APPDATA%\Claude\claude_desktop_config.json');os.makedirs(os.path.dirname(p),exist_ok=True);c=json.load(open(p)) if os.path.exists(p) and os.path.getsize(p)>0 else {};c.setdefault('mcpServers',{})['novomcp']={'command':'npx','args':['-y','mcp-remote','http://localhost:8018/mcp/','--header','Authorization:Bearer x']};json.dump(c,open(p,'w'),indent=2);print('done')"
        ```

    You should see `done — NovoMCP added`.

!!! note "Why the proxy"
    Claude Desktop's `mcpServers` config only accepts stdio commands. The `Add custom connector` UI would work for HTTP, but only accepts HTTPS URLs — not localhost. `mcp-remote` bridges the gap: Claude Desktop spawns it as a stdio process, and it forwards to our HTTP engine. Nothing installs permanently — `npx -y` runs it on demand.

### 2. Restart Claude Desktop

Fully quit (Cmd+Q on macOS, right-click tray icon → Quit on Windows) and reopen. A window close is not enough — MCP config only reloads on full app restart.

!!! note "Why `Bearer x`"
    In local mode, `LocalAuthGate` accepts any bearer token — `x` is just a placeholder. In hosted mode, replace with your real API key.

### 3. Verify the connection

In Claude Desktop:

- Look for the **tools icon** (hammer or plug shape) near the message input
- Or go to **Settings → Developer → MCP Servers**
- `novomcp` should show as connected with 11 tools (the always-available set; the other 57 in the catalog appear as you wire backing services — see [tool-availability.md](tool-availability.md))

In the terminal running the engine, you should see an incoming request when Claude connects — that's the initialize handshake.

### 4. Try a real query

```
Using NovoMCP, get me the ADMET properties of aspirin
```

Claude picks a tool (usually `get_molecule_profile` or `predict_admet`), calls it, and returns the results.

For autonomous discovery-funnel mode:

```
agm glioblastoma
```

Claude calls `run_novo_ag(disease="glioblastoma")` and starts executing the 11-stage discovery protocol.

---

## Cursor

Cursor supports MCP via its settings UI or a config file.

### Via the UI

1. Open Cursor
2. Settings → Features → MCP → Add new MCP server
3. Fill in:
     - **Name**: `novomcp`
     - **Type**: `http`
     - **URL**: `http://localhost:8018/mcp/`
     - **Headers**: `Authorization: Bearer x`
4. Save

### Via config file

Cursor reads MCP config from `~/.cursor/mcp.json` (macOS/Linux) or the equivalent on Windows.

```json
{
  "mcpServers": {
    "novomcp": {
      "type": "http",
      "url": "http://localhost:8018/mcp/",
      "headers": {
        "Authorization": "Bearer x"
      }
    }
  }
}
```

Restart Cursor after editing.

---

## Zed

Zed's `context_servers` run **stdio** MCP servers (a command Zed spawns), so reach the HTTP engine through the `mcp-remote` bridge — the same one Claude Desktop uses (needs Node.js — see the Node.js prerequisite under Claude Desktop above). Add to `~/.config/zed/settings.json` (macOS/Linux) or `%APPDATA%\Zed\settings.json` (Windows):

```json
{
  "context_servers": {
    "novomcp": {
      "command": {
        "path": "npx",
        "args": [
          "-y", "mcp-remote",
          "http://localhost:8018/mcp/",
          "--header", "Authorization:Bearer x"
        ]
      }
    }
  }
}
```

Zed restarts the context server automatically on save — no editor restart needed. If you installed `novomcp-lite`, skip the bridge and set `"path": "novomcp-lite"` (drop the `args`) for the chem + search subset.

---

## OpenAI Codex

Codex reads MCP config from `~/.codex/config.toml` (or a project-scoped `.codex/config.toml`) and supports **both** transports.

### Full engine over HTTP (all 68 tools)

```toml
[mcp_servers.novomcp]
url = "http://localhost:8018/mcp/"
bearer_token_env_var = "NOVOMCP_TOKEN"
```

`bearer_token_env_var` names an environment variable holding the token; local mode accepts any string:

```bash
export NOVOMCP_TOKEN=x
```

### The `novomcp-lite` command (stdio subset, no HTTP)

```toml
[mcp_servers.novomcp-lite]
command = "novomcp-lite"
```

Or add it without editing the file:

```bash
codex mcp add novomcp-lite -- novomcp-lite
```

Start a Codex session and run `/mcp` to confirm the server is connected and its tools are listed.

---

## ChatGPT (Developer Mode)

ChatGPT connects to MCP servers through **Developer Mode** (Settings → toggle Developer Mode on; the connector list lives under Plugins, formerly Connectors). Unlike Claude, Codex, and Zed, ChatGPT connects only to a **remote** server over Streamable HTTP or SSE — there is no "run a local command" option, and the ChatGPT web app cannot reach `http://localhost`. So the engine has to be reachable at a URL ChatGPT can hit.

**Option A — tunnel the local engine (quickest).** Expose `:8018` over HTTPS, then register the tunnel URL:

```bash
# cloudflared shown; ngrok works the same way
cloudflared tunnel --url http://localhost:8018
# -> https://<random>.trycloudflare.com
```

In ChatGPT: Settings → Developer Mode → add a connector:

- **URL**: `https://<your-tunnel>/mcp/`
- **Auth**: none (local mode accepts any bearer), or a real token if you've configured auth

**Option B — point at a deployed instance.** If you self-host the engine behind HTTPS (your own cloud/EKS), register that URL directly — no tunnel needed.

**Heads-up:** OpenAI does not verify custom connectors, and a connector can read *and write* through every tool the server exposes — only connect a server you control. A tunnel also makes your local engine reachable by anyone who has the URL for as long as it's open; close it when you're done.

---

## Custom / any MCP client

Any client that speaks MCP JSON-RPC 2024-11-05 over HTTP works. Point it at:

- **URL**: `http://localhost:8018/mcp/`
- **Method**: `POST`
- **Auth header**: `Authorization: Bearer <any-string-in-local-mode>`
- **Content-Type**: `application/json`

Send an `initialize` request first, then `tools/list`, then `tools/call`. See the [API reference](api-reference.md#mcp-json-rpc) for wire format.

---

## Troubleshooting

### "MCP server disconnected" / red indicator

Almost always one of:

1. **Engine not running** — check `curl -s http://localhost:8018/health`. If nothing, boot the engine per the [Quickstart](quickstart.md).
2. **Config syntax error** — validate your JSON with `python3 -m json.tool < path/to/config.json`. Common causes: missing comma between blocks, trailing comma inside a block, mismatched braces.
3. **Client not fully restarted** — quit the app (not just the window) and reopen.
4. **Wrong URL** — the trailing `/` on `http://localhost:8018/mcp/` matters. Without it, some clients pass a different path.

### `funnel_id` warnings in Claude's responses

Expected on the first tool call. Claude mints a `funnel_id` automatically per session; you'll see it in the audit log at `~/.novo/audit.jsonl`. Not an error.

### Tools not appearing

If the server connects but no tools show:

- Check the engine log for the `tools/list` request — if it arrives and returns 11+ tools (11 on a fresh install; more as backing services are wired), the client is filtering somewhere
- In Claude Desktop, try **Settings → Developer → Restart connectors** if available
- Verify with a direct curl: `curl -X POST http://localhost:8018/mcp/ -H 'Authorization: Bearer x' -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python3 -m json.tool`

### Hosted mode

If you're connecting to hosted NovoMCP instead of localhost:

- Replace `http://localhost:8018/mcp/` with `https://ai.novomcp.com/mcp/` (core surface) or `https://compute.novomcp.com/mcp/` (compute-only tools, paid tier)
- Replace `Bearer x` with your real `nmcp_...` (core) or `ncmcp_...` (compute) API key from your account console

---

## See also

- [Quickstart](quickstart.md) — get the engine running locally
- [API reference](api-reference.md) — REST + MCP JSON-RPC surface details
- [Configuring LLM providers](configuring-llm.md) — enable semantic tool search + orchestration planning
