/**
 * Entry point for Materials Project Search MCP App
 */
import { mountApp } from "../create-app.tsx";
import MaterialsProjectViewer from "../materials-project.tsx";

mountApp(MaterialsProjectViewer, {
  name: "NovoMCP Materials Project Search",
  version: "1.0.0",
});
