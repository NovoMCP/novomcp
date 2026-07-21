/**
 * Entry point for Patient Stratification MCP App
 */
import { mountApp } from "../create-app.tsx";
import StratifyPatientsViewer from "../stratify-patients.tsx";

mountApp(StratifyPatientsViewer, {
  name: "NovoMCP Patient Stratification",
  version: "1.0.0",
});
