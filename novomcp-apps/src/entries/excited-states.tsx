/**
 * Entry point for Excited States (sTDA-xTB) MCP App
 */
import { mountApp } from "../create-app.tsx";
import ExcitedStatesViewer from "../excited-states.tsx";

mountApp(ExcitedStatesViewer, {
  name: "NovoMCP Excited States (sTDA-xTB)",
  version: "1.0.0",
});
