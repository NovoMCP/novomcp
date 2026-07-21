/**
 * Entry point for QM Calculation MCP App
 */
import { mountApp } from "../create-app.tsx";
import QmCalculationViewer from "../qm-calculation.tsx";

mountApp(QmCalculationViewer, {
  name: "NovoMCP QM Calculation",
  version: "1.0.0",
});
