/**
 * Entry point for pKa Prediction MCP App
 */
import { mountApp } from "../create-app.tsx";
import PredictPkaViewer from "../predict-pka.tsx";

mountApp(PredictPkaViewer, {
  name: "NovoMCP pKa Prediction",
  version: "1.0.0",
});
