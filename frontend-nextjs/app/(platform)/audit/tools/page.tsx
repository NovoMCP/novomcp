'use client';

import { useState } from 'react';
import { useAuth } from '@/core/auth/provider';
import { useAuditLog } from '@/core/api/admin-client';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { Download, ChevronLeft, ChevronRight } from 'lucide-react';
import { format } from 'date-fns';

export default function AuditPage() {
  const { user } = useAuth();
  const router = useRouter();
  const isAdmin = user?.roles?.includes('admin');

  const [page, setPage] = useState(1);
  const [filterUser, setFilterUser] = useState('');
  const [filterTool, setFilterTool] = useState('');
  const perPage = 20;

  const { data, isLoading } = useAuditLog({
    page,
    per_page: perPage,
    user_id: filterUser || undefined,
    tool_name: filterTool || undefined,
  });

  useEffect(() => { if (user && !isAdmin) router.push('/dashboard'); }, [user, isAdmin, router]);
  if (!isAdmin) return null;

  const entries = data?.entries || [];
  const totalPages = data?.total_pages || 1;

  const handleExportCSV = () => {
    if (!entries.length) return;
    const headers = ['Timestamp', 'User', 'Tool', 'Credit Cost', 'Status', 'Execution Time (ms)'];
    const rows = entries.map((e: any) => [
      e.created_at || '',
      e.email || e.user_id || '',
      e.tool_name || '',
      e.credit_cost?.toFixed(2) || '0',
      e.success ? 'Success' : 'Failed',
      e.execution_time_ms || '',
    ]);
    const csv = [headers, ...rows].map((r) => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-log-${format(new Date(), 'yyyy-MM-dd')}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const inputClass = "px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--accent)]";

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm text-[var(--accent)] font-medium">Tools</span>
            <span className="text-sm text-[var(--text-muted)]">/</span>
            <a href="/audit/pipelines" className="text-sm text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors">Pipelines</a>
          </div>
          <h1 className="text-2xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>Audit Log</h1>
          <p className="text-sm text-[var(--text-muted)]">Tool usage and activity history</p>
        </div>
        <button onClick={handleExportCSV} disabled={!entries.length}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium border border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg-warm)] transition-colors disabled:opacity-50">
          <Download className="h-4 w-4" />Export CSV
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <input value={filterUser} onChange={(e) => { setFilterUser(e.target.value); setPage(1); }}
          placeholder="Filter by user..." className={`${inputClass} w-full sm:w-48`} />
        <input value={filterTool} onChange={(e) => { setFilterTool(e.target.value); setPage(1); }}
          placeholder="Filter by tool..." className={`${inputClass} w-full sm:w-48`} />
      </div>

      {/* Mobile card view */}
      <div className="lg:hidden space-y-2">
        {isLoading && (
          <div className="bg-[var(--card)] border border-[var(--border)] px-6 py-8 text-center text-[var(--text-muted)]">Loading...</div>
        )}
        {!isLoading && entries.length === 0 && (
          <div className="bg-[var(--card)] border border-[var(--border)] px-6 py-8 text-center text-[var(--text-muted)]">No audit entries</div>
        )}
        {entries.map((e: any, i: number) => (
          <div key={e.id || i} className="bg-[var(--card)] border border-[var(--border)] p-4 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs text-[var(--text-soft)]">{e.tool_name}</span>
              <span className={`inline-flex px-2 py-0.5 text-xs font-medium ${e.success !== false ? 'bg-[var(--success)]/10 text-[var(--success)]' : 'bg-[var(--destructive)]/10 text-[var(--destructive)]'}`}>
                {e.success !== false ? 'Success' : 'Failed'}
              </span>
            </div>
            <p className="text-xs text-[var(--text-muted)]">{e.email || e.user_id}</p>
            <div className="flex items-center justify-between text-xs text-[var(--text-muted)]">
              <span>{e.created_at ? format(new Date(e.created_at), 'MMM d, HH:mm:ss') : '—'}</span>
              <span>{e.credit_cost?.toFixed(2)} credits</span>
            </div>
          </div>
        ))}
      </div>

      {/* Table */}
      <div className="hidden lg:block bg-[var(--card)] border border-[var(--border)]">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left">
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Timestamp</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">User</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Tool / Action</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Credit Cost</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Status</th>
                <th className="px-6 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">Time (ms)</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && <tr><td colSpan={6} className="px-6 py-8 text-center text-[var(--text-muted)]">Loading...</td></tr>}
              {entries.map((e: any, i: number) => (
                <tr key={e.id || i} className="border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--bg)]">
                  <td className="px-6 py-3 text-[var(--text-soft)] whitespace-nowrap">
                    {e.created_at ? format(new Date(e.created_at), 'MMM d, HH:mm:ss') : '—'}
                  </td>
                  <td className="px-6 py-3 text-[var(--text)]">{e.email || e.user_id}</td>
                  <td className="px-6 py-3 font-mono text-xs text-[var(--text-soft)]">{e.tool_name}</td>
                  <td className="px-6 py-3 text-[var(--text)]">{e.credit_cost?.toFixed(2)}</td>
                  <td className="px-6 py-3">
                    <span className={`inline-flex px-2 py-0.5 text-xs font-medium ${
                      e.success !== false ? 'bg-[var(--success)]/10 text-[var(--success)]' : 'bg-[var(--destructive)]/10 text-[var(--destructive)]'
                    }`}>
                      {e.success !== false ? 'Success' : 'Failed'}
                    </span>
                  </td>
                  <td className="px-6 py-3 text-[var(--text-soft)]">{e.execution_time_ms || '—'}</td>
                </tr>
              ))}
              {!isLoading && entries.length === 0 && <tr><td colSpan={6} className="px-6 py-8 text-center text-[var(--text-muted)]">No audit entries</td></tr>}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="px-6 py-3 border-t border-[var(--border)] flex items-center justify-between">
            <span className="text-xs text-[var(--text-muted)]">Page {page} of {totalPages}</span>
            <div className="flex gap-2">
              <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page <= 1}
                className="p-1.5 border border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg-warm)] transition-colors disabled:opacity-30">
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button onClick={() => setPage(Math.min(totalPages, page + 1))} disabled={page >= totalPages}
                className="p-1.5 border border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg-warm)] transition-colors disabled:opacity-30">
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
