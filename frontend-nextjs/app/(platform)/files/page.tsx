'use client';

import { useState, Fragment } from 'react';
import { useAuth } from '@/core/auth/provider';
import { useFiles as useFilesQuery } from '@/core/api/admin-client';
import {
  FileUp, Clock, CheckCircle2, XCircle, Loader2,
  Copy, Download, ChevronDown, ChevronUp, Filter,
  FlaskConical, FileText, Database, Atom,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FileRecord {
  file_id: string;
  filename: string;
  file_type: string;
  status: string;
  size_bytes: number | null;
  upload_source: string;
  created_at: string;
  expires_at: string | null;
  linked_job_ids: string[];
  linked_tool_calls?: Array<{ tool: string; timestamp: string; job_id?: string }>;
  processing_results?: any;
  parent_file_id?: string | null;
  content_hash?: string;
  metadata?: Record<string, any>;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

function useFiles(fileType?: string, status?: string) {
  return useFilesQuery({
    file_type: fileType,
    status: status,
  });
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending_upload: 'bg-[var(--accent)]/10 text-[var(--accent)]',
    uploaded: 'bg-[var(--success)]/10 text-[var(--success)]',
    processing: 'bg-[var(--accent)]/10 text-[var(--accent)]',
    completed: 'bg-[var(--success)]/10 text-[var(--success)]',
    expired: 'bg-[var(--text-muted)]/10 text-[var(--text-muted)]',
    deleted: 'bg-[var(--destructive)]/10 text-[var(--destructive)]',
    failed: 'bg-[var(--destructive)]/10 text-[var(--destructive)]',
  };
  const icons: Record<string, React.ReactNode> = {
    pending_upload: <Clock className="h-3 w-3" />,
    uploaded: <CheckCircle2 className="h-3 w-3" />,
    processing: <Loader2 className="h-3 w-3 animate-spin" />,
    completed: <CheckCircle2 className="h-3 w-3" />,
    expired: <XCircle className="h-3 w-3" />,
    failed: <XCircle className="h-3 w-3" />,
  };

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium ${styles[status] || 'bg-[var(--bg)] text-[var(--text-muted)]'}`}>
      {icons[status]} {status.replace('_', ' ')}
    </span>
  );
}

function FileTypeIcon({ type }: { type: string }) {
  const icons: Record<string, React.ReactNode> = {
    qm_log: <Atom className="h-4 w-4 text-[var(--accent)]" />,
    pdb: <FlaskConical className="h-4 w-4 text-[var(--success)]" />,
    trajectory: <Database className="h-4 w-4 text-[var(--accent)]" />,
    library: <Database className="h-4 w-4 text-[var(--text-soft)]" />,
    frcmod: <FileText className="h-4 w-4 text-[var(--success)]" />,
    custom: <FileUp className="h-4 w-4 text-[var(--text-muted)]" />,
  };
  return <>{icons[type] || icons.custom}</>;
}

function FileTypeLabel({ type }: { type: string }) {
  const labels: Record<string, string> = {
    qm_log: 'QM Log',
    pdb: 'PDB Structure',
    trajectory: 'Trajectory',
    library: 'Compound Library',
    frcmod: 'Force Field',
    custom: 'Custom',
  };
  return <span className="text-xs text-[var(--text-soft)]">{labels[type] || type}</span>;
}

function formatSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ', ' +
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

function FileDetailPanel({ file }: { file: FileRecord }) {
  const [copied, setCopied] = useState(false);

  const copyId = () => {
    navigator.clipboard.writeText(file.file_id);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="px-6 py-4 bg-[var(--bg)] border-t border-[var(--border)] space-y-3">
      {/* File ID */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-[var(--text-muted)]">File ID:</span>
        <code className="font-mono text-xs text-[var(--accent)]">{file.file_id}</code>
        <button onClick={copyId} className="p-0.5 text-[var(--text-muted)] hover:text-[var(--accent)]">
          {copied ? <CheckCircle2 className="h-3 w-3 text-[var(--success)]" /> : <Copy className="h-3 w-3" />}
        </button>
      </div>

      {/* Metadata grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <div>
          <span className="text-[var(--text-muted)] block">Type</span>
          <FileTypeLabel type={file.file_type} />
        </div>
        <div>
          <span className="text-[var(--text-muted)] block">Size</span>
          <span className="text-[var(--text)]">{formatSize(file.size_bytes)}</span>
        </div>
        <div>
          <span className="text-[var(--text-muted)] block">Source</span>
          <span className="text-[var(--text)]">{file.upload_source}</span>
        </div>
        <div>
          <span className="text-[var(--text-muted)] block">Expires</span>
          <span className="text-[var(--text)]">{file.status === 'expired' ? 'Expired' : file.expires_at ? formatTime(file.expires_at) : 'Never'}</span>
        </div>
      </div>

      {/* Content hash */}
      {file.content_hash && (
        <div className="text-xs">
          <span className="text-[var(--text-muted)]">SHA-256: </span>
          <code className="font-mono text-[10px] text-[var(--text-soft)]">{file.content_hash}</code>
        </div>
      )}

      {/* Parent file (provenance) */}
      {file.parent_file_id && (
        <div className="text-xs">
          <span className="text-[var(--text-muted)]">Derived from: </span>
          <code className="font-mono text-[var(--accent)]">{file.parent_file_id}</code>
        </div>
      )}

      {/* Linked jobs */}
      {file.linked_job_ids.length > 0 && (
        <div>
          <span className="text-xs text-[var(--text-muted)] block mb-1">Linked Jobs</span>
          <div className="flex flex-wrap gap-1">
            {file.linked_job_ids.map((id) => (
              <span key={id} className="font-mono text-[10px] px-2 py-0.5 bg-[var(--bg-warm)] text-[var(--text-soft)] border border-[var(--border)]">
                {id}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Linked tool calls */}
      {file.linked_tool_calls && file.linked_tool_calls.length > 0 && (
        <div>
          <span className="text-xs text-[var(--text-muted)] block mb-1">Tool History</span>
          <div className="space-y-1">
            {file.linked_tool_calls.map((call, i) => (
              <div key={i} className="flex items-center gap-2 text-[10px]">
                <code className="font-mono text-[var(--accent)]">{call.tool}</code>
                <span className="text-[var(--text-muted)]">{formatTime(call.timestamp)}</span>
                {call.job_id && (
                  <code className="font-mono text-[var(--text-soft)]">{call.job_id}</code>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Metadata */}
      {file.metadata && Object.keys(file.metadata).length > 0 && (
        <div>
          <span className="text-xs text-[var(--text-muted)] block mb-1">Metadata</span>
          <pre className="text-[10px] font-mono text-[var(--text-soft)] bg-[var(--bg-warm)] p-2 overflow-auto max-h-24">
            {JSON.stringify(file.metadata, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function FilesPage() {
  const [typeFilter, setTypeFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data, isLoading, error } = useFiles(
    typeFilter || undefined,
    statusFilter || undefined,
  );

  const files: FileRecord[] = data?.files || [];

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text)]">Files</h1>
          <p className="text-sm text-[var(--text-soft)] mt-1">
            Uploaded files and processing outputs across all tools
          </p>
        </div>
        <div className="text-xs text-[var(--text-muted)]">
          {files.length} file{files.length !== 1 ? 's' : ''}
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 mb-4">
        <Filter className="h-4 w-4 text-[var(--text-muted)]" />

        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="text-xs bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] px-2 py-1"
        >
          <option value="">All types</option>
          <option value="qm_log">QM Logs</option>
          <option value="pdb">PDB Structures</option>
          <option value="trajectory">Trajectories</option>
          <option value="library">Libraries</option>
          <option value="frcmod">Force Field</option>
          <option value="custom">Custom</option>
        </select>

        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="text-xs bg-[var(--bg)] border border-[var(--border)] text-[var(--text)] px-2 py-1"
        >
          <option value="">All statuses</option>
          <option value="pending_upload">Pending Upload</option>
          <option value="uploaded">Uploaded</option>
          <option value="processing">Processing</option>
          <option value="completed">Completed</option>
          <option value="expired">Expired</option>
        </select>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-12 text-sm text-[var(--text-muted)]">
          <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading files...
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="py-8 text-center text-sm text-[var(--destructive)]">
          Failed to load files: {(error as Error).message}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !error && files.length === 0 && (
        <div className="py-16 text-center">
          <FileUp className="h-10 w-10 text-[var(--text-muted)] mx-auto mb-4" />
          <h3 className="text-sm font-medium text-[var(--text)]">No files yet</h3>
          <p className="text-xs text-[var(--text-muted)] mt-1 max-w-xs mx-auto">
            Upload files through Claude, NovoWorkbench, or the API using the{' '}
            <code className="font-mono text-[var(--accent)]">generate_upload_url</code> tool.
          </p>
        </div>
      )}

      {/* Files table */}
      {files.length > 0 && (
        <div className="border border-[var(--border)]">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-[var(--bg-warm)] border-b border-[var(--border)]">
                <th className="px-4 py-2.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">File</th>
                <th className="px-4 py-2.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">Type</th>
                <th className="px-4 py-2.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">Size</th>
                <th className="px-4 py-2.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">Status</th>
                <th className="px-4 py-2.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">Uploaded</th>
                <th className="px-4 py-2.5 text-left text-[10px] text-[var(--text-muted)] uppercase font-medium">Jobs</th>
              </tr>
            </thead>
            <tbody>
              {files.map((file) => (
                <Fragment key={file.file_id}>
                  <tr
                    className="border-b border-[var(--border)] hover:bg-[var(--bg)]/50 cursor-pointer group"
                    onClick={() => setExpandedId(expandedId === file.file_id ? null : file.file_id)}
                  >
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <FileTypeIcon type={file.file_type} />
                        <div>
                          <span className="text-sm text-[var(--text)]">{file.filename}</span>
                          <code className="block font-mono text-[10px] text-[var(--text-muted)]">
                            {file.file_id.slice(0, 14)}...
                          </code>
                        </div>
                        {expandedId === file.file_id
                          ? <ChevronUp className="h-3 w-3 text-[var(--text-muted)] ml-auto" />
                          : <ChevronDown className="h-3 w-3 text-[var(--text-muted)] ml-auto opacity-0 group-hover:opacity-100" />
                        }
                      </div>
                    </td>
                    <td className="px-4 py-2.5"><FileTypeLabel type={file.file_type} /></td>
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--text-soft)]">{formatSize(file.size_bytes)}</td>
                    <td className="px-4 py-2.5"><StatusBadge status={file.status} /></td>
                    <td className="px-4 py-2.5 text-xs text-[var(--text-soft)]">{formatTime(file.created_at)}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-[var(--text-muted)]">
                      {file.linked_job_ids.length > 0
                        ? `${file.linked_job_ids.length} linked`
                        : '—'
                      }
                    </td>
                  </tr>
                  {expandedId === file.file_id && (
                    <tr>
                      <td colSpan={6}>
                        <FileDetailPanel file={file} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
