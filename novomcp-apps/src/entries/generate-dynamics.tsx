/**
 * Entry point for Conformational Dynamics (AlphaFlow) MCP App
 */
import { mountApp } from "../create-app.tsx";
import GenerateDynamicsViewer from "../generate-dynamics.tsx";

mountApp(GenerateDynamicsViewer, {
  name: "NovoMCP Conformational Dynamics",
  version: "1.0.0",
});
