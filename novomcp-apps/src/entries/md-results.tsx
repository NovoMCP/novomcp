/**
 * Entry point for MD Results Visualization MCP App
 */
import { mountApp } from "../create-app.tsx";
import MdResultsViewer from "../md-results.tsx";

mountApp(MdResultsViewer, {
  name: "NovoMCP MD Results",
  version: "1.0.0",
});
