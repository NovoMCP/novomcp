/**
 * Entry point for BDE Prediction MCP App
 */
import { mountApp } from "../create-app.tsx";
import PredictBdeViewer from "../predict-bde.tsx";

mountApp(PredictBdeViewer, {
  name: "NovoMCP BDE Prediction",
  version: "1.0.0",
});
