/**
 * Entry point for Frontier Orbital Analysis MCP App
 */
import { mountApp } from "../create-app.tsx";
import FrontierOrbitalsViewer from "../frontier-orbitals.tsx";

mountApp(FrontierOrbitalsViewer, {
  name: "NovoMCP Frontier Orbital Analysis",
  version: "1.0.0",
});
