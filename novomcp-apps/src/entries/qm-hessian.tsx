/**
 * Entry point for QM Hessian (Vibrational Frequencies & Thermochemistry) MCP App
 */
import { mountApp } from "../create-app.tsx";
import QmHessianViewer from "../qm-hessian.tsx";

mountApp(QmHessianViewer, {
  name: "NovoMCP Vibrational Frequencies & Thermochemistry",
  version: "1.0.0",
});
