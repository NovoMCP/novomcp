'use client';

import { Fragment, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Download, FlaskConical, Loader2, UserCheck } from 'lucide-react';

// Runs: one row per run (everything filed under a funnel_id). Open a run for
// its timeline: the tool calls it made (exploration) and the stages the model
// saved (checkpoints: molecules in and out, what was filtered and why, the
// human decision and reasoning, curation method, system preparation). Read
// from /api/local/runs, which reads the engine's local audit file.

interface RunSummary {
  funnel_id: string;
  started_at: string;
  last_activity: string;
  tool_calls: number;
  checkpoints: number;
  last_stage: string | null;
}

type Stage = Record<string, unknown> & {
  event_type: string;
  timestamp: string;
  stage_label?: string;
  tool_name?: string;
};

const when = (iso?: string) => (iso ? new Date(iso).toLocaleString() : '');

export default function RunsView({ onShowCalls }: { onShowCalls: (runId: string) => void }) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/local/runs', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setRuns(d?.runs ?? []))
      .catch(() => setRuns([]));
  }, []);

  if (runs === null) return <div className="px-6 py-10 text-sm text-[var(--text-muted)]">Loading…</div>;
  if (runs.length === 0) {
    return (
      <div className="px-6 py-12 text-center">
        <FlaskConical className="h-7 w-7 text-[var(--text-muted)] mx-auto mb-2" />
        <p className="text-sm text-[var(--text-muted)]">No runs yet.</p>
        <p className="text-xs text-[var(--text-muted)] mt-1">
          Run a discovery funnel from an MCP client (for example, ask for &ldquo;agm&rdquo; on a disease) and its full trail shows up here.
        </p>
      </div>
    );
  }

  return (
    <div>
      {runs.map((run) => {
        const isOpen = open === run.funnel_id;
        return (
          <div key={run.funnel_id} className="border-b border-[var(--border)] last:border-0">
            <button
              onClick={() => setOpen(isOpen ? null : run.funnel_id)}
              className="w-full flex items-center gap-4 px-4 py-3.5 text-left hover:bg-[var(--bg-warm)] transition-colors"
            >
              {isOpen ? (
                <ChevronDown className="h-4 w-4 text-[var(--text-muted)] shrink-0" />
              ) : (
                <ChevronRight className="h-4 w-4 text-[var(--text-muted)] shrink-0" />
              )}
              <div className="flex-1 min-w-0">
                <div className="font-mono text-sm text-[var(--text)] truncate">{run.funnel_id}</div>
                <div className="text-xs text-[var(--text-muted)] mt-0.5">
                  {when(run.started_at)}
                  {run.last_stage && <> · last: {run.last_stage}</>}
                </div>
              </div>
              <div className="text-xs text-[var(--text-muted)] text-right shrink-0">
                {run.tool_calls} {run.tool_calls === 1 ? 'call' : 'calls'}
                {run.checkpoints > 0 && (
                  <span className="ml-2 text-[var(--accent)]">
                    {run.checkpoints} {run.checkpoints === 1 ? 'stage' : 'stages'} saved
                  </span>
                )}
              </div>
            </button>
            {isOpen && <RunTimeline runId={run.funnel_id} onShowCalls={onShowCalls} />}
          </div>
        );
      })}
    </div>
  );
}

