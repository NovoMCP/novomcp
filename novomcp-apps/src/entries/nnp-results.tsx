/**
 * Entry point for NNP Results MCP App (compute_energy + optimize_geometry_nnp)
 */
import { mountApp } from "../create-app.tsx";
import NnpResultsViewer from "../nnp-results.tsx";

mountApp(NnpResultsViewer, {
  name: "NovoMCP NNP Results",
  version: "1.0.0",
});
