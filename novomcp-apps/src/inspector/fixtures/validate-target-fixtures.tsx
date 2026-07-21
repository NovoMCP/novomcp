import ValidateTargetViewer from "../../validate-target.tsx";
import { makeMockProps } from "../mock-view-props.ts";
import type { Fixture } from "../registry.tsx";

const EGFR_MATURE = {
  target: "EGFR",
  disease: "NSCLC",
  confidence_score: 0.88,
  confidence_level: "high",
  recommendation: "proceed",
  target_maturity: "mature_validated",
  evidence: {
    omics: {
      composite_score: 0.91,
      genetic_score: 0.85,
      expression_score: 0.93,
      tractable: true,
      known_drugs: 7,
      high_competition: true,
      suggested_pdb_id: "4HJO",
    },
    clinical_trials: {
      completed: 84,
      terminated: 22,
      phase3_failures: 3,
      key_successes: [
        { nct_id: "NCT00322452", title: "Gefitinib vs Carboplatin/Paclitaxel in advanced NSCLC", phase: "3", status: "completed" },
        { nct_id: "NCT02296125", title: "Osimertinib in EGFRm-positive NSCLC", phase: "3", status: "completed" },
      ],
      key_failures: [
        { nct_id: "NCT00789750", title: "Cetuximab adjuvant in EGFR-mutant NSCLC", phase: "3", status: "terminated", reason: "efficacy futility" },
      ],
    },
    literature: {
      supporting_papers: 412,
      contradicting_papers: 18,
      top_supporting: [
        { title: "Mutations of the epidermal growth factor receptor gene", year: 2004, journal: "Science" },
        { title: "Osimertinib in untreated EGFR-mutated NSCLC", year: 2018, journal: "NEJM" },
      ],
    },
    chembl: {
      activity_count: 14820,
      best_pchembl: 11.2,
      assay_types: ["IC50", "Ki", "Kd"],
    },
  },
  strengths: [
    "Mature validated target: 7 approved drugs (erlotinib, gefitinib, osimertinib, afatinib, dacomitinib, amivantamab, mobocertinib)",
    "Canonical NSCLC oncogenic driver with well-characterized resistance pathways",
    "Active competitive landscape validates mechanism",
  ],
  risk_factors: [
    "T790M and C797S resistance mutations are known liabilities for 1st/2nd-gen inhibitors",
  ],
};

const NOVEL_LOW = {
  target: "CMTR2",
  disease: "glioblastoma",
  confidence_score: 0.34,
  confidence_level: "low",
  recommendation: "reconsider",
  target_maturity: "novel",
  evidence: {
    omics: {
      composite_score: 0.42,
      genetic_score: 0.31,
      expression_score: 0.52,
      tractable: false,
      known_drugs: 0,
      high_competition: false,
      suggested_pdb_id: null,
    },
    clinical_trials: {
      completed: 0,
      terminated: 0,
      phase3_failures: 0,
      key_successes: [],
      key_failures: [],
    },
    literature: {
      supporting_papers: 4,
      contradicting_papers: 1,
      top_supporting: [
        { title: "Cap methyltransferase CMTR2 in glioblastoma stem cells", year: 2023, journal: "Oncogene" },
      ],
    },
    chembl: {
      activity_count: 0,
    },
  },
  risk_factors: [
    "No approved drugs and no co-crystal structures — tractability unknown",
    "Single-paper disease link; no genetic validation from GWAS or rare-variant studies",
    "No active clinical programs in the indication",
  ],
  strengths: ["Open competitive landscape if validation succeeds"],
  partial_data: ["ChEMBL returned 0 assays — tool calls may have failed upstream"],
};

export const validateTargetFixtures: Fixture[] = [
  {
    id: "egfr-mature",
    label: "EGFR (mature validated)",
    notes:
      "The canonical mature target. Should render recommendation=proceed, target_maturity pill, all 4 evidence streams full.",
    render: ({ log }) => (
      <ValidateTargetViewer
        {...makeMockProps({ toolInputs: EGFR_MATURE }, log)}
      />
    ),
  },
  {
    id: "novel-low",
    label: "Novel target (low confidence)",
    notes: "Reconsider verdict with partial data warning and sparse evidence — tests empty-state handling.",
    render: ({ log }) => (
      <ValidateTargetViewer
        {...makeMockProps({ toolInputs: NOVEL_LOW }, log)}
      />
    ),
  },
  {
    id: "streaming",
    label: "Streaming (no data yet)",
    notes: "toolInputs null — should render a loading state.",
    render: ({ log }) => (
      <ValidateTargetViewer
        {...makeMockProps({ toolInputs: null, toolInputsPartial: null, toolResult: null }, log)}
      />
    ),
  },
];
