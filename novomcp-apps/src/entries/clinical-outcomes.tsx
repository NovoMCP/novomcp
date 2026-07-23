/**
 * Entry point for Clinical Outcomes (NovoExpert) MCP App
 */
import { mountApp } from "../create-app.tsx";
import ClinicalOutcomesViewer from "../clinical-outcomes.tsx";

mountApp(ClinicalOutcomesViewer, {
  name: "NovoMCP Clinical Outcomes (NovoExpert v3)",
  version: "1.0.0",
});
