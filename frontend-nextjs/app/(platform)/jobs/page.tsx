'use client';

import { useState } from 'react';
import { useAuth } from '@/core/auth/provider';
import { useAsyncJobs, useJobContext, usePipelineAudit } from '@/core/api/admin-client';
import { FlaskConical, Clock, CheckCircle2, XCircle, Loader2, Copy, ChevronDown, ChevronUp, Download, FileText } from 'lucide-react';

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    submitted: 'bg-[var(--accent)]/10 text-[var(--accent)]',
    running: 'bg-[var(--accent)]/10 text-[var(--accent)]',
    completed: 'bg-[var(--success)]/10 text-[var(--success)]',
    failed: 'bg-[var(--destructive)]/10 text-[var(--destructive)]',
  };
  const icons: Record<string, React.ReactNode> = {
    submitted: <Clock className="h-3 w-3" />,
    running: <Loader2 className="h-3 w-3 animate-spin" />,
    completed: <CheckCircle2 className="h-3 w-3" />,
    failed: <XCircle className="h-3 w-3" />,
  };

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium ${styles[status] || 'bg-[var(--muted)] text-[var(--text-muted)]'}`}>
      {icons[status]} {status}
    </span>
  );
}

function ServiceLabel({ service }: { service: string }) {
  const labels: Record<string, string> = {
    'gromacs-md': 'MD Simulation',
    'autodock-gpu': 'Docking',
    'lead-optimization': 'Lead Optimization',
  };
  return <span className="font-mono text-xs text-[var(--text-soft)]">{labels[service] || service}</span>;
}

function formatDuration(submitted: string | null, completed: string | null): string {
  if (!submitted) return '—';
  const start = new Date(submitted).getTime();
  const end = completed ? new Date(completed).getTime() : Date.now();
  const mins = Math.round((end - start) / 60000);
  if (mins < 1) return '<1 min';
  if (mins < 60) return `${mins} min`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return `${hrs}h ${rem}m`;
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ', ' +
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

function DispositionBadge({ disposition }: { disposition: string }) {
  if (disposition === 'included') return <span className="text-[10px] px-1.5 py-0.5 bg-[var(--success)]/10 text-[var(--success)] font-medium">included</span>;
  if (disposition === 'excluded') return <span className="text-[10px] px-1.5 py-0.5 bg-[var(--destructive)]/10 text-[var(--destructive)] font-medium">excluded</span>;
  return <span className="text-[10px] px-1.5 py-0.5 bg-[var(--accent)]/10 text-[var(--accent)] font-medium">{disposition}</span>;
}

function AuditLogPanel({ pipelineId }: { pipelineId: string }) {
  const { data, isLoading } = usePipelineAudit(pipelineId);
  const [filter, setFilter] = useState<'all' | 'included' | 'excluded'>('all');

  if (isLoading) return <div className="px-6 py-4 text-sm text-[var(--text-muted)]">Loading audit log...</div>;
  if (!data?.molecule_audit_log?.length) return <div className="px-6 py-4 text-sm text-[var(--text-muted)]">No audit data available.</div>;

  const log: any[] = data.molecule_audit_log;
  const summary = data.audit_summary || {};
  const filtered = filter === 'all' ? log : log.filter((e: any) => e.disposition === filter);

  return (
    <div className="space-y-3">
      {/* Summary bar */}
      <div className="flex items-center gap-4 text-xs">
        <span className="text-[var(--text-muted)]">{summary.total || log.length} molecules</span>
        <span className="text-[var(--success)]">{summary.included || 0} included</span>
        <span className="text-[var(--destructive)]">{summary.excluded || 0} excluded</span>
        {summary.invalid_smiles > 0 && <span className="text-[var(--text-muted)]">{summary.invalid_smiles} invalid</span>}
        <div className="ml-auto flex gap-1">
          {(['all', 'included', 'excluded'] as const).map((f) => (
            <button key={f} onClick={() => setFilter(f)}
              className={`px-2 py-0.5 text-[10px] border border-[var(--border)] ${filter === f ? 'bg-[var(--accent)] text-white border-[var(--accent)]' : 'text-[var(--text-soft)] hover:bg-[var(--bg)]'}`}>
              {f}
            </button>
          ))}
          <a
            href={`/api/mcp/pipelines/${pipelineId}/audit/csv`}
            download
            className="flex items-center gap-1 px-2 py-0.5 text-[10px] border border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg)]"
          >
            <Download className="h-2.5 w-2.5" /> CSV
          </a>
        </div>
      </div>

      {/* Audit table */}
      <div className="overflow-x-auto border border-[var(--border)]">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-[var(--bg)] border-b border-[var(--border)]">
              <th className="px-2 py-1.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">#</th>
              <th className="px-2 py-1.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">SMILES</th>
              <th className="px-2 py-1.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">MW</th>
              <th className="px-2 py-1.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">LogP</th>
              <th className="px-2 py-1.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">QED</th>
              <th className="px-2 py-1.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">hERG</th>
              <th className="px-2 py-1.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">Compliance</th>
              <th className="px-2 py-1.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">Disposition</th>
              <th className="px-2 py-1.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">Reason</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((entry: any) => {
              const props = entry.tools_applied?.calculate_properties?.key_results || {};
              const admet = entry.tools_applied?.predict_admet?.key_results || {};
              const compliance = entry.tools_applied?.check_compliance?.key_results || {};
              return (
                <tr key={entry.row_index} className="border-b border-[var(--border)] hover:bg-[var(--bg)]/50">
                  <td className="px-2 py-1.5 text-[var(--text-muted)]">{entry.row_index}</td>
                  <td className="px-2 py-1.5 font-mono text-[var(--text)] max-w-[200px] truncate">{entry.input_smiles || '—'}</td>
                  <td className="px-2 py-1.5 text-[var(--text-soft)]">{props.mw || '—'}</td>
                  <td className="px-2 py-1.5 text-[var(--text-soft)]">{props.logp || '—'}</td>
                  <td className="px-2 py-1.5 text-[var(--text-soft)]">{props.qed || '—'}</td>
                  <td className="px-2 py-1.5">
                    {admet.herg !== undefined ? (
                      <span className={admet.herg > 0.5 ? 'text-[var(--destructive)]' : 'text-[var(--success)]'}>
                        {admet.herg}
                      </span>
                    ) : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-[var(--text-soft)]">{compliance.overall_status || '—'}</td>
                  <td className="px-2 py-1.5"><DispositionBadge disposition={entry.disposition} /></td>
                  <td className="px-2 py-1.5 text-[var(--text-muted)] max-w-[150px] truncate">{entry.exclusion_reason || ''}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function JobDetailPanel({ jobId, service }: { jobId: string; service: string }) {
  const { data, isLoading } = useJobContext(jobId);
  const [activeTab, setActiveTab] = useState<'context' | 'audit'>('context');
  const isPipeline = jobId.startsWith('pipe_');

  if (isLoading) return <div className="px-6 py-4 text-sm text-[var(--text-muted)]">Loading context...</div>;
  if (!data) return <div className="px-6 py-4 text-sm text-[var(--text-muted)]">No context saved for this job.</div>;

  const ctx = data.funnel_context || {};
  const results = data.result_data;

  return (
    <div className="px-6 py-4 bg-[var(--bg)] border-t border-[var(--border)] space-y-3">
      {/* Tab bar for pipeline jobs */}
      {isPipeline && (
        <div className="flex gap-0 border border-[var(--border)] w-fit">
          <button onClick={() => setActiveTab('context')}
            className={`px-3 py-1 text-xs font-medium ${activeTab === 'context' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-soft)] hover:bg-[var(--bg-warm)]'}`}>
            Context
          </button>
          <button onClick={() => setActiveTab('audit')}
            className={`px-3 py-1 text-xs font-medium flex items-center gap-1 ${activeTab === 'audit' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-soft)] hover:bg-[var(--bg-warm)]'}`}>
            <FileText className="h-3 w-3" /> Audit Log
          </button>
        </div>
      )}

      {/* Audit tab */}
      {isPipeline && activeTab === 'audit' ? (
        <AuditLogPanel pipelineId={jobId} />
      ) : (
        <>
          {/* Funnel Context */}
          {Object.keys(ctx).length > 0 && (
            <div>
              <p className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider mb-1">Funnel Context</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                {ctx.target_gene && (
                  <div><span className="text-[var(--text-muted)]">Target:</span> <span className="font-medium text-[var(--text)]">{ctx.target_gene || ctx.pdb_id}</span></div>
                )}
                {ctx.pdb_id && (
                  <div><span className="text-[var(--text-muted)]">PDB:</span> <span className="font-mono text-[var(--text)]">{ctx.pdb_id}</span></div>
                )}
                {ctx.duration_ns && (
                  <div><span className="text-[var(--text-muted)]">Duration:</span> <span className="text-[var(--text)]">{ctx.duration_ns}ns</span></div>
                )}
                {ctx.smiles && (
                  <div className="col-span-2"><span className="text-[var(--text-muted)]">SMILES:</span> <span className="font-mono text-[var(--text)]">{ctx.smiles.slice(0, 50)}{ctx.smiles.length > 50 ? '...' : ''}</span></div>
                )}
              </div>
            </div>
          )}

          {/* Results (if completed) */}
          {results && (
            <div>
              <p className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider mb-1">Results</p>
              <pre className="text-xs text-[var(--text-soft)] bg-[var(--card)] p-3 border border-[var(--border)] overflow-x-auto max-h-48">
                {JSON.stringify(results, null, 2)}
              </pre>
            </div>
          )}

          {/* Copy resume prompt */}
          <div className="flex items-center gap-2">
            <button
              onClick={(e) => {
                const prompt = `What are the results for ${jobId}?`;
                navigator.clipboard.writeText(prompt);
                const btn = e.currentTarget;
                const original = btn.innerHTML;
                btn.innerHTML = `<svg class="h-3 w-3" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg> Copied`;
                btn.classList.add('text-green-600', 'border-green-300');
                setTimeout(() => { btn.innerHTML = original; btn.classList.remove('text-green-600', 'border-green-300'); }, 2000);
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-[var(--border)] text-[var(--text-soft)] hover:bg-[var(--bg-warm)] transition-colors"
            >
              <Copy className="h-3 w-3" /> Copy Prompt
            </button>
            <span className="text-xs text-[var(--text-muted)]">Paste into any AI assistant with NovoMCP</span>
          </div>
        </>
      )}
    </div>
  );
}

