/**
 * Entry point for Solubility Prediction MCP App
 */
import { mountApp } from "../create-app.tsx";
import PredictSolubilityViewer from "../predict-solubility.tsx";

mountApp(PredictSolubilityViewer, {
  name: "NovoMCP Solubility Prediction",
  version: "1.0.0",
});
