/**
 * Entry point for ADMET Dashboard MCP App
 */
import { mountApp } from "../create-app.tsx";
import AdmetDashboard from "../admet-dashboard.tsx";

mountApp(AdmetDashboard, {
  name: "NovoMCP ADMET Dashboard",
  version: "1.0.0",
});
