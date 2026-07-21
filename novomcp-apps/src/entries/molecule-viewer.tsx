/**
 * Entry point for Molecule Viewer MCP App
 */
import { mountApp } from "../create-app.tsx";
import MoleculeViewer from "../molecule-viewer.tsx";

mountApp(MoleculeViewer, {
  name: "NovoMCP Molecule Viewer",
  version: "1.0.0",
});
