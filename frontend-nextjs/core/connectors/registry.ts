// Connector registry — the single source of truth for the Connections
// marketplace. Each entry describes one connector: how it presents (monogram,
// name, role, the guide copy) and how it configures (which local API route and
// which fields). The page renders tiles + a detail form from this list; adding
// a connector is a data edit here, not new UI.

export type Category = 'ai' | 'service' | 'data' | 'compliance' | 'observability';

export interface Field {
  name: string;
  label: string;
  placeholder?: string;
  required?: boolean;
  secret?: boolean; // password input; write-only (never prefilled from GET)
  hint?: string;
  bucket?: 'config' | 'credentials'; // data connectors only
}

export interface Connector {
  id: string;
  category: Category;
  monogram: string;
  name: string;
  role: string;
  what: string; // what it is
  use: string; // when you'd use it
  uses: string; // how NovoMCP uses it (which tools it unlocks)
  guide: string; // path under https://docs.novomcp.com/
  urlEnv?: string; // service category: the engine env var it writes
  fields: Field[];
}

export const CATEGORIES: { id: Category | 'all'; name: string }[] = [
  { id: 'all', name: 'All' },
  { id: 'ai', name: 'AI providers' },
  { id: 'service', name: 'NovoMCP services' },
  { id: 'data', name: 'Data connectors' },
  { id: 'compliance', name: 'Compliance' },
  { id: 'observability', name: 'Observability' },
];

export const CATEGORY_LABEL: Record<Category, string> = {
  ai: 'AI provider',
  service: 'NovoMCP service',
  data: 'Data connector',
  compliance: 'Compliance',
  observability: 'Observability',
};

const KEY = (label = 'API key', ph = 'optional', required = false): Field => ({
  name: 'apiKey',
  label,
  placeholder: ph,
  secret: true,
  required,
  hint: 'Stored write-only in your local credential file (chmod 600); never shown again.',
});

