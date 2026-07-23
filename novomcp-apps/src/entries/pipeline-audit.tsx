/**
 * Entry point for Pipeline Audit MCP App
 */
import { mountApp } from "../create-app.tsx";
import PipelineAuditViewer from "../pipeline-audit.tsx";

mountApp(PipelineAuditViewer, {
  name: "NovoMCP Pipeline Audit",
  version: "1.0.0",
});
