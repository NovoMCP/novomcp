'use client';

import { useState } from 'react';
import { useAuth } from '@/core/auth/provider';
import { usePolicies, useCreatePolicy, useDeletePolicy } from '@/core/api/admin-client';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';

const ROLES = ['admin', 'researcher', 'manager', 'clinician', 'member'];

export default function PoliciesPage() {
  const { user } = useAuth();
  const router = useRouter();
  const isAdmin = user?.roles?.includes('admin');
  const { data, isLoading } = usePolicies();
  const createPolicy = useCreatePolicy();
  const deletePolicy = useDeletePolicy();

  const [showCreate, setShowCreate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  // Field names below MUST match the novomcp-auth PolicyCreate model
  // (app/models/policy.py). The Pydantic model is permissive (no extra=forbid)
  // so mis-named keys are silently dropped and the policy lands in the DB with
  // empty array / NULL constraints. Use the API field names verbatim.
  const [form, setForm] = useState({
    name: '',
    selectedRoles: ['member'] as string[],
    allowed_connector_types: '',
    allowed_operations: '',
    allowed_scopes: '',
    max_rows_per_export: '10000',
    require_consent: false,
    priority: '100',
  });

  useEffect(() => { if (user && !isAdmin) router.push('/dashboard'); }, [user, isAdmin, router]);
  if (!isAdmin) return null;

  const policies = data?.policies || (Array.isArray(data) ? data : []);

  const toggleRole = (role: string) => {
    setForm((prev) => ({
      ...prev,
      selectedRoles: prev.selectedRoles.includes(role)
        ? prev.selectedRoles.filter((r) => r !== role)
        : [...prev.selectedRoles, role],
    }));
  };

  const splitCsv = (s: string): string[] =>
    s ? s.split(',').map((x) => x.trim()).filter(Boolean) : [];

  const handleCreate = async () => {
    await createPolicy.mutateAsync({
      name: form.name,
      applies_to_roles: form.selectedRoles,
      allowed_connector_types: splitCsv(form.allowed_connector_types),
      allowed_operations: splitCsv(form.allowed_operations),
      allowed_scopes: splitCsv(form.allowed_scopes),
      // Blank field = no row cap (NULL upstream). Don't synthesize a default
      // that constrains policies the admin didn't ask to constrain.
      max_rows_per_export: form.max_rows_per_export ? parseInt(form.max_rows_per_export) : null,
      require_consent: form.require_consent,
      priority: parseInt(form.priority) || 100,
      org_id: user?.orgId,
    });
    setShowCreate(false);
    setForm({
      name: '',
      selectedRoles: ['member'],
      allowed_connector_types: '',
      allowed_operations: '',
      allowed_scopes: '',
      max_rows_per_export: '10000',
      require_consent: false,
      priority: '100',
    });
  };

  const handleDelete = async (id: string) => { await deletePolicy.mutateAsync(id); setConfirmDelete(null); };

  const inputClass = "w-full px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--accent)]";

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>Policies</h1>
          <p className="text-sm text-[var(--text-muted)]">Data access and governance policies</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all duration-[400ms]">
          <Plus className="h-4 w-4" />Create Policy
        </button>
      </div>

      <div className="bg-[var(--card)] border border-[var(--border)]">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left">
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Name</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Roles</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Connectors</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Scopes</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Max Rows</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Consent</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Priority</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && <tr><td colSpan={8} className="px-6 py-8 text-center text-[var(--text-muted)]">Loading...</td></tr>}
              {policies.map((p: any) => {
                const connectors = Array.isArray(p.allowed_connector_types) && p.allowed_connector_types.length > 0
                  ? p.allowed_connector_types.join(', ')
                  : 'all';
                const scopes = Array.isArray(p.allowed_scopes) && p.allowed_scopes.length > 0
                  ? p.allowed_scopes.join(', ')
                  : 'all';
                const maxRows = p.max_rows_per_export;
                return (
                  <tr key={p.id || p.policy_id} className="border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--bg)]">
                    <td className="px-6 py-3 text-[var(--text)] font-medium">{p.name}</td>
                    <td className="px-6 py-3 text-[var(--text-soft)]">{Array.isArray(p.applies_to_roles) ? p.applies_to_roles.join(', ') : p.applies_to_roles}</td>
                    <td className="px-6 py-3 text-[var(--text-soft)] font-mono text-xs">{connectors}</td>
                    <td className="px-6 py-3 text-[var(--text-soft)] font-mono text-xs">{scopes}</td>
                    <td className="px-6 py-3 text-[var(--text-soft)]">{maxRows != null ? maxRows.toLocaleString() : '—'}</td>
                    <td className="px-6 py-3">
                      <span className={`inline-flex px-2 py-0.5 text-xs font-medium ${p.require_consent ? 'bg-[var(--warning)]/10 text-[var(--warning)]' : 'bg-[var(--bg-warm)] text-[var(--text-muted)]'}`}>
                        {p.require_consent ? 'Required' : 'No'}
                      </span>
                    </td>
                    <td className="px-6 py-3 text-[var(--text-soft)]">{p.priority}</td>
                    <td className="px-6 py-3"><button onClick={() => setConfirmDelete(p.id || p.policy_id)} className="text-[var(--destructive)] hover:opacity-80"><Trash2 className="h-3.5 w-3.5" /></button></td>
                  </tr>
                );
              })}
              {!isLoading && policies.length === 0 && <tr><td colSpan={8} className="px-6 py-8 text-center text-[var(--text-muted)]">No policies defined</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="bg-[var(--card)] border-[var(--border)]">
          <DialogHeader>
            <DialogTitle className="text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>Create Policy</DialogTitle>
            <DialogDescription className="text-[var(--text-muted)]">Define data access rules</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Name</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Applies to Roles</label>
              <div className="flex flex-wrap gap-2">
                {ROLES.map((role) => (
                  <button
                    key={role}
                    type="button"
                    onClick={() => toggleRole(role)}
                    className={`px-3 py-1.5 text-xs font-medium border transition-colors capitalize ${
                      form.selectedRoles.includes(role)
                        ? 'border-[var(--accent)] bg-[var(--accent)]/10 text-[var(--accent)]'
                        : 'border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg-warm)]'
                    }`}
                  >
                    {role}
                  </button>
                ))}
              </div>
              {form.selectedRoles.length === 0 && (
                <p className="text-xs text-[var(--destructive)] mt-1">Select at least one role</p>
              )}
            </div>
            <div>
              <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Connector Types (comma-separated, blank = all)</label>
              <input
                value={form.allowed_connector_types}
                onChange={(e) => setForm({ ...form, allowed_connector_types: e.target.value })}
                placeholder="snowflake, databricks, bigquery"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Operations (comma-separated, blank = all)</label>
              <input
                value={form.allowed_operations}
                onChange={(e) => setForm({ ...form, allowed_operations: e.target.value })}
                placeholder="read, write, export"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Allowed Scopes (comma-separated, blank = no scope constraint)</label>
              <input
                value={form.allowed_scopes}
                onChange={(e) => setForm({ ...form, allowed_scopes: e.target.value })}
                placeholder="tools:read, connector:bigquery:read"
                className={inputClass}
              />
              <p className="text-xs text-[var(--text-muted)] mt-1">
                Scopes are the load-bearing constraint at JWT mint time. Leave blank to allow all scopes the matched role would otherwise receive.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Max Rows (blank = no cap)</label>
                <input
                  type="number"
                  value={form.max_rows_per_export}
                  onChange={(e) => setForm({ ...form, max_rows_per_export: e.target.value })}
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-[var(--text)] mb-1.5">Priority (lower = evaluated first)</label>
                <input type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className={inputClass} />
              </div>
            </div>
            <div className="flex items-center gap-2"><input type="checkbox" checked={form.require_consent} onChange={(e) => setForm({ ...form, require_consent: e.target.checked })} className="accent-[var(--accent)]" /><label className="text-sm text-[var(--text)]">Require user consent</label></div>
          </div>
          <DialogFooter>
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm font-medium text-[var(--text-soft)] border border-[var(--border)] hover:bg-[var(--bg-warm)] transition-colors">Cancel</button>
            <button onClick={handleCreate} disabled={createPolicy.isPending || !form.name || form.selectedRoles.length === 0} className="px-4 py-2 text-sm font-medium text-white bg-[var(--accent)] hover:bg-[var(--accent)]/90 transition-all disabled:opacity-50">{createPolicy.isPending ? 'Creating...' : 'Create'}</button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!confirmDelete} onOpenChange={() => setConfirmDelete(null)}>
        <DialogContent className="bg-[var(--card)] border-[var(--border)]">
          <DialogHeader><DialogTitle className="text-[var(--text)]">Delete Policy</DialogTitle><DialogDescription className="text-[var(--text-muted)]">This action cannot be undone.</DialogDescription></DialogHeader>
          <DialogFooter>
            <button onClick={() => setConfirmDelete(null)} className="px-4 py-2 text-sm font-medium text-[var(--text-soft)] border border-[var(--border)] hover:bg-[var(--bg-warm)] transition-colors">Cancel</button>
            <button onClick={() => confirmDelete && handleDelete(confirmDelete)} disabled={deletePolicy.isPending} className="px-4 py-2 text-sm font-medium text-white bg-[var(--destructive)] hover:bg-[var(--destructive)]/90 transition-all disabled:opacity-50">{deletePolicy.isPending ? 'Deleting...' : 'Delete'}</button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
