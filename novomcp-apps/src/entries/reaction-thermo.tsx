/**
 * Entry point for Reaction Thermodynamics MCP App
 */
import { mountApp } from "../create-app.tsx";
import ReactionThermoViewer from "../reaction-thermo.tsx";

mountApp(ReactionThermoViewer, {
  name: "NovoMCP Reaction Thermodynamics",
  version: "1.0.0",
});
