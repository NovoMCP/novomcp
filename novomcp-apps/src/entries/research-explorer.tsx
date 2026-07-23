/**
 * Entry point for Research Explorer MCP App
 */
import { mountApp } from "../create-app.tsx";
import ResearchExplorer from "../research-explorer.tsx";

mountApp(ResearchExplorer, {
  name: "NovoMCP Research Explorer",
  version: "1.0.0",
});
