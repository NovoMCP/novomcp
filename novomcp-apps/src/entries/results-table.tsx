/**
 * Entry point for Molecule Results Table MCP App
 */
import { mountApp } from "../create-app.tsx";
import ResultsTableViewer from "../results-table.tsx";

mountApp(ResultsTableViewer, {
  name: "NovoMCP Molecule Results",
  version: "1.0.0",
});
