/**
 * Entry point for Target Validation MCP App
 */
import { mountApp } from "../create-app.tsx";
import ValidateTargetViewer from "../validate-target.tsx";

mountApp(ValidateTargetViewer, {
  name: "NovoMCP Target Validation",
  version: "1.0.0",
});
