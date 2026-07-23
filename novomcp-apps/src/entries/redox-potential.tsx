/**
 * Entry point for Electrolyte Redox Potential MCP App
 */
import { mountApp } from "../create-app.tsx";
import RedoxPotentialViewer from "../redox-potential.tsx";

mountApp(RedoxPotentialViewer, {
  name: "NovoMCP Electrolyte Redox Potential",
  version: "1.0.0",
});
