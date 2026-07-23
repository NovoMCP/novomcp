/**
 * Entry point for Target Discovery MCP App
 */
import { mountApp } from "../create-app.tsx";
import TargetDiscoveryViewer from "../target-discovery.tsx";

mountApp(TargetDiscoveryViewer, {
  name: "NovoMCP Target Discovery",
  version: "1.0.0",
});
