/**
 * Entry point for Docking Viewer MCP App
 */
import { mountApp } from "../create-app.tsx";
import DockingViewer from "../docking-viewer.tsx";

mountApp(DockingViewer, {
  name: "NovoMCP Docking Viewer",
  version: "1.0.0",
});
