import { ApprovalPrompt } from "../../providers/approval.tsx";
import type { Fixture } from "../registry.tsx";

export const approvalFixtures: Fixture[] = [
  {
    id: "docking-estimate",
    label: "Docking cost estimate",
    notes:
      "Typical credit-metered action. Confirm triggers a mock callServerTool; Cancel triggers a sendMessage.",
    render: ({ log, mockConfig }) => (
      <ApprovalPrompt
        title="Docking Cost Estimate"
        summary="Dock 12 molecules against 4EY7 (acetylcholinesterase). Exhaustiveness 8, 9 poses each."
        cost={{
          total: 48,
          unit: "Credits",
          breakdown: [
            { label: "Base", amount: 12 },
            { label: "Per molecule × 12", amount: 36 },
          ],
        }}
        details={
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--text-soft)" }}>
            <li>Protein: human AChE, 2.4 Å X-ray</li>
            <li>Binding site: co-crystallized (donepezil)</li>
            <li>Protonation pH: 7.4</li>
          </ul>
        }
        onConfirm={async () => {
          log({
            kind: "callServerTool",
            payload: { name: "dock_molecules", arguments: { confirmation_token: "mock-token-xyz" } },
          });
          await new Promise((r) => setTimeout(r, mockConfig.delayMs ?? 0));
        }}
        onCancel={() => {
          log({
            kind: "sendMessage",
            payload: { role: "user", content: [{ type: "text", text: "Cancel the docking run." }] },
          });
        }}
      />
    ),
  },
  {
    id: "destructive-delete",
    label: "Destructive delete",
    notes: "Destructive style (red confirm). No cost.",
    render: ({ log }) => (
      <ApprovalPrompt
        title="Delete Pipeline Funnel"
        summary="Remove funnel `lead-opt-Q2` and all 14 saved stages. This cannot be undone."
        destructive
        confirmLabel="Delete"
        onConfirm={async () => {
          log({
            kind: "callServerTool",
            payload: { name: "delete_funnel", arguments: { funnel_id: "lead-opt-Q2" } },
          });
        }}
        onCancel={() => {
          log({ kind: "sendMessage", payload: { role: "user", content: [{ type: "text", text: "Keep the funnel." }] } });
        }}
      />
    ),
  },
  {
    id: "failing-confirm",
    label: "Confirm fails (server error)",
    notes:
      "Simulates a backend error during confirm — approval drops to the Failed state with Retry.",
    render: () => (
      <ApprovalPrompt
        title="Lead Optimization"
        summary="Run 3-round lead optimization across 8 scaffolds."
        cost={{ total: 240 }}
        onConfirm={async () => {
          await new Promise((r) => setTimeout(r, 300));
          throw new Error("Upstream rate limit: retry in 30s");
        }}
        onCancel={() => {}}
      />
    ),
  },
  {
    id: "no-cost",
    label: "Minimal (no cost, no cancel)",
    notes: "Smallest viable approval — just a confirm.",
    render: ({ log }) => (
      <ApprovalPrompt
        title="Proceed with Validation"
        summary="Run adversarial target checkpoint on EGFR/NSCLC?"
        onConfirm={() => {
          log({
            kind: "callServerTool",
            payload: { name: "validate_target", arguments: { gene: "EGFR", indication: "NSCLC" } },
          });
        }}
      />
    ),
  },
];
