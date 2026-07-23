/**
 * Entry point for the Message Inspector dev harness.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../global.css";
import { Inspector } from "../inspector/main.tsx";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Inspector />
  </StrictMode>
);
