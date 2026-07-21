import { Choice } from "../../providers/choice.tsx";
import type { Fixture } from "../registry.tsx";

export const choiceFixtures: Fixture[] = [
  {
    id: "target-pick",
    label: "Pick a validated target",
    notes: "Typical target-discovery → validate_target handoff. Sends a prompt on select.",
    render: ({ log }) => (
      <Choice
        title="Which target should we validate?"
        description="Top 5 hits from target_discovery for NSCLC. Click one to kick off adversarial validation."
        options={[
          {
            id: "EGFR",
            title: "EGFR",
            subtitle: "Epidermal growth factor receptor",
            rank: 1,
            metric: { label: "Score", value: "0.92", tone: "success" },
            tags: [
              { label: "5 approved drugs", variant: "success" },
              { label: "Mature validated", variant: "success" },
            ],
            description: "Canonical NSCLC driver. Strong clinical precedent, mature resistance literature.",
          },
          {
            id: "KRAS",
            title: "KRAS",
            subtitle: "GTPase KRas",
            rank: 2,
            metric: { label: "Score", value: "0.84", tone: "accent" },
            tags: [
              { label: "2 approved drugs", variant: "success" },
              { label: "G12C only", variant: "warning" },
            ],
            description: "Sotorasib/adagrasib validated G12C; rest of alleles remain open.",
          },
          {
            id: "ALK",
            title: "ALK",
            subtitle: "Anaplastic lymphoma kinase",
            rank: 3,
            metric: { label: "Score", value: "0.79", tone: "accent" },
            tags: [{ label: "4 approved drugs", variant: "success" }],
          },
          {
            id: "MET",
            title: "MET",
            subtitle: "Hepatocyte growth factor receptor",
            rank: 4,
            metric: { label: "Score", value: "0.71" },
            tags: [
              { label: "1 approved drug", variant: "success" },
              { label: "exon14 skipping", variant: "warning" },
            ],
          },
          {
            id: "RET",
            title: "RET",
            subtitle: "Proto-oncogene tyrosine-protein kinase",
            rank: 5,
            metric: { label: "Score", value: "0.66" },
            tags: [{ label: "2 approved drugs", variant: "success" }],
          },
        ]}
        onSelect={(opt) => {
          log({
            kind: "sendMessage",
            payload: {
              role: "user",
              content: [{ type: "text", text: `Validate ${opt.id} for NSCLC.` }],
            },
          });
        }}
        onCancel={() => {
          log({ kind: "sendMessage", payload: { role: "user", content: [{ type: "text", text: "Skip validation." }] } });
        }}
      />
    ),
  },
  {
    id: "minimal",
    label: "Minimal (no metric, no tags)",
    notes: "Pure label selection — stress-tests empty-state styling.",
    render: ({ log }) => (
      <Choice
        title="Pick a docking backend"
        options={[
          { id: "vina", title: "AutoDock Vina", description: "Fast, widely validated." },
          { id: "smina", title: "smina", description: "Vina fork with user-defined scoring." },
          { id: "qvina", title: "QuickVina 2", description: "Highest throughput, slight accuracy drop." },
        ]}
        onSelect={(opt) => {
          log({ kind: "callServerTool", payload: { name: "dock_molecules", arguments: { backend: opt.id } } });
        }}
      />
    ),
  },
  {
    id: "warning-danger",
    label: "Mixed risk tags",
    notes: "Exercises success / warning / danger tag variants side by side.",
    render: ({ log }) => (
      <Choice
        title="Select a lead scaffold"
        options={[
          {
            id: "scaffold-A",
            title: "Scaffold A",
            rank: 1,
            metric: { label: "ΔG", value: "−9.1", tone: "success" },
            tags: [
              { label: "Ro5 compliant", variant: "success" },
              { label: "hERG flag", variant: "warning" },
            ],
          },
          {
            id: "scaffold-B",
            title: "Scaffold B",
            rank: 2,
            metric: { label: "ΔG", value: "−8.4", tone: "accent" },
            tags: [
              { label: "clean ADMET", variant: "success" },
              { label: "PAINS hit", variant: "danger" },
            ],
          },
          {
            id: "scaffold-C",
            title: "Scaffold C",
            rank: 3,
            metric: { label: "ΔG", value: "−7.7" },
            tags: [{ label: "novel chemotype", variant: "neutral" }],
          },
        ]}
        onSelect={(opt) => {
          log({ kind: "sendMessage", payload: { role: "user", content: [{ type: "text", text: `Advance ${opt.id}.` }] } });
        }}
      />
    ),
  },
];
