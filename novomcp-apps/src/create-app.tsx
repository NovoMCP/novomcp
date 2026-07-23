/**
 * NovoMCP App Factory
 *
 * Creates MCP App wrappers for any component.
 * Handles connection, streaming, and host context.
 */
import type { App, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { useApp, useHostStyles } from "@modelcontextprotocol/ext-apps/react";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { StrictMode, useState, useCallback, useEffect, ComponentType } from "react";
import { createRoot } from "react-dom/client";
import "./global.css";

// =============================================================================
// Types
// =============================================================================

/**
 * Props passed to MCP App view components.
 */
export interface ViewProps<TToolInput = Record<string, unknown>> {
  /** Complete tool input (after streaming finishes) */
  toolInputs: TToolInput | null;
  /** Partial tool input (during streaming) */
  toolInputsPartial: TToolInput | null;
  /** Tool execution result from the server */
  toolResult: CallToolResult | null;
  /** Host context (theme, dimensions, locale, etc.) */
  hostContext: McpUiHostContext | null;
  /** Call a tool on the MCP server */
  callServerTool: App["callServerTool"];
  /** Send a message to the host's chat */
  sendMessage: App["sendMessage"];
  /** Request the host to open a URL */
  openLink: App["openLink"];
  /** Send log messages to the host */
  sendLog: App["sendLog"];
}

interface AppConfig {
  name: string;
  version: string;
}

// =============================================================================
// MCP App Wrapper Factory
// =============================================================================

function createMcpAppWrapper<TToolInput>(
  ViewComponent: ComponentType<ViewProps<TToolInput>>,
  config: AppConfig
) {
  return function McpAppWrapper() {
    const [toolInputs, setToolInputs] = useState<TToolInput | null>(null);
    const [toolInputsPartial, setToolInputsPartial] = useState<TToolInput | null>(null);
    const [toolResult, setToolResult] = useState<CallToolResult | null>(null);
    const [hostContext, setHostContext] = useState<McpUiHostContext | null>(null);

    const { app, error } = useApp({
      appInfo: { name: config.name, version: config.version },
      capabilities: {},
      onAppCreated: (app) => {
        app.ontoolinput = (params) => {
          setToolInputs(params.arguments as TToolInput);
          setToolInputsPartial(null);
        };
        app.ontoolinputpartial = (params) => {
          setToolInputsPartial(params.arguments as TToolInput);
        };
        app.ontoolresult = (params) => {
          setToolResult(params as CallToolResult);
        };
        app.onhostcontextchanged = (params) => {
          setHostContext((prev) => ({ ...prev, ...params }));
        };
      },
    });

    useHostStyles(app);

    useEffect(() => {
      if (app) {
        const ctx = app.getHostContext();
        if (ctx) {
          setHostContext(ctx);
        }
      }
    }, [app]);

    const callServerTool = useCallback<App["callServerTool"]>(
      (params, options) => app!.callServerTool(params, options),
      [app]
    );
    const sendMessage = useCallback<App["sendMessage"]>(
      (params, options) => app!.sendMessage(params, options),
      [app]
    );
    const openLink = useCallback<App["openLink"]>(
      (params, options) => app!.openLink(params, options),
      [app]
    );
    const sendLog = useCallback<App["sendLog"]>(
      (params) => app!.sendLog(params),
      [app]
    );

    if (error) {
      return <div className="error">Error: {error.message}</div>;
    }

    if (!app) {
      return (
        <div className="loading">
          <div className="loading-spinner" />
          <span>Connecting to NovoMCP...</span>
        </div>
      );
    }

    return (
      <ViewComponent
        toolInputs={toolInputs}
        toolInputsPartial={toolInputsPartial}
        toolResult={toolResult}
        hostContext={hostContext}
        callServerTool={callServerTool}
        sendMessage={sendMessage}
        openLink={openLink}
        sendLog={sendLog}
      />
    );
  };
}

// =============================================================================
// App Mounting
// =============================================================================

export function mountApp<TToolInput>(
  ViewComponent: ComponentType<ViewProps<TToolInput>>,
  config: AppConfig
) {
  const AppWrapper = createMcpAppWrapper(ViewComponent, config);

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <AppWrapper />
    </StrictMode>
  );
}

