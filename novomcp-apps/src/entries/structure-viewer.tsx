/**
 * Entry point for Structure Viewer MCP App
 */
import { mountApp } from "../create-app.tsx";
import StructureViewer from "../structure-viewer.tsx";

mountApp(StructureViewer, {
  name: "NovoMCP Structure Viewer",
  version: "1.0.0",
});
