'use client';

import { useState } from 'react';
import { useAuth } from '@/core/auth/provider';
import { useConnections, useCreateConnection, useTestConnection, useDeleteConnection, useInitiateOAuth } from '@/core/api/admin-client';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect } from 'react';
import { Plug, Plus, CheckCircle, XCircle, Loader2, Trash2, Shield } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';

const CONNECTOR_TYPES = [
  'bigquery', 'snowflake', 'databricks', 'supabase', 'benchling',
];

const OAUTH_CONNECTORS = new Set(['bigquery']);

const CONNECTOR_LABELS: Record<string, string> = {
  bigquery: 'BigQuery',
  snowflake: 'Snowflake',
  databricks: 'Databricks',
  supabase: 'Supabase',
  benchling: 'Benchling',
};

// Per-connector auth hint shown on the picker tile. Makes the OAuth vs
// manual-credentials split visible at a glance instead of only flagging the
// OAuth-capable ones (which previously left every other tile with no
// indication of what credential they'd be asked for).
const CONNECTOR_AUTH_HINTS: Record<string, string> = {
  bigquery: 'OAuth or service account',
  snowflake: 'Username + password',
  databricks: 'Personal access token',
  supabase: 'Service-role key + DB password',
  benchling: 'API key',
};

const CONFIG_EXAMPLES: Record<string, string> = {
  bigquery: '{"project_id": "my-project", "dataset_id": "my_dataset"}',
  snowflake: '{"account": "abc123", "warehouse": "COMPUTE_WH", "database": "MY_DB", "schema": "PUBLIC"}',
  databricks: '{"server_hostname": "adb-123.azuredatabricks.net", "http_path": "/sql/1.0/warehouses/abc"}',
  supabase: '{"supabase_url": "https://xxx.supabase.co"}',
  benchling: '{"tenant_url": "https://myorg.benchling.com"}',
};

const CRED_EXAMPLES: Record<string, string> = {
  bigquery: '{"service_account_key": "{...}"}',
  snowflake: '{"username": "...", "password": "..."}',
  databricks: '{"access_token": "dapi..."}',
  supabase: '{"supabase_key": "eyJ...", "db_password": "your-db-password"}',
  benchling: '{"api_key": "sk_..."}',
};

