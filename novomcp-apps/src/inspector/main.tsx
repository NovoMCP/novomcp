/**
 * Message Inspector — local dev harness for viewers and providers.
 *
 * Mount viewers outside the MCP host with mocked ViewProps so we can
 * iterate on UI, payload shapes, and provider integration without
 * deploying. Renders a sidebar of registry entries, a stage, and a
 * live log of every sendMessage / callServerTool / openLink / sendLog
 * call that the mounted component would emit.
 */
import { useEffect, useMemo, useState } from "react";
import { REGISTRY, type Fixture, type RegistryEntry } from "./registry.tsx";
import type { LogEntry, LogSink, MockConfig } from "./mock-view-props.ts";

type Theme = "light" | "dark";

// =============================================================================
// Top bar
// =============================================================================

function TopBar({
  theme,
  onThemeChange,
  onClearLog,
}: {
  theme: Theme;
  onThemeChange: (t: Theme) => void;
  onClearLog: () => void;
}) {
  return (
    <div className="inspector-top">
      <div className="inspector-brand">
        Novo<span className="accent">MCP</span> · Message Inspector
      </div>
      <div className="inspector-controls">
        <button
          className={`inspector-theme-btn ${theme === "light" ? "active" : ""}`}
          onClick={() => onThemeChange("light")}
          type="button"
        >
          Light
        </button>
        <button
          className={`inspector-theme-btn ${theme === "dark" ? "active" : ""}`}
          onClick={() => onThemeChange("dark")}
          type="button"
        >
          Dark
        </button>
        <button className="inspector-theme-btn" onClick={onClearLog} type="button">
          Clear log
        </button>
      </div>
    </div>
  );
}

// =============================================================================
// Sidebar
// =============================================================================

function Sidebar({
  selected,
  onSelect,
}: {
  selected: { entryId: string; fixtureId: string };
  onSelect: (entryId: string, fixtureId: string) => void;
}) {
  const providers = REGISTRY.filter((e) => e.kind === "provider");
  const viewers = REGISTRY.filter((e) => e.kind === "viewer");

  const renderGroup = (label: string, group: RegistryEntry[]) => (
    <>
      <div className="inspector-section-label">{label}</div>
      {group.map((entry) => (
        <div key={entry.id} style={{ marginBottom: 4 }}>
          <div
            style={{
              padding: "6px 16px 2px",
              fontSize: 12,
              fontWeight: 600,
              color: "var(--text)",
            }}
          >
            {entry.title}
          </div>
          {entry.subtitle && (
            <div
              style={{
                padding: "0 16px 4px",
                fontSize: 10,
                color: "var(--text-muted)",
              }}
            >
              {entry.subtitle}
            </div>
          )}
          {entry.fixtures.map((fx) => (
            <button
              key={fx.id}
              className={`inspector-item ${
                selected.entryId === entry.id && selected.fixtureId === fx.id ? "active" : ""
              }`}
              onClick={() => onSelect(entry.id, fx.id)}
              type="button"
            >
              {fx.label}
            </button>
          ))}
        </div>
      ))}
    </>
  );

  return (
    <nav className="inspector-side">
      {renderGroup("Providers", providers)}
      <div style={{ height: 12 }} />
      {renderGroup("Viewers", viewers)}
    </nav>
  );
}

// =============================================================================
// Log panel
// =============================================================================

function LogPanel({ entries }: { entries: LogEntry[] }) {
  const fmt = (ts: number) => {
    const d = new Date(ts);
    return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}:${d.getSeconds().toString().padStart(2, "0")}`;
  };

  const describe = (e: LogEntry) => {
    try {
      return typeof e.payload === "string" ? e.payload : JSON.stringify(e.payload);
    } catch {
      return String(e.payload);
    }
  };

  return (
    <div className="inspector-log">
      <div className="inspector-log-header">
        <span>Host callbacks</span>
        <span style={{ color: "var(--text-muted)" }}>{entries.length} event{entries.length === 1 ? "" : "s"}</span>
      </div>
      <div className="inspector-log-body">
        {entries.length === 0 && (
          <div className="inspector-empty">
            No events yet. Interact with the component above — sendMessage, callServerTool, and
            openLink calls will appear here.
          </div>
        )}
        {entries.slice().reverse().map((e) => (
          <div key={e.id} className={`inspector-log-row ${e.error ? "err" : ""}`}>
            <span className="t">{fmt(e.timestamp)}</span>
            <span className="k">{e.kind}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {e.error ? `ERROR: ${e.error}` : describe(e)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Fixture stage
// =============================================================================

function Stage({ fixture, log, mockConfig }: { fixture: Fixture; log: LogSink; mockConfig: MockConfig }) {
  return (
    <div className="inspector-stage">
      {fixture.notes && (
        <div
          style={{
            fontSize: 11,
            color: "var(--text-muted)",
            marginBottom: 16,
            padding: "8px 12px",
            background: "var(--bg-warm)",
            borderLeft: "2px solid var(--accent)",
            borderRadius: 2,
          }}
        >
          {fixture.notes}
        </div>
      )}
      <div style={{ maxWidth: 720 }}>{fixture.render({ log, mockConfig })}</div>
    </div>
  );
}

// =============================================================================
// Root
// =============================================================================

export function Inspector() {
  const [theme, setTheme] = useState<Theme>("light");
  const [selected, setSelected] = useState<{ entryId: string; fixtureId: string }>({
    entryId: REGISTRY[0].id,
    fixtureId: REGISTRY[0].fixtures[0].id,
  });
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [nextId, setNextId] = useState(1);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const log = useMemo<LogSink>(
    () => (entry) => {
      setLogs((prev) => {
        const next: LogEntry = { ...entry, id: prev.length + 1, timestamp: Date.now() };
        return [...prev, next];
      });
      setNextId((n) => n + 1);
    },
    []
  );

  // `nextId` is held to force a stable identity seed per session; not directly
  // read because `logs.length + 1` also works, but keeping it here in case we
  // later separate visible logs from the id counter.
  void nextId;

  const entry = REGISTRY.find((e) => e.id === selected.entryId) ?? REGISTRY[0];
  const fixture = entry.fixtures.find((f) => f.id === selected.fixtureId) ?? entry.fixtures[0];

  const mockConfig: MockConfig = { delayMs: 400 };

  return (
    <div className="inspector-shell">
      <TopBar
        theme={theme}
        onThemeChange={setTheme}
        onClearLog={() => setLogs([])}
      />
      <Sidebar
        selected={selected}
        onSelect={(entryId, fixtureId) => {
          setSelected({ entryId, fixtureId });
          setLogs([]);
        }}
      />
      <main className="inspector-main">
        <Stage fixture={fixture} log={log} mockConfig={mockConfig} />
        <LogPanel entries={logs} />
      </main>
    </div>
  );
}
