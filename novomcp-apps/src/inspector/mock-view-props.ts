/**
 * Mock implementations of the MCP App host callbacks for the inspector.
 *
 * Every callback records into a log buffer and resolves immediately (or
 * with a simulated delay / error when configured). This lets us render
 * viewers outside the real MCP host and still observe what they would
 * emit to the chat.
 */
import type { ViewProps } from "../create-app.tsx";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";

// =============================================================================
// Log entry + sink
// =============================================================================

export interface LogEntry {
  id: number;
  timestamp: number;
  kind: "callServerTool" | "sendMessage" | "openLink" | "sendLog";
  payload: unknown;
  error?: string;
}

export type LogSink = (entry: Omit<LogEntry, "id" | "timestamp">) => void;

// =============================================================================
// Stub configuration
// =============================================================================

export interface MockConfig {
  /** If set, `callServerTool` returns this payload (wrapped as CallToolResult). */
  callServerToolResponse?: unknown;
  /** If set, `callServerTool` rejects with this error message. */
  callServerToolError?: string;
  /** Artificial delay (ms) applied to every async call. */
  delayMs?: number;
}

// =============================================================================
// Factory
// =============================================================================

function delay(ms?: number): Promise<void> {
  if (!ms) return Promise.resolve();
  return new Promise((r) => setTimeout(r, ms));
}

export function makeMockProps<T>(
  partial: Partial<ViewProps<T>>,
  log: LogSink,
  config: MockConfig = {}
): ViewProps<T> {
  const callServerTool = async (params: unknown) => {
    log({ kind: "callServerTool", payload: params });
    await delay(config.delayMs);
    if (config.callServerToolError) {
      const err = new Error(config.callServerToolError);
      log({ kind: "callServerTool", payload: params, error: config.callServerToolError });
      throw err;
    }
    const result = (config.callServerToolResponse ?? { ok: true }) as unknown;
    return {
      content: [{ type: "text", text: JSON.stringify(result) }],
      structuredContent: result as Record<string, unknown>,
    } as CallToolResult;
  };

  const sendMessage = async (params: unknown) => {
    log({ kind: "sendMessage", payload: params });
    await delay(config.delayMs);
    return {} as { [x: string]: unknown };
  };

  const openLink = async (params: unknown) => {
    log({ kind: "openLink", payload: params });
    await delay(config.delayMs);
    return {} as { [x: string]: unknown };
  };

  const sendLog = (params: unknown) => {
    log({ kind: "sendLog", payload: params });
  };

  return {
    toolInputs: null,
    toolInputsPartial: null,
    toolResult: null,
    hostContext: null,
    callServerTool: callServerTool as ViewProps<T>["callServerTool"],
    sendMessage: sendMessage as ViewProps<T>["sendMessage"],
    openLink: openLink as ViewProps<T>["openLink"],
    sendLog: sendLog as ViewProps<T>["sendLog"],
    ...partial,
  };
}
