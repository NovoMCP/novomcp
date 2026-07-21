/**
 * Entry point for Transition State (NEB) MCP App
 */
import { mountApp } from "../create-app.tsx";
import TransitionStateViewer from "../transition-state.tsx";

mountApp(TransitionStateViewer, {
  name: "NovoMCP Transition State (NEB)",
  version: "1.0.0",
});
