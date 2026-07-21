/**
 * Entry point for Pipeline Jobs MCP App
 */
import { mountApp } from "../create-app.tsx";
import JobsViewer from "../jobs.tsx";

mountApp(JobsViewer, {
  name: "NovoMCP Pipeline Jobs",
  version: "1.0.0",
});
