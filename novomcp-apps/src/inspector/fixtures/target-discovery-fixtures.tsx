import TargetDiscoveryViewer from "../../target-discovery.tsx";
import { makeMockProps } from "../mock-view-props.ts";
import type { Fixture } from "../registry.tsx";

const NSCLC_TARGETS = {
  disease: "NSCLC",
  disease_efo_id: "EFO_0003060",
  total_targets: 24,
  targets_dockable: 18,
  suggested_target: "EGFR",
  suggested_pdb_id: "4HJO",
  wall_time_seconds: 3.4,
  targets: [
    {
      gene_symbol: "EGFR",
      ensembl_id: "ENSG00000146648",
      overall_score: 0.92,
      composite_score: 0.91,
      genetic_score: 0.88,
      expression_score: 0.93,
      tractability_small_molecule: true,
      known_drugs_count: 7,
      high_competition: true,
      suggested_pdb_id: "4HJO",
      best_pdb_resolution_A: 2.75,
      pdb_selection_criteria: "Highest-resolution EGFR kinase domain with co-crystal erlotinib",
      top_pathways: ["RTK signaling", "PI3K/AKT", "RAS-MAPK"],
    },
    {
      gene_symbol: "KRAS",
      ensembl_id: "ENSG00000133703",
      overall_score: 0.84,
      composite_score: 0.81,
      genetic_score: 0.92,
      expression_score: 0.68,
      tractability_small_molecule: true,
      known_drugs_count: 2,
      high_competition: false,
      suggested_pdb_id: "6OIM",
      best_pdb_resolution_A: 2.1,
      top_pathways: ["RAS signaling", "RAF-MEK-ERK"],
    },
    {
      gene_symbol: "ALK",
      overall_score: 0.79,
      genetic_score: 0.76,
      expression_score: 0.82,
      tractability_small_molecule: true,
      known_drugs_count: 4,
      high_competition: true,
      suggested_pdb_id: "3LCT",
      best_pdb_resolution_A: 1.95,
      top_pathways: ["RTK signaling", "JAK-STAT"],
    },
    {
      gene_symbol: "MET",
      overall_score: 0.71,
      genetic_score: 0.63,
      expression_score: 0.78,
      tractability_small_molecule: true,
      known_drugs_count: 1,
      high_competition: false,
      suggested_pdb_id: "3ZXZ",
      best_pdb_resolution_A: 2.3,
      top_pathways: ["HGF signaling", "RTK signaling"],
    },
    {
      gene_symbol: "RET",
      overall_score: 0.66,
      genetic_score: 0.58,
      expression_score: 0.74,
      tractability_small_molecule: true,
      known_drugs_count: 2,
      high_competition: false,
      suggested_pdb_id: "2IVU",
      best_pdb_resolution_A: 2.5,
      top_pathways: ["RTK signaling", "RAS-MAPK"],
    },
    {
      gene_symbol: "TP53",
      overall_score: 0.58,
      genetic_score: 0.94,
      expression_score: 0.22,
      tractability_small_molecule: false,
      known_drugs_count: 0,
      high_competition: false,
      suggested_pdb_id: null,
      structure_unavailable: true,
      top_pathways: ["DNA damage response"],
    },
  ],
};

const NOVEL_DISEASE = {
  disease: "frontotemporal dementia",
  total_targets: 3,
  targets_dockable: 1,
  suggested_target: "MAPT",
  suggested_pdb_id: "5O3L",
  targets: [
    {
      gene_symbol: "MAPT",
      overall_score: 0.54,
      genetic_score: 0.72,
      expression_score: 0.48,
      tractability_small_molecule: true,
      known_drugs_count: 0,
      high_competition: false,
      suggested_pdb_id: "5O3L",
      top_pathways: ["Microtubule assembly"],
    },
    {
      gene_symbol: "GRN",
      overall_score: 0.41,
      known_drugs_count: 0,
      suggested_pdb_id: null,
      structure_unavailable: true,
      top_pathways: ["Lysosomal biology"],
    },
    {
      gene_symbol: "C9orf72",
      overall_score: 0.38,
      known_drugs_count: 0,
      suggested_pdb_id: null,
      structure_unavailable: true,
      top_pathways: ["Autophagy"],
    },
  ],
};

export const targetDiscoveryFixtures: Fixture[] = [
  {
    id: "nsclc-ranked",
    label: "NSCLC — top 6 ranked",
    notes:
      "Full target list with Choice next-step panel at the top. Click a Choice card to trigger sendMessage asking Claude to validate. Click any table row for a deeper briefing prompt.",
    render: ({ log }) => (
      <TargetDiscoveryViewer
        {...makeMockProps({ toolInputs: NSCLC_TARGETS as any }, log)}
      />
    ),
  },
  {
    id: "novel-sparse",
    label: "Novel disease — sparse results",
    notes: "Only 3 targets, most lack structures. Stress-tests Choice with small option count + 'no dockable structure' tags.",
    render: ({ log }) => (
      <TargetDiscoveryViewer
        {...makeMockProps({ toolInputs: NOVEL_DISEASE as any }, log)}
      />
    ),
  },
];
