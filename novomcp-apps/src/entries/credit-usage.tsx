/**
 * Entry point for Credit Usage Dashboard MCP App
 */
import { mountApp } from "../create-app.tsx";
import CreditUsageViewer from "../credit-usage.tsx";

mountApp(CreditUsageViewer, {
  name: "NovoMCP Credit Usage",
  version: "1.0.0",
});