function RunTimeline({ runId, onShowCalls }: { runId: string; onShowCalls: (runId: string) => void }) {
  const [stages, setStages] = useState<Stage[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    fetch(`/api/local/runs?id=${encodeURIComponent(runId)}`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setStages(d?.stages ?? []))
      .catch(() => setStages([]));
  }, [runId]);

  if (stages === null) {
    return (
      <div className="px-6 py-6 text-sm text-[var(--text-muted)] flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading the run…
      </div>
    );
  }

  return (
    <div className="bg-[var(--bg-warm)] px-4 pb-4">
      <div className="flex flex-wrap items-center justify-between gap-2 py-3">
        <p className="text-xs text-[var(--text-muted)]">
          Every step in order. <span className="text-[var(--text-soft)]">Calls</span> are what the run executed;{' '}
          <span className="text-[var(--accent)]">stages</span> are checkpoints saved with what was kept, what was filtered and who decided.
        </p>
        <div className="flex gap-3 text-xs">
          <button onClick={() => onShowCalls(runId)} className="text-[var(--accent)] hover:underline">
            Show these calls
          </button>
          <a
            href={`/api/local/runs?id=${encodeURIComponent(runId)}&format=json`}
            className="inline-flex items-center gap-1 text-[var(--accent)] hover:underline"
          >
            <Download className="h-3.5 w-3.5" /> Download run
          </a>
        </div>
      </div>
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-md overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wider text-[var(--text-muted)] border-b border-[var(--border)]">
              <th className="w-6" />
              <th className="px-3 py-2 font-normal">Step</th>
              <th className="px-3 py-2 font-normal">Molecules</th>
              <th className="px-3 py-2 font-normal">Filtered out</th>
              <th className="px-3 py-2 font-normal">Review</th>
              <th className="px-3 py-2 font-normal text-right">When</th>
            </tr>
          </thead>
          <tbody>
            {stages.map((st, i) => {
              const checkpoint = st.event_type !== 'exploration';
              const isOpen = open === i;
              const filtered = (st.molecules_filtered ?? null) as Record<string, number> | null;
              const filteredTotal = filtered ? Object.values(filtered).reduce((a, b) => a + (Number(b) || 0), 0) : 0;
              return (
                <Fragment key={i}>
                  <tr
                    onClick={() => setOpen(isOpen ? null : i)}
                    className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--bg-warm)] cursor-pointer align-top"
                  >
                    <td className="pl-3 py-2.5 text-[var(--text-muted)]">
                      {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    </td>
                    <td className="px-3 py-2.5">
                      <div className={checkpoint ? 'text-[var(--accent)] font-medium' : 'text-[var(--text)]'}>
                        {typeof st.funnel_stage === 'number' && <span className="tabular-nums">Stage {st.funnel_stage} · </span>}
                        {st.stage_label ?? st.tool_name}
                        {st.success === false && <span className="text-amber-500 font-normal"> · failed</span>}
                      </div>
                      {st.tool_name && st.tool_name !== st.stage_name && (
                        <div className="font-mono text-xs text-[var(--text-muted)]">{st.tool_name}</div>
                      )}
                    </td>
                    <td className="px-3 py-2.5 tabular-nums text-[var(--text-soft)] whitespace-nowrap">
                      {st.molecules_in != null || st.molecules_out != null
                        ? `${st.molecules_in ?? '—'} → ${st.molecules_out ?? '—'}`
                        : ''}
                    </td>
                    <td className="px-3 py-2.5 text-[var(--text-soft)]">
                      {filtered && filteredTotal > 0
                        ? Object.entries(filtered)
                            .map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`)
                            .join(', ')
                        : ''}
                    </td>
                    <td className="px-3 py-2.5 text-[var(--text-soft)] max-w-[260px]">
                      {st.human_reviewed === true || st.human_decision ? (
                        <span className="inline-flex items-start gap-1.5">
                          <UserCheck className="h-3.5 w-3.5 mt-0.5 text-emerald-500 shrink-0" />
                          <span className="truncate">{String(st.human_decision ?? 'Reviewed')}</span>
                        </span>
                      ) : (
                        ''
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right text-xs text-[var(--text-muted)] whitespace-nowrap">{when(st.timestamp)}</td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b border-[var(--border)] bg-[var(--bg-warm)]">
                      <td />
                      <td colSpan={5} className="px-3 py-3">
                        <StageDetail stage={st} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// The fields the model saved for this step, in the order a reviewer reads them.
const DETAIL_FIELDS: { key: string; label: string }[] = [
  { key: 'human_prompt', label: 'What the user said' },
  { key: 'human_decision', label: 'Decision' },
  { key: 'decision_reasoning', label: 'Reasoning' },
  { key: 'ai_recommendation', label: 'Recommendation' },
  { key: 'failure_pattern', label: 'What didn’t work' },
  { key: 'curation_method', label: 'Curation method' },
  { key: 'system_metadata', label: 'System preparation' },
  { key: 'context_forward', label: 'Carried forward' },
  { key: 'tool_arguments', label: 'Inputs' },
  { key: 'results_summary', label: 'Results' },
];

function StageDetail({ stage }: { stage: Stage }) {
  const shown = DETAIL_FIELDS.filter(({ key }) => {
    const v = stage[key];
    return v !== undefined && v !== null && v !== '' && !(typeof v === 'object' && Object.keys(v as object).length === 0);
  });
  if (shown.length === 0) return <p className="text-sm text-[var(--text-muted)]">Nothing else was recorded for this step.</p>;
  return (
    <dl className="grid gap-3 md:grid-cols-2">
      {shown.map(({ key, label }) => {
        const v = stage[key];
        return (
          <div key={key} className="min-w-0">
            <dt className="text-[11px] uppercase tracking-wider text-[var(--text-muted)] mb-1">{label}</dt>
            <dd>
              {typeof v === 'string' ? (
                <p className="text-sm text-[var(--text)] whitespace-pre-wrap">{v}</p>
              ) : (
                <pre className="text-xs font-mono text-[var(--text-soft)] bg-[var(--card)] border border-[var(--border)] rounded p-2.5 overflow-x-auto max-h-60">
                  {JSON.stringify(v, null, 2)}
                </pre>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
