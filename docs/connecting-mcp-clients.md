# Connecting MCP clients

Once the engine is running locally on `http://localhost:8018`, any MCP-compatible client can use it as a tool provider. This page walks through the exact config for the most common clients.

**Before you start:** confirm the engine is up with `curl -s http://localhost:8018/health` — expect `{"status":"healthy",...}`. If not, see the [Quickstart](quickstart.md).

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
- `novomcp` should show as connected with 11 tools (the always-available set; the other 56 in the catalog appear as you wire backing services — see [tool-availability.md](tool-availability.md))

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

Add to `~/.config/zed/settings.json` (macOS/Linux):

```json
{
  "context_servers": {
    "novomcp": {
      "command": {
        "path": "curl",
        "args": [
          "-N",
          "-H", "Authorization: Bearer x",
          "http://localhost:8018/mcp/"
        ]
      }
    }
  }
}
```

Zed uses stdio for MCP; the `curl -N` invocation wraps our HTTP endpoint. Restart Zed.

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
