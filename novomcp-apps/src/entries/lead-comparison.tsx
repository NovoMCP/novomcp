/**
 * Entry point for Lead Comparison MCP App
 */
import { mountApp } from "../create-app.tsx";
import LeadComparison from "../lead-comparison.tsx";

mountApp(LeadComparison, {
  name: "NovoMCP Lead Comparison",
  version: "1.0.0",
});