export const CONNECTORS: Connector[] = [
  // ── AI providers ────────────────────────────────────────────────
  {
    id: 'openai', category: 'ai', monogram: 'AI', name: 'OpenAI',
    role: 'Intent, planning & semantic tool search',
    what: 'The reasoning model behind intent recognition, orchestration planning, and semantic tool search.',
    use: 'Turn a plain-language request into the right sequence of tool calls.',
    uses: 'Enables planning and semantic tool search across the catalog.',
    guide: 'configuring-llm/',
    fields: [
      KEY('API key', 'sk-…', true),
      { name: 'model', label: 'Model', placeholder: 'gpt-4o (default)' },
      { name: 'baseUrl', label: 'Base URL', placeholder: 'optional — for OpenAI-compatible hosts' },
    ],
  },
  {
    id: 'anthropic', category: 'ai', monogram: 'AN', name: 'Anthropic',
    role: 'Claude models for planning',
    what: "Anthropic's Claude models as the engine's planning and reasoning provider.",
    use: 'Same planning role as OpenAI, on Claude.',
    uses: 'Selectable as the active LLM provider.',
    guide: 'configuring-llm/',
    fields: [KEY('API key', 'sk-ant-…', true), { name: 'model', label: 'Model', placeholder: 'claude-… (default)' }],
  },
  {
    id: 'ollama', category: 'ai', monogram: 'OL', name: 'Ollama',
    role: 'Local, offline models',
    what: 'Run open models locally with no API key — fully offline planning.',
    use: 'Keep everything on your machine; no data leaves.',
    uses: 'Points the engine at a running Ollama host.',
    guide: 'configuring-llm/',
    fields: [
      { name: 'url', label: 'Host URL', placeholder: 'http://localhost:11434', required: true },
      { name: 'model', label: 'Model', placeholder: 'llama3.1 (default)' },
    ],
  },
  {
    id: 'azure', category: 'ai', monogram: 'AZ', name: 'Azure OpenAI',
    role: 'OpenAI models on Azure',
    what: 'OpenAI models served through your Azure deployment.',
    use: 'When your org standardizes on Azure for model access.',
    uses: 'Selectable as the active LLM provider.',
    guide: 'configuring-llm/',
    fields: [
      KEY('API key', 'azure key', true),
      { name: 'endpoint', label: 'Endpoint', placeholder: 'https://your-resource.openai.azure.com', required: true },
      { name: 'deployment', label: 'Deployment', placeholder: 'your-deployment-name' },
    ],
  },

  // ── NovoMCP compute services ────────────────────────────────────
  {
    id: 'admet', category: 'service', monogram: 'AD', name: 'ADDIE models', urlEnv: 'ADDIE_MODELS_URL',
    role: '31 ADMET predictions',
    what: "NovoMCP's ADMET service — 31 ML models for absorption, distribution, metabolism, excretion & toxicity.",
    use: 'Get predicted ADMET properties on any molecule, in the profile and the funnel.',
    uses: 'Unlocks predict_admet and fills the ADMET half of get_molecule_profile.',
    guide: 'deploying-services/addie-models/',
    fields: [
      { name: 'url', label: 'Service URL', placeholder: 'http://addie-models:8025', required: true, hint: 'Where the service is running (Docker, K8s, or a remote host).' },
      KEY('API key', 'optional — only if auth-gated'),
    ],
  },
  {
    id: 'docking', category: 'service', monogram: 'AG', name: 'AutoDock-GPU', urlEnv: 'AUTODOCK_GPU_URL',
    role: 'GPU molecular docking',
    what: 'GPU-accelerated docking (AutoDock-GPU): scores how a ligand binds a protein target.',
    use: 'Rank candidates by predicted pose and affinity against a PDB structure.',
    uses: 'Unlocks dock_molecules and dock_with_strain.',
    guide: 'deploying-services/autodock-gpu/',
    fields: [{ name: 'url', label: 'Service URL', placeholder: 'http://autodock-gpu:8022', required: true, hint: 'NVIDIA GPU service endpoint.' }, KEY()],
  },
  {
    id: 'md', category: 'service', monogram: 'MD', name: 'GROMACS-MD', urlEnv: 'GROMACS_MD_URL',
    role: 'Molecular dynamics',
    what: 'GPU molecular dynamics (GROMACS): equilibrium simulation of a protein–ligand system.',
    use: 'Validate a binding pose with a short MD run before committing compute.',
    uses: 'Unlocks run_molecular_dynamics and generate_dynamics.',
    guide: 'deploying-services/gromacs-md/',
    fields: [{ name: 'url', label: 'Service URL', placeholder: 'http://gromacs-md:8021', required: true, hint: 'GPU service endpoint.' }, KEY()],
  },
  {
    id: 'structure', category: 'service', monogram: 'OF', name: 'OpenFold3', urlEnv: 'OPENFOLD3_URL',
    role: 'Protein structure prediction',
    what: 'Predicts a protein structure from sequence (OpenFold3).',
    use: 'Get a structure to dock or simulate against when you only have a sequence.',
    uses: 'Unlocks predict_structure, get_protein_structure, get_structure_result.',
    guide: 'deploying-services/openfold3/',
    fields: [{ name: 'url', label: 'Service URL', placeholder: 'http://openfold3:8025', required: true, hint: 'GPU service endpoint.' }, KEY()],
  },
  {
    id: 'qm', category: 'service', monogram: 'QM', name: 'NovoMCP-QM', urlEnv: 'NOVOMCP_QM_URL',
    role: 'Quantum chemistry (xTB / CREST)',
    what: 'Native quantum-mechanical service: xTB, CREST, and MCPB.py for metal sites.',
    use: 'Energies, conformer search, and metal-site parameterization.',
    uses: 'Unlocks 8 QM tools including run_qm_calculation and run_conformer_search.',
    guide: 'deploying-services/novomcp-qm/',
    fields: [{ name: 'url', label: 'Service URL', placeholder: 'http://novomcp-qm:8031', required: true, hint: 'CPU service endpoint.' }, KEY()],
  },
  {
    id: 'nnp', category: 'service', monogram: 'NN', name: 'NovoMCP-NNP', urlEnv: 'NOVOMCP_NNP_URL',
    role: 'Neural-net potentials',
    what: 'Neural-network potentials (AIMNet2 / MACE / ANI-2x) for fast energies and geometry.',
    use: 'ML-speed energies and optimization without a full QM calc.',
    uses: 'Unlocks compute_energy and optimize_geometry_nnp.',
    guide: 'deploying-services/novomcp-nnp/',
    fields: [{ name: 'url', label: 'Service URL', placeholder: 'http://novomcp-nnp:8032', required: true, hint: 'Service endpoint.' }, KEY()],
  },
  {
    id: 'properties', category: 'service', monogram: 'PR', name: 'NovoMCP-Properties', urlEnv: 'NOVOMCP_PROPERTIES_URL',
    role: 'pKa / solubility / BDE',
    what: 'Predicts pKa, solubility, and bond dissociation energies.',
    use: 'Physicochemical properties beyond the RDKit basics.',
    uses: 'Unlocks predict_pka, predict_solubility, predict_bde.',
    guide: 'deploying-services/novomcp-properties/',
    fields: [{ name: 'url', label: 'Service URL', placeholder: 'http://novomcp-properties:8030', required: true, hint: 'Service endpoint.' }, KEY()],
  },
  {
    id: 'molecule_index', category: 'service', monogram: 'IX', name: 'Molecule index', urlEnv: 'NOVOMCP_MOLECULE_INDEX_URL',
    role: 'Similarity + tree search',
    what: 'A molecule-index service over a parquet slice of the corpus — similarity and tree-guided retrieval.',
    use: 'Find analogs and explore chemical space around a hit.',
    uses: 'Unlocks search_similar, filter_molecules, and the tree-search tools.',
    guide: 'deploying-services/',
    fields: [{ name: 'url', label: 'Service URL', placeholder: 'http://molecule-index:8040', required: true, hint: 'Any self-hosted parquet index works.' }, KEY()],
  },

  // ── Data connectors ─────────────────────────────────────────────
  {
    id: 'snowflake', category: 'data', monogram: 'SF', name: 'Snowflake',
    role: 'Warehouse export',
    what: 'Push NovoMCP results into a Snowflake warehouse.',
    use: 'Land ADMET / docking results next to the rest of your data.',
    uses: 'Registers a Snowflake destination for push_to_destination.',
    guide: 'optional-data-services/',
    fields: [
      { name: 'account', label: 'Account', placeholder: 'xy12345.us-east-1', required: true, bucket: 'config' },
      { name: 'warehouse', label: 'Warehouse', placeholder: 'COMPUTE_WH', bucket: 'config' },
      { name: 'database', label: 'Database', placeholder: 'optional', bucket: 'config' },
      { name: 'username', label: 'Username', placeholder: 'svc_novomcp', required: true, bucket: 'credentials' },
      { name: 'password', label: 'Password', placeholder: '••••', required: true, secret: true, bucket: 'credentials' },
    ],
  },
  {
    id: 'databricks', category: 'data', monogram: 'DB', name: 'Databricks',
    role: 'Lakehouse SQL',
    what: 'Read from and write to a Databricks SQL warehouse.',
    use: 'Bidirectional pipeline with your lakehouse.',
    uses: 'Registers a Databricks destination for push_to_destination.',
    guide: 'optional-data-services/',
    fields: [
      { name: 'server_hostname', label: 'Server hostname', placeholder: 'dbc-xxxx.cloud.databricks.com', required: true, bucket: 'config' },
      { name: 'http_path', label: 'HTTP path', placeholder: '/sql/1.0/warehouses/xxxx', required: true, bucket: 'config' },
      { name: 'catalog', label: 'Catalog', placeholder: 'optional', bucket: 'config' },
      { name: 'access_token', label: 'Access token', placeholder: 'dapi…', required: true, secret: true, bucket: 'credentials' },
    ],
  },

  // ── Compliance ──────────────────────────────────────────────────
  {
    id: 'compliance', category: 'compliance', monogram: 'CO', name: 'Compliance service',
    role: 'Controlled-substance & regulatory screening',
    what: 'A compliance service the engine forwards molecules to — ours, or any compatible endpoint.',
    use: 'Screen a molecule against controlled-substance and regulatory rules before you act on it.',
    uses: 'Unlocks check_compliance; the engine bundles no ruleset of its own.',
    guide: 'tool-availability/',
    fields: [
      { name: 'url', label: 'Service URL', placeholder: 'https://compliance.example.com', required: true },
      KEY('API key', 'optional'),
    ],
  },

  // ── Observability ───────────────────────────────────────────────
  {
    id: 'otlp', category: 'observability', monogram: 'OT', name: 'OpenTelemetry (OTLP)',
    role: 'Traces to Grafana / Honeycomb / Arize / Datadog',
    what: 'Export engine traces over OTLP to any compatible observability backend.',
    use: 'Watch prompts, tool calls, latency and tokens in the tool you already use.',
    uses: 'Turns on request / tool-call tracing when an endpoint is set.',
    guide: 'configuring-llm/',
    fields: [
      { name: 'endpoint', label: 'OTLP endpoint', placeholder: 'https://otlp.example.com:4317', required: true, hint: 'An http(s):// URL or host:port.' },
      { name: 'headers', label: 'Auth header', placeholder: 'api-key=…', secret: true, hint: 'Sent as an OTLP header; write-only.' },
      { name: 'samplingRate', label: 'Sampling rate', placeholder: '1.0', hint: '0–1. Fraction of traces exported.' },
    ],
  },
];