export default function JobsPage() {
  const { user } = useAuth();
  const [filterStatus, setFilterStatus] = useState('');
  const [expandedJob, setExpandedJob] = useState<string | null>(null);

  const { data, isLoading } = useAsyncJobs({
    status: filterStatus || undefined,
    service: undefined,
  });

  const jobs = data?.jobs || [];

  const inputClass = "px-3 py-2 bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--accent)]";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold text-[var(--text)]" style={{ fontFamily: 'var(--serif)' }}>Pipeline Jobs</h1>
        <p className="text-sm text-[var(--text-muted)]">Track molecular dynamics simulations and other long-running compute jobs</p>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className={inputClass}
        >
          <option value="">All statuses</option>
          <option value="submitted">Submitted</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {/* Stats row */}
      {jobs.length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {['submitted', 'running', 'completed', 'failed'].map((s) => {
            const count = jobs.filter((j: any) => j.status === s).length;
            return (
              <div key={s} className="bg-[var(--card)] border border-[var(--border)] px-4 py-3">
                <p className="text-xs text-[var(--text-muted)] uppercase">{s}</p>
                <p className="text-xl font-semibold text-[var(--text)]">{count}</p>
              </div>
            );
          })}
        </div>
      )}

      {/* Mobile card view */}
      <div className="lg:hidden space-y-2">
        {isLoading && (
          <div className="bg-[var(--card)] border border-[var(--border)] px-6 py-8 text-center text-[var(--text-muted)]">Loading jobs...</div>
        )}
        {!isLoading && jobs.length === 0 && (
          <div className="bg-[var(--card)] border border-[var(--border)] px-6 py-12 text-center">
            <FlaskConical className="h-8 w-8 text-[var(--text-muted)] mx-auto mb-2" />
            <p className="text-[var(--text-muted)]">No pipeline jobs yet</p>
            <p className="text-xs text-[var(--text-muted)] mt-1">Jobs appear here when you run molecular dynamics simulations via the MCP funnel</p>
          </div>
        )}
        {jobs.map((job: any) => (
          <div key={job.job_id} className="bg-[var(--card)] border border-[var(--border)] p-4 space-y-2"
            onClick={() => setExpandedJob(expandedJob === job.job_id ? null : job.job_id)}>
            <div className="flex items-center justify-between">
              <ServiceLabel service={job.service} />
              <StatusBadge status={job.status} />
            </div>
            <p className="font-mono text-xs text-[var(--text-soft)] truncate">{job.job_id}</p>
            <div className="flex justify-between text-xs text-[var(--text-muted)]">
              <span>{formatTime(job.submitted_at)}</span>
              <span>{formatDuration(job.submitted_at, job.completed_at)}</span>
            </div>
            {expandedJob === job.job_id && <JobDetailPanel jobId={job.job_id} service={job.service} />}
          </div>
        ))}
      </div>

      {/* Jobs table */}
      <div className="hidden lg:block bg-[var(--card)] border border-[var(--border)]">
        <div className="overflow-x-auto">
          {/* Column grid: chevron | job id | service | status | submitted | duration */}
          <div className="grid text-sm" style={{ gridTemplateColumns: '40px minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 0.8fr)' }}>
            {/* Header */}
            <div className="border-b border-[var(--border)]" />
            <div className="px-4 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border)]">Job ID</div>
            <div className="px-4 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border)]">Service</div>
            <div className="px-4 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border)]">Status</div>
            <div className="px-4 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border)]">Submitted</div>
            <div className="px-4 py-3 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border)]">Duration</div>

            {isLoading && (
              <div className="col-span-6 px-6 py-8 text-center text-[var(--text-muted)]">Loading jobs...</div>
            )}
            {!isLoading && jobs.length === 0 && (
              <div className="col-span-6 px-6 py-12 text-center">
                <FlaskConical className="h-8 w-8 text-[var(--text-muted)] mx-auto mb-2" />
                <p className="text-[var(--text-muted)]">No pipeline jobs yet</p>
                <p className="text-xs text-[var(--text-muted)] mt-1">Jobs appear here when you run molecular dynamics simulations via the MCP funnel</p>
              </div>
            )}
            {jobs.map((job: any) => {
              const isExpanded = expandedJob === job.job_id;
              return (
                <div key={job.job_id} className="col-span-6">
                  <div
                    className={`grid items-center cursor-pointer border-b border-[var(--border)] hover:bg-[var(--bg)] transition-colors ${isExpanded ? 'bg-[var(--bg)]/60' : ''}`}
                    style={{ gridTemplateColumns: '40px minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 0.8fr)' }}
                    onClick={() => setExpandedJob(isExpanded ? null : job.job_id)}
                  >
                    <div className="px-3 py-3 flex justify-center">
                      {isExpanded ? <ChevronUp className="h-4 w-4 text-[var(--text-muted)]" /> : <ChevronDown className="h-4 w-4 text-[var(--text-muted)]" />}
                    </div>
                    <div className="px-4 py-3 font-mono text-xs text-[var(--text)] truncate">{job.job_id}</div>
                    <div className="px-4 py-3"><ServiceLabel service={job.service} /></div>
                    <div className="px-4 py-3"><StatusBadge status={job.status} /></div>
                    <div className="px-4 py-3 text-[var(--text-soft)] whitespace-nowrap">{formatTime(job.submitted_at)}</div>
                    <div className="px-4 py-3 text-[var(--text-soft)] whitespace-nowrap">{formatDuration(job.submitted_at, job.completed_at)}</div>
                  </div>
                  {isExpanded && <JobDetailPanel jobId={job.job_id} service={job.service} />}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
