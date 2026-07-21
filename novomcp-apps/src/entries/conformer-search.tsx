/**
 * Entry point for Conformer Search MCP App
 */
import { mountApp } from "../create-app.tsx";
import ConformerSearchViewer from "../conformer-search.tsx";

mountApp(ConformerSearchViewer, {
  name: "NovoMCP Conformer Search",
  version: "1.0.0",
});
