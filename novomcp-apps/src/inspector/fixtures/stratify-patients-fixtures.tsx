import StratifyPatientsViewer from "../../stratify-patients.tsx";
import { makeMockProps } from "../mock-view-props.ts";
import type { Fixture } from "../registry.tsx";

const MOCK_RESULT = {
  smiles: "CC(C)n1c(/C=C/[C@@H](O)C[C@@H](O)CC(=O)O)c(-c2ccc(F)cc2)c(-c2ccccc2)n1",
  target_gene: "EGFR",
  indication: "NSCLC",
  pharmacogenomics: {
    primary_metabolism: ["CYP3A4", "CYP2D6"],
    CYP3A4_cpic_level: "A",
    CYP3A4_substrate_probability: 0.84,
    CYP3A4_clinical_implications: "Major metabolic route; PPI co-administration can halve AUC.",
    CYP2D6_cpic_level: "B",
    CYP2D6_substrate_probability: 0.41,
    pgx_risk_alleles: [
      { gene: "CYP2D6", allele: "*4", effect: "Poor metabolizer — consider dose reduction" },
      { gene: "CYP3A5", allele: "*3", effect: "Non-expresser in ~85% of Caucasians" },
    ],
  },
  population_coverage: {
    global_normal_metabolizer_pct: 64.2,
    by_ancestry: [
      { ancestry: "European", cyp: "CYP2D6", normal_metabolizer_pct: 71.4 },
      { ancestry: "East Asian", cyp: "CYP2D6", normal_metabolizer_pct: 58.9 },
      { ancestry: "African", cyp: "CYP2D6", normal_metabolizer_pct: 51.2 },
    ],
  },
  resistance: {
    known_mutations: [
      { mutation: "T790M", cancer_type: "NSCLC", clinvar_significance: "Pathogenic", affects_binding_site: true },
      { mutation: "C797S", cancer_type: "NSCLC", clinvar_significance: "Pathogenic", affects_binding_site: true },
    ],
    total_pathogenic_variants: 84,
    variants_near_binding_site: 12,
    resistance_risk: "moderate",
  },
  summary: {
    clinical_viability: "moderate",
    key_risks: [
      "T790M / C797S resistance in patients previously treated with 1st/2nd-gen EGFR inhibitors",
      "CYP3A4 inducer co-medication reduces plasma exposure",
    ],
    recommended_actions: [
      "Stratify trial to treatment-naive EGFRm NSCLC or include a C797S-active backup",
      "Exclude patients on strong CYP3A4 inducers (rifampin, carbamazepine, St. John's wort)",
    ],
  },
  wall_time_seconds: 4.2,
};

export const stratifyPatientsFixtures: Fixture[] = [
  {
    id: "result",
    label: "EGFR / NSCLC result",
    notes: "Typical result shape with PGx, population coverage, resistance, and recommended actions.",
    render: ({ log }) => (
      <StratifyPatientsViewer
        {...makeMockProps({ toolInputs: MOCK_RESULT as any }, log)}
      />
    ),
  },
];
