import DockingViewer from "../../docking-viewer.tsx";
import { makeMockProps } from "../mock-view-props.ts";
import type { Fixture } from "../registry.tsx";

const ESTIMATE_PHASE = {
  phase: "estimate" as const,
  protein_pdb_id: "4EY7",
  confirmation_token: "tok-mock-123",
  estimated_credits: 48,
  n_molecules: 12,
  exhaustiveness: 8,
  num_modes: 9,
  protonation_ph: 7.4,
  credit_breakdown: {
    base_cost: 12,
    per_molecule_cost: 3,
    molecule_count: 12,
    total_credits: 48,
  },
  // Original-call inputs the viewer will need to re-invoke the tool on confirm.
  smiles_list: [
    "O=C(NCCc1ccc(O)cc1)c1ccc2c(c1)OCO2",
    "Cc1ccc(NC(=O)CN2CCN(c3ccccc3)CC2)cc1",
  ],
};

const COMPLETED_WITH_REFERENCE = {
  protein_pdb_id: "4EY7",
  protein_name: "Acetylcholinesterase in complex with donepezil",
  resolution: 2.4,
  method: "X-RAY DIFFRACTION",
  organism: "Homo sapiens",
  chains: ["A"],
  ligands: ["E20"],
  binding_site_source: "known" as const,
  exhaustiveness: 8,
  num_modes: 9,
  protonation_ph: 7.4,
  molecules_docked: 4,
  molecules_failed: 0,
  credits_consumed: 24,
  best_affinity_kcal: -10.2,
  reference_affinity_kcal: -9.4,
  reference_ligand_smiles: "COc1cc2c(cc1OC)C(=O)C(CC2)Cc1ccncc1",
  reference_source: "co_crystallized" as const,
  native_ligand: { residue_name: "E20" },
  results: [
    {
      smiles: "O=C(NCCc1ccc(O)cc1)c1ccc2c(c1)OCO2",
      binding_affinity_kcal: -10.2,
      delta_vs_best_kcal: 0.0,
      delta_vs_reference_kcal: -0.8,
      poses: 9,
      contacts: [
        { type: "hbond", residue: "TYR337", chain: "A", distance_A: 2.9 },
        { type: "pi_stacking", residue: "TRP86", chain: "A", distance_A: 4.2 },
        { type: "hydrophobic", residue: "PHE295", chain: "A", distance_A: 3.8 },
      ],
      interaction_summary: {
        n_hbonds: 1,
        n_hydrophobic: 1,
        n_pi_stacking: 1,
        total_interactions: 3,
        key_residues: ["TYR337", "TRP86", "PHE295"],
      },
    },
    {
      smiles: "Cc1ccc(NC(=O)CN2CCN(c3ccccc3)CC2)cc1",
      binding_affinity_kcal: -8.9,
      delta_vs_best_kcal: 1.3,
      delta_vs_reference_kcal: 0.5,
      poses: 9,
      contacts: [{ type: "hydrophobic", residue: "TRP86", chain: "A", distance_A: 3.7 }],
    },
    {
      smiles: "O=S(=O)(N)c1ccc(NC(=O)c2cccnc2)cc1",
      binding_affinity_kcal: -7.1,
      delta_vs_best_kcal: 3.1,
      delta_vs_reference_kcal: 2.3,
      poses: 9,
      weak_binder: true,
    },
    {
      smiles: "CCO",
      binding_affinity_kcal: -4.2,
      delta_vs_best_kcal: 6.0,
      delta_vs_reference_kcal: 5.2,
      poses: 9,
      weak_binder: true,
    },
  ],
};

const SUBMITTED_PHASE = {
  phase: "submitted" as const,
  protein_pdb_id: "4EY7",
  job_id: "job-dock-mock-789",
  status: "Running",
  estimated_minutes: 4,
  molecules_docked: 2,
};

export const dockingViewerFixtures: Fixture[] = [
  {
    id: "estimate",
    label: "Estimate (passive notice)",
    notes:
      "Phase 1 response. Renders a transient cost notice (credits + 'Queueing…') while Claude re-invokes dock_molecules with the confirmation_token in the same turn. Approval gate was tried and dropped — see Cookbook §11.2 for why.",
    render: ({ log }) => (
      <DockingViewer
        {...makeMockProps({ toolInputs: ESTIMATE_PHASE }, log)}
      />
    ),
  },
  {
    id: "submitted",
    label: "Async submitted",
    notes: "Batch job polling state — shows BatchStatus.",
    render: ({ log }) => (
      <DockingViewer
        {...makeMockProps({ toolInputs: SUBMITTED_PHASE }, log)}
      />
    ),
  },
  {
    id: "completed",
    label: "Completed with reference",
    notes: "Full result with co-crystallized reference ligand — click any row or contact chip to trigger sendMessage.",
    render: ({ log }) => (
      <DockingViewer
        {...makeMockProps({ toolInputs: COMPLETED_WITH_REFERENCE }, log)}
      />
    ),
  },
];
