import { FormInput } from "../../providers/form-input.tsx";
import type { Fixture } from "../registry.tsx";

export const formInputFixtures: Fixture[] = [
  {
    id: "stratify-patients",
    label: "Stratify patients params",
    notes: "Typical parameter form before calling a tool. Mix of required / optional / select fields.",
    render: ({ log }) => (
      <FormInput
        title="Stratify Patients"
        description="Collect the inputs we need before running pharmacogenomic stratification."
        fields={[
          { name: "target_gene", label: "Target gene", type: "text", required: true, placeholder: "EGFR" },
          { name: "indication", label: "Indication", type: "text", placeholder: "NSCLC" },
          { name: "smiles", label: "Candidate SMILES", type: "smiles", placeholder: "CC(C)..." },
          {
            name: "ancestry_focus",
            label: "Ancestry focus",
            type: "select",
            options: [
              { value: "global", label: "Global" },
              { value: "EAS", label: "East Asian" },
              { value: "AFR", label: "African" },
              { value: "EUR", label: "European" },
            ],
            defaultValue: "global",
          },
        ]}
        onSubmit={(values) => {
          log({
            kind: "callServerTool",
            payload: { name: "stratify_patients", arguments: values },
          });
        }}
        onCancel={() => {
          log({ kind: "sendMessage", payload: { role: "user", content: [{ type: "text", text: "Skip stratification." }] } });
        }}
      />
    ),
  },
  {
    id: "numeric-validation",
    label: "Numeric validation",
    notes: "Tests the number coercion + min/max validators.",
    render: ({ log }) => (
      <FormInput
        title="Conformer Search"
        fields={[
          { name: "smiles", label: "SMILES", type: "smiles", required: true, placeholder: "c1ccccc1" },
          { name: "n_conformers", label: "Number of conformers", type: "number", required: true, min: 1, max: 500, defaultValue: 50 },
          { name: "energy_window_kcal", label: "Energy window (kcal/mol)", type: "number", min: 0, max: 20, defaultValue: 6 },
          { name: "notes", label: "Notes", type: "multiline", placeholder: "Optional run description" },
        ]}
        onSubmit={(values) => {
          log({ kind: "callServerTool", payload: { name: "run_conformer_search", arguments: values } });
        }}
      />
    ),
  },
  {
    id: "custom-validate",
    label: "Custom validator",
    notes: "SMILES with a toy validator (must contain C or c).",
    render: ({ log }) => (
      <FormInput
        title="Run QM Calculation"
        fields={[
          {
            name: "smiles",
            label: "SMILES",
            type: "smiles",
            required: true,
            validate: (raw) =>
              /[Cc]/.test(raw) ? null : "SMILES must contain at least one carbon",
          },
          {
            name: "method",
            label: "Method",
            type: "select",
            required: true,
            options: [
              { value: "DFT", label: "DFT" },
              { value: "HF", label: "Hartree-Fock" },
              { value: "MP2", label: "MP2" },
            ],
          },
        ]}
        onSubmit={(values) => {
          log({ kind: "callServerTool", payload: { name: "run_qm_calculation", arguments: values } });
        }}
      />
    ),
  },
];
