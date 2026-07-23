/**
 * Entry point for FAVES Compliance Dashboard MCP App
 */
import { mountApp } from "../create-app.tsx";
import FavesDashboard from "../faves-dashboard.tsx";

mountApp(FavesDashboard, {
  name: "NovoMCP FAVES Compliance Dashboard",
  version: "1.0.0",
});