export default function ConnectionsPage() {
  const { user } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAdmin = user?.roles?.includes('admin');
  const { data, isLoading, refetch } = useConnections();
  const createConn = useCreateConnection();
  const testConn = useTestConnection();
  const deleteConn = useDeleteConnection();
  const initiateOAuth = useInitiateOAuth();

  const [showCreate, setShowCreate] = useState(false);
  const [step, setStep] = useState(1);
  const [authMode, setAuthMode] = useState<'oauth' | 'manual'>('oauth');
  const [form, setForm] = useState({
    connector_type: '',
    display_name: '',
    description: '',
    config: '{}',
    credentials: '{}',
  });
  const [bqProjectId, setBqProjectId] = useState('');
  const [bqDatasetId, setBqDatasetId] = useState('');
  // Snowflake fields
  const [sfAccount, setSfAccount] = useState('');
  const [sfWarehouse, setSfWarehouse] = useState('');
  const [sfDatabase, setSfDatabase] = useState('');
  const [sfSchema, setSfSchema] = useState('PUBLIC');
  // Databricks fields
  const [dbHostname, setDbHostname] = useState('');
  const [dbHttpPath, setDbHttpPath] = useState('');
  const [dbCatalog, setDbCatalog] = useState('');
  const [dbSchema, setDbSchema] = useState('default');
  // Supabase fields
  const [sbUrl, setSbUrl] = useState('');
  const [sbPoolerHost, setSbPoolerHost] = useState('');
  const [sbKey, setSbKey] = useState('');
  const [sbPassword, setSbPassword] = useState('');
  // Benchling fields (required: tenant_url + api_key; optional: folder/schema/registry IDs)
  const [bnTenantUrl, setBnTenantUrl] = useState('');
  const [bnFolderId, setBnFolderId] = useState('');
  const [bnSchemaId, setBnSchemaId] = useState('');
  const [bnRegistryId, setBnRegistryId] = useState('');
  const [bnApiKey, setBnApiKey] = useState('');
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ id: string; success: boolean; error?: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);

  // Handle OAuth return params
  useEffect(() => {
    const status = searchParams.get('status');
    if (status === 'connected') {
      const connectorType = searchParams.get('connector_type') || '';
      setStatusMessage({ type: 'success', text: `${CONNECTOR_LABELS[connectorType] || connectorType} connected via OAuth` });
      refetch();
      // Clean URL params
      router.replace('/connections');
    } else if (status === 'error') {
      const message = searchParams.get('message') || 'OAuth connection failed';
      setStatusMessage({ type: 'error', text: message });
      router.replace('/connections');
    }
  }, [searchParams, refetch, router]);

  // Auto-dismiss status messages
  useEffect(() => {
    if (statusMessage) {
      const timer = setTimeout(() => setStatusMessage(null), 6000);
      return () => clearTimeout(timer);
    }
  }, [statusMessage]);

  useEffect(() => {
    if (user && !isAdmin) router.push('/dashboard');
  }, [user, isAdmin, router]);

  if (!isAdmin) return null;

  const connections = data?.connections || [];

  const buildConfig = (): { config: Record<string, any>; error?: string } => {
    const ct = form.connector_type;
    if (ct === 'bigquery') {
      if (!bqProjectId.trim()) return { config: {}, error: 'GCP Project ID is required for BigQuery' };
      const c: Record<string, any> = { project_id: bqProjectId.trim() };
      if (bqDatasetId.trim()) c.dataset_id = bqDatasetId.trim();
      return { config: c };
    }
    if (ct === 'snowflake') {
      if (!sfAccount.trim()) return { config: {}, error: 'Snowflake account identifier is required' };
      if (!sfDatabase.trim()) return { config: {}, error: 'Snowflake database name is required' };
      return { config: { account: sfAccount.trim(), warehouse: sfWarehouse.trim() || undefined, database: sfDatabase.trim(), schema: sfSchema.trim() || 'PUBLIC' } };
    }
    if (ct === 'databricks') {
      if (!dbHostname.trim()) return { config: {}, error: 'Databricks server hostname is required' };
      if (!dbHttpPath.trim()) return { config: {}, error: 'Databricks HTTP path is required' };
      return { config: { server_hostname: dbHostname.trim(), http_path: dbHttpPath.trim(), catalog: dbCatalog.trim() || undefined, schema: dbSchema.trim() || 'default' } };
    }
    if (ct === 'supabase') {
      if (!sbUrl.trim()) return { config: {}, error: 'Supabase project URL is required' };
      if (!sbPoolerHost.trim()) return { config: {}, error: 'Session pooler host is required (Settings → Database → Session pooler → View parameters)' };
      return { config: { supabase_url: sbUrl.trim(), db_host: sbPoolerHost.trim() } };
    }
    if (ct === 'benchling') {
      if (!bnTenantUrl.trim()) return { config: {}, error: 'Benchling tenant URL is required (e.g., https://myorg.benchling.com)' };
      const c: Record<string, any> = { tenant_url: bnTenantUrl.trim() };
      if (bnFolderId.trim()) c.folder_id = bnFolderId.trim();
      if (bnSchemaId.trim()) c.schema_id = bnSchemaId.trim();
      if (bnRegistryId.trim()) c.registry_id = bnRegistryId.trim();
      return { config: c };
    }
    try { return { config: JSON.parse(form.config) }; } catch { return { config: {} }; }
  };

  const handleOAuthConnect = async () => {
    const { config, error } = buildConfig();
    if (error) { setDialogError(error); return; }
    setDialogError(null);

    try {
      const result = await initiateOAuth.mutateAsync({
        org_id: user?.orgId || '',
        connector_type: form.connector_type,
        user_id: user?.id || '',
        display_name: form.display_name,
        description: form.description || undefined,
        config,
        redirect_uri: `${window.location.origin}/api/oauth/callback`,
      });

      if (!result.authorize_url) {
        setDialogError('No authorize URL returned from server');
        return;
      }

      // Redirect to OAuth provider
      window.location.href = result.authorize_url;
    } catch (e: any) {
      setDialogError(e.message || 'Failed to initiate OAuth');
    }
  };

  const handleCreate = async () => {
    const { config, error } = buildConfig();
    if (error) { setDialogError(error); return; }
    let credentials: Record<string, any> = {};
    if (form.connector_type === 'supabase') {
      if (!sbKey.trim() || !sbPassword.trim()) { setDialogError('Service role key and database password are required'); return; }
      credentials = { supabase_key: sbKey.trim(), db_password: sbPassword.trim() };
    } else if (form.connector_type === 'benchling') {
      if (!bnApiKey.trim()) { setDialogError('Benchling API key is required'); return; }
      credentials = { api_key: bnApiKey.trim() };
    } else {
      try { credentials = JSON.parse(form.credentials); } catch { /* */ }
    }

    await createConn.mutateAsync({
      org_id: user?.orgId || '',
      display_name: form.display_name,
      connector_type: form.connector_type,
      description: form.description || undefined,
      config,
      credentials,
    });
    setShowCreate(false);
    resetForm();
  };

  const handleTest = async (connectionId: string) => {
    setTestingId(connectionId);
    setTestResult(null);
    try {
      const result = await testConn.mutateAsync(connectionId);
      setTestResult({ id: connectionId, success: result.success, error: result.error });
      if (result.success) refetch();
    } catch (e: any) {
      setTestResult({ id: connectionId, success: false, error: e.message });
    }
    setTestingId(null);
  };

  const handleDelete = async (connectionId: string) => {
    await deleteConn.mutateAsync(connectionId);
    setConfirmDelete(null);
  };

  const resetForm = () => {
    setStep(1);
    setAuthMode('oauth');
    setDialogError(null);
    setBqProjectId(''); setBqDatasetId('');
    setSfAccount(''); setSfWarehouse(''); setSfDatabase(''); setSfSchema('PUBLIC');
    setDbHostname(''); setDbHttpPath(''); setDbCatalog(''); setDbSchema('default');
    setSbUrl(''); setSbPoolerHost(''); setSbKey(''); setSbPassword('');
    setBnTenantUrl(''); setBnFolderId(''); setBnSchemaId(''); setBnRegistryId(''); setBnApiKey('');
    setForm({ connector_type: '', display_name: '', description: '', config: '{}', credentials: '{}' });
  };

  const isOAuthConnector = OAUTH_CONNECTORS.has(form.connector_type);

  // Determine total steps based on auth mode
  // OAuth: 1 (select) -> 2 (name) -> 3 (config) -> OAuth redirect
  // Manual: 1 (select) -> 2 (name) -> 3 (config) -> 4 (credentials) -> create
  const totalSteps = authMode === 'manual' ? 4 : 3;

  return (
    <div className="space-y-6">
      {/* Status toast */}
      {statusMessage && (
        <div className={`p-3 text-sm ${
          statusMessage.type === 'success'
            ? 'bg-[var(--success)]/10 text-[var(--success)] border border-[var(--success)]/20'
            : 'bg-[var(--destructive)]/10 text-[var(--destructive)] border border-[var(--destructive)]/20'
        }`}>
          {statusMessage.text}
        </div>
      )}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>
            Connections
          </h1>
          <p className="text-sm text-[var(--text-muted)]">Manage data source connections</p>
        </div>
        <button
          onClick={() => { setShowCreate(true); resetForm(); }}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all duration-[400ms]"
        >
          <Plus className="h-4 w-4" />
          Register Connection
        </button>
      </div>

      {isLoading ? (
        <p className="text-[var(--text-muted)]">Loading...</p>
      ) : connections.length === 0 ? (
        <div className="bg-[var(--card)] border border-[var(--border)] p-12 text-center">
          <Plug className="h-8 w-8 text-[var(--text-muted)] mx-auto mb-3" />
          <p className="text-[var(--text-muted)]">No connections registered yet</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {connections.map((c: any) => (
            <div key={c.connection_id} className="bg-[var(--card)] border border-[var(--border)] p-5">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-semibold text-[var(--text)]">{c.display_name}</p>
                    {c.auth_method === 'oauth' && (
                      <span className="flex items-center gap-0.5 text-[10px] font-medium px-1.5 py-0.5 bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20">
                        <Shield className="h-2.5 w-2.5" />
                        OAuth
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-[var(--text-muted)] mt-0.5 font-mono">{c.connector_type}</p>
                </div>
                <span className={`flex items-center gap-1 text-xs font-medium px-2 py-0.5 ${
                  c.is_validated
                    ? 'bg-[var(--success)]/10 text-[var(--success)]'
                    : 'bg-[var(--destructive)]/10 text-[var(--destructive)]'
                }`}>
                  {c.is_validated ? <CheckCircle className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
                  {c.is_validated ? 'Validated' : 'Not validated'}
                </span>
              </div>
              {c.description && <p className="text-xs text-[var(--text-soft)] mb-3">{c.description}</p>}
              {testResult && testResult.id === c.connection_id && (
                <div className={`text-xs p-2 mb-3 ${
                  testResult.success ? 'bg-[var(--success)]/10 text-[var(--success)]' : 'bg-[var(--destructive)]/10 text-[var(--destructive)]'
                }`}>
                  {testResult.success ? 'Connection test passed' : `Test failed: ${testResult.error}`}
                </div>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => handleTest(c.connection_id)}
                  disabled={testingId === c.connection_id}
                  className="text-xs px-3 py-1.5 border border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg-warm)] transition-colors disabled:opacity-50"
                >
                  {testingId === c.connection_id ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Test'}
                </button>
                <button
                  onClick={() => setConfirmDelete(c.connection_id)}
                  className="text-xs px-3 py-1.5 text-[var(--destructive)] hover:bg-[var(--destructive)]/5 transition-colors"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="bg-[var(--card)] border-[var(--border)]">
          <DialogHeader>
            <DialogTitle className="text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>
              Register Connection
            </DialogTitle>
            <DialogDescription className="text-[var(--text-muted)]">
              {step === 1 && 'Select connector type'}
              {step === 2 && 'Name and describe your connection'}
              {step === 3 && authMode === 'manual' ? 'Configuration (non-secret)' : step === 3 && 'Configuration and connect'}
              {step === 4 && 'Credentials (secret)'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {step === 1 && (
              <div className="grid grid-cols-2 gap-2">
                {CONNECTOR_TYPES.map((type) => (
                  <button key={type} onClick={() => {
                    setForm({ ...form, connector_type: type });
                    setAuthMode(OAUTH_CONNECTORS.has(type) ? 'oauth' : 'manual');
                    setStep(2);
                  }}
                    className={`p-3 text-left text-sm border transition-colors ${form.connector_type === type ? 'border-[var(--accent)] bg-[var(--accent)]/5' : 'border-[var(--border)] hover:bg-[var(--bg-warm)]'}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs text-[var(--text)]">{type}</span>
                      {OAUTH_CONNECTORS.has(type) && (
                        <span className="text-[10px] font-medium px-1.5 py-0.5 bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20">
                          OAuth
                        </span>
                      )}
                    </div>
                    {CONNECTOR_AUTH_HINTS[type] && (
                      <span className="block text-[10px] text-[var(--text-muted)] mt-1">
                        {CONNECTOR_AUTH_HINTS[type]}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
            {step === 2 && (
              <>
                <div>
                  <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Display Name</label>
                  <input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                    className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--accent)]" placeholder="e.g., Production Snowflake" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Description</label>
                  <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
                    className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--accent)] min-h-[80px]" />
                </div>
              </>
            )}
            {step === 3 && (
              <div>
                {form.connector_type === 'bigquery' ? (
                  <>
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                        GCP Project ID <span className="text-[var(--destructive)]">*</span>
                      </label>
                      <input value={bqProjectId} onChange={(e) => setBqProjectId(e.target.value)}
                        placeholder="e.g., my-project-123456"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      <p className="text-xs text-[var(--text-muted)] mt-1">
                        Find at <a href="https://console.cloud.google.com" target="_blank" rel="noopener noreferrer" className="text-blue-500 underline">console.cloud.google.com</a> → project selector
                      </p>
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Default Dataset</label>
                      <input value={bqDatasetId} onChange={(e) => setBqDatasetId(e.target.value)}
                        placeholder="e.g., novomcp_data (optional)"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                    </div>
                  </>
                ) : form.connector_type === 'snowflake' ? (
                  <>
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                        Account Identifier <span className="text-[var(--destructive)]">*</span>
                      </label>
                      <input value={sfAccount} onChange={(e) => setSfAccount(e.target.value)}
                        placeholder="e.g., xy12345.us-east-1"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      <p className="text-xs text-[var(--text-muted)] mt-1">
                        Find at <a href="https://app.snowflake.com" target="_blank" rel="noopener noreferrer" className="text-blue-500 underline">app.snowflake.com</a> → account menu (bottom-left)
                      </p>
                    </div>
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                        Database <span className="text-[var(--destructive)]">*</span>
                      </label>
                      <input value={sfDatabase} onChange={(e) => setSfDatabase(e.target.value)}
                        placeholder="e.g., NOVOMCP_DB"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Warehouse</label>
                        <input value={sfWarehouse} onChange={(e) => setSfWarehouse(e.target.value)}
                          placeholder="e.g., COMPUTE_WH"
                          className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Schema</label>
                        <input value={sfSchema} onChange={(e) => setSfSchema(e.target.value)}
                          placeholder="PUBLIC"
                          className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      </div>
                    </div>
                  </>
                ) : form.connector_type === 'databricks' ? (
                  <>
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                        Server Hostname <span className="text-[var(--destructive)]">*</span>
                      </label>
                      <input value={dbHostname} onChange={(e) => setDbHostname(e.target.value)}
                        placeholder="e.g., adb-123456789.azuredatabricks.net"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      <p className="text-xs text-[var(--text-muted)] mt-1">
                        SQL Warehouse → Connection details → Server hostname
                      </p>
                    </div>
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                        HTTP Path <span className="text-[var(--destructive)]">*</span>
                      </label>
                      <input value={dbHttpPath} onChange={(e) => setDbHttpPath(e.target.value)}
                        placeholder="e.g., /sql/1.0/warehouses/abc123"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Catalog</label>
                        <input value={dbCatalog} onChange={(e) => setDbCatalog(e.target.value)}
                          placeholder="main (optional)"
                          className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Schema</label>
                        <input value={dbSchema} onChange={(e) => setDbSchema(e.target.value)}
                          placeholder="default"
                          className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      </div>
                    </div>
                  </>
                ) : form.connector_type === 'supabase' ? (
                  <>
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                        Project URL <span className="text-[var(--destructive)]">*</span>
                      </label>
                      <input value={sbUrl} onChange={(e) => setSbUrl(e.target.value)}
                        placeholder="e.g., https://abcdefghijk.supabase.co"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      <p className="text-xs text-[var(--text-muted)] mt-1">
                        Settings → API → Project URL
                      </p>
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                        Session Pooler Host <span className="text-[var(--destructive)]">*</span>
                      </label>
                      <input value={sbPoolerHost} onChange={(e) => setSbPoolerHost(e.target.value)}
                        placeholder="e.g., aws-0-us-east-1.pooler.supabase.com"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      <p className="text-xs text-[var(--text-muted)] mt-1">
                        Settings → Database → Connection string → Session pooler → View parameters → host
                      </p>
                    </div>
                  </>
                ) : form.connector_type === 'benchling' ? (
                  <>
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                        Tenant URL <span className="text-[var(--destructive)]">*</span>
                      </label>
                      <input value={bnTenantUrl} onChange={(e) => setBnTenantUrl(e.target.value)}
                        placeholder="e.g., https://myorg.benchling.com"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      <p className="text-xs text-[var(--text-muted)] mt-1">
                        Your org-specific Benchling URL (the one you log in to)
                      </p>
                    </div>
                    <div className="mb-3">
                      <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Default Folder ID</label>
                      <input value={bnFolderId} onChange={(e) => setBnFolderId(e.target.value)}
                        placeholder="lib_xxx (optional)"
                        className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      <p className="text-xs text-[var(--text-muted)] mt-1">
                        Default destination folder for new entities
                      </p>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Default Schema ID</label>
                        <input value={bnSchemaId} onChange={(e) => setBnSchemaId(e.target.value)}
                          placeholder="ts_xxx (optional)"
                          className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Registry ID</label>
                        <input value={bnRegistryId} onChange={(e) => setBnRegistryId(e.target.value)}
                          placeholder="src_xxx (optional)"
                          className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                      </div>
                    </div>
                  </>
                ) : (
                  /* Other connectors: generic JSON config */
                  <>
                    <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Config JSON</label>
                    <p className="text-xs text-[var(--text-muted)] mb-2">
                      Non-secret configuration (account IDs, warehouse names, database names).
                      {isOAuthConnector && ' Optional for OAuth — you can specify the target at export time.'}
                    </p>
                    <textarea value={form.config} onChange={(e) => setForm({ ...form, config: e.target.value })}
                      className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)] min-h-[120px]" />
                    {form.connector_type && CONFIG_EXAMPLES[form.connector_type] && (
                      <p className="text-xs text-[var(--text-muted)] mt-1.5">
                        Example: <code className="bg-[var(--bg-warm)] px-1 py-0.5 text-[10px]">{CONFIG_EXAMPLES[form.connector_type]}</code>
                      </p>
                    )}
                  </>
                )}
                {/* Auth mode toggle for OAuth-capable connectors */}
                {isOAuthConnector && (
                  <div className="mt-4 pt-4 border-t border-[var(--border)]">
                    <p className="text-xs font-medium text-[var(--text)] mb-2">Authentication method</p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setAuthMode('oauth')}
                        className={`flex-1 p-2 text-xs border transition-colors ${
                          authMode === 'oauth'
                            ? 'border-[var(--accent)] bg-[var(--accent)]/10 text-[var(--accent)]'
                            : 'border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg-warm)]'
                        }`}
                      >
                        <span className="font-medium">OAuth 2.0</span>
                        <span className="block text-[10px] mt-0.5 opacity-70">Sign in with {CONNECTOR_LABELS[form.connector_type] || form.connector_type}</span>
                      </button>
                      <button
                        onClick={() => setAuthMode('manual')}
                        className={`flex-1 p-2 text-xs border transition-colors ${
                          authMode === 'manual'
                            ? 'border-[var(--accent)] bg-[var(--accent)]/5 text-[var(--text)]'
                            : 'border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg-warm)]'
                        }`}
                      >
                        <span className="font-medium">Manual credentials</span>
                        <span className="block text-[10px] mt-0.5 opacity-70">Service account / API key</span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
            {step === 4 && authMode === 'manual' && form.connector_type === 'supabase' ? (
              <div>
                <div className="mb-3">
                  <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                    Service Role Key <span className="text-[var(--destructive)]">*</span>
                  </label>
                  <input type="password" value={sbKey} onChange={(e) => setSbKey(e.target.value)}
                    placeholder="eyJhbGciOiJIUzI1NiIs..."
                    className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                  <p className="text-xs text-[var(--text-muted)] mt-1">Settings → API → service_role key (not the anon key)</p>
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                    Database Password <span className="text-[var(--destructive)]">*</span>
                  </label>
                  <input type="password" value={sbPassword} onChange={(e) => setSbPassword(e.target.value)}
                    placeholder="Your database password"
                    className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                  <p className="text-xs text-[var(--text-muted)] mt-1">Settings → Database → Database Password (enables auto-table creation)</p>
                </div>
              </div>
            ) : step === 4 && authMode === 'manual' && form.connector_type === 'benchling' ? (
              <div>
                <label className="block text-sm font-medium text-[var(--text)] mb-1.5">
                  Benchling API Key <span className="text-[var(--destructive)]">*</span>
                </label>
                <input type="password" value={bnApiKey} onChange={(e) => setBnApiKey(e.target.value)}
                  placeholder="sk_..."
                  className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)]" />
                <p className="text-xs text-[var(--text-muted)] mt-1">
                  Settings → User Settings → API Keys → create new key with the scopes you want this connection to have. Stored in AWS Secrets Manager.
                </p>
              </div>
            ) : step === 4 && authMode === 'manual' ? (
              <div>
                <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Credentials JSON</label>
                <p className="text-xs text-[var(--text-muted)] mb-2">Secret credentials stored securely in AWS Secrets Manager (passwords, API keys, service account JSON).</p>
                <textarea value={form.credentials} onChange={(e) => setForm({ ...form, credentials: e.target.value })}
                  className="w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm font-mono focus:outline-none focus:border-[var(--accent)] min-h-[120px]" />
                {form.connector_type && CRED_EXAMPLES[form.connector_type] && (
                  <p className="text-xs text-[var(--text-muted)] mt-1.5">
                    Example for {form.connector_type}: <code className="bg-[var(--bg-warm)] px-1 py-0.5 text-[10px]">{CRED_EXAMPLES[form.connector_type]}</code>
                  </p>
                )}
              </div>
            ) : null}
          </div>
          {dialogError && (
            <div className="p-3 text-sm bg-[var(--destructive)]/10 text-[var(--destructive)] border border-[var(--destructive)]/20">
              {dialogError}
            </div>
          )}
          <DialogFooter>
            {step > 1 && (
              <button onClick={() => setStep(step - 1)} className="px-4 py-2 text-sm font-medium text-[var(--text-soft)] border border-[var(--border)] hover:bg-[var(--bg-warm)] transition-colors">
                Back
              </button>
            )}
            {step === 2 && (
              <button onClick={() => setStep(3)} disabled={!form.display_name} className="px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all disabled:opacity-50">
                Next
              </button>
            )}
            {step === 3 && authMode === 'oauth' && (
              <button
                onClick={handleOAuthConnect}
                disabled={initiateOAuth.isPending || (form.connector_type === 'bigquery' && !bqProjectId.trim())}
                className="px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all disabled:opacity-50"
              >
                {initiateOAuth.isPending ? 'Redirecting...' : `Connect with ${CONNECTOR_LABELS[form.connector_type] || form.connector_type}`}
              </button>
            )}
            {step === 3 && authMode === 'manual' && (
              <button onClick={() => { const { error } = buildConfig(); if (error) { setDialogError(error); } else { setDialogError(null); setStep(4); } }}
                className="px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all">
                Next
              </button>
            )}
            {step === 4 && authMode === 'manual' && (
              <button onClick={handleCreate} disabled={createConn.isPending} className="px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all disabled:opacity-50">
                {createConn.isPending ? 'Creating...' : 'Create Connection'}
              </button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!confirmDelete} onOpenChange={() => setConfirmDelete(null)}>
        <DialogContent className="bg-[var(--card)] border-[var(--border)]">
          <DialogHeader>
            <DialogTitle className="text-[var(--text)]">Delete Connection</DialogTitle>
            <DialogDescription className="text-[var(--text-muted)]">This will permanently remove this connection.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button onClick={() => setConfirmDelete(null)} className="px-4 py-2 text-sm font-medium text-[var(--text-soft)] border border-[var(--border)] hover:bg-[var(--bg-warm)] transition-colors">Cancel</button>
            <button onClick={() => confirmDelete && handleDelete(confirmDelete)} disabled={deleteConn.isPending} className="px-4 py-2 text-sm font-medium text-white bg-[var(--destructive)] hover:bg-[var(--destructive)]/90 transition-all disabled:opacity-50">{deleteConn.isPending ? 'Deleting...' : 'Delete'}</button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
