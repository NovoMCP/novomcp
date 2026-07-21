/**
 * Viewer-side job polling hook.
 *
 * Implements the "private backend tool" pattern from FastMCP 3.2 adapted
 * for @modelcontextprotocol/ext-apps: the viewer itself polls get_job_status
 * via callServerTool, bypassing Claude.ai's tool-result forwarding path.
 *
 * Why this matters: for async jobs whose completion payload exceeds the
 * host's toolResult forwarding cap (~250-300 KB), the host drops toolResult
 * when the model calls get_job_status, leaving the viewer stuck on the
 * submission card. When the viewer fetches the same tool itself, the
 * response arrives directly — no size cap, no polymorphic-schema Zod drop.
 * Restores rich completion views (NGL trajectory, RMSF chart, PCA bars)
 * that had to be cut when we pulled _meta.ui.resourceUri off get_job_status.
 *
 * Consumer pattern:
 *
 *   const { data, phase, error, elapsedSeconds } = useJobPoll<MyResult>({
 *     jobId: jobIdFromSubmission,
 *     callServerTool,
 *     intervalMs: 30_000,
 *     coldStartGraceSeconds: 90,
 *     maxTotalSeconds: 30 * 60,
 *   });
 *
 *   if (phase === "completed" && data) return <RichView data={data} />;
 *   if (phase === "failed") return <FailedCard error={error} />;
 *   return <QueuedCard elapsed={elapsedSeconds} />;
 */

import { useEffect, useRef, useState } from "react";
import type { App } from "@modelcontextprotocol/ext-apps";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";

type CallServerTool = App["callServerTool"];

export type JobPhase = "queued" | "running" | "completed" | "failed" | "unknown";

/**
 * Shape returned by get_job_status. `results` is service-specific — we pass
 * it through as an unknown-typed record and let the caller cast.
 */
export interface JobStatusResponse {
  job_id?: string;
  status?: string;
  service?: string;
  completed?: boolean;
  progress?: number;
  progress_percent?: number;
  message?: string;
  results?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string;
  estimated_remaining_minutes?: number;
  [key: string]: unknown;
}

export interface JobPollOptions<TResult> {
  /** Job ID to poll. When undefined, the hook is inert. */
  jobId?: string | null;
  /** callServerTool from ViewProps. When undefined, the hook is inert. */
  callServerTool?: CallServerTool;
  /** Poll interval in ms. Default 30s. */
  intervalMs?: number;
  /** Before this many seconds, swallow connection errors (AlphaFlow cold-start). Default 90s. */
  coldStartGraceSeconds?: number;
  /** After this many seconds, stop polling and expose a timeout error. Default 30 minutes. */
  maxTotalSeconds?: number;
  /**
   * Extract the completion result from the raw job response. Different
   * services nest results differently (e.g. AlphaFlow under `results`, some
   * under `result`, some at top level). Default: tries `results`, then
   * `result`, then the response itself.
   */
  extractResult?: (raw: JobStatusResponse) => TResult | null;
}

export interface JobPollState<TResult> {
  /** Current phase derived from the latest poll. */
  phase: JobPhase;
  /** Extracted result object when phase === "completed". */
  data: TResult | null;
  /** Error message when phase === "failed" or a transport error occurred. */
  error: string | null;
  /** Seconds since the hook mounted. Useful for "N min elapsed" UI. */
  elapsedSeconds: number;
  /** Raw response from the latest poll. For debug panels. */
  raw: JobStatusResponse | null;
  /** Number of polls fired so far. */
  pollCount: number;
}

function defaultExtract<TResult>(raw: JobStatusResponse): TResult | null {
  const candidate =
    raw.results ??
    raw.result ??
    (raw as unknown as Record<string, unknown>);
  if (!candidate || typeof candidate !== "object") return null;
  return candidate as TResult;
}

function derivePhase(raw: JobStatusResponse | null): JobPhase {
  if (!raw) return "queued";
  if (raw.status === "completed" || raw.status === "success" || raw.completed === true) return "completed";
  if (raw.status === "failed" || raw.status === "error") return "failed";
  if (raw.status === "running") return "running";
  if (raw.status === "queued" || raw.status === "submitted") return "queued";
  return "unknown";
}

export function useJobPoll<TResult>(options: JobPollOptions<TResult>): JobPollState<TResult> {
  const {
    jobId,
    callServerTool,
    intervalMs = 30_000,
    coldStartGraceSeconds = 90,
    maxTotalSeconds = 30 * 60,
    extractResult = defaultExtract,
  } = options;

  const [state, setState] = useState<JobPollState<TResult>>({
    phase: "queued",
    data: null,
    error: null,
    elapsedSeconds: 0,
    raw: null,
    pollCount: 0,
  });
  const cancelledRef = useRef(false);

  useEffect(() => {
    if (!jobId || !callServerTool) return;
    cancelledRef.current = false;
    const startedAt = Date.now();

    const pollOnce = async () => {
      try {
        const result = await callServerTool({
          name: "get_job_status",
          arguments: { job_id: jobId },
        });
        if (cancelledRef.current) return null;
        // Prefer structuredContent; fall back to parsing the text block.
        const sc = (result as CallToolResult).structuredContent as JobStatusResponse | undefined;
        let raw: JobStatusResponse | null = sc && typeof sc === "object" ? sc : null;
        if (!raw && Array.isArray(result.content)) {
          for (const block of result.content) {
            if (block && typeof block === "object" && "text" in block) {
              try {
                const parsed = JSON.parse((block as any).text);
                if (parsed && typeof parsed === "object") {
                  raw = parsed as JobStatusResponse;
                  break;
                }
              } catch { /* keep trying */ }
            }
          }
        }
        return raw;
      } catch (e) {
        if (cancelledRef.current) return null;
        const elapsed = (Date.now() - startedAt) / 1000;
        // During cold-start window, swallow transport errors (AlphaFlow takes
        // ~50s to load ESMFlow). After the grace window, surface them.
        if (elapsed < coldStartGraceSeconds) {
          return null;
        }
        const msg = e instanceof Error ? e.message : String(e);
        setState((s) => ({ ...s, phase: "failed", error: `Poll error: ${msg}` }));
        return null;
      }
    };

    const tick = async () => {
      if (cancelledRef.current) return;
      const elapsed = Math.floor((Date.now() - startedAt) / 1000);
      if (elapsed > maxTotalSeconds) {
        setState((s) => ({
          ...s,
          phase: "failed",
          error: `Job exceeded ${Math.round(maxTotalSeconds / 60)}-minute poll limit. The job may still be running — check later via get_job_status.`,
          elapsedSeconds: elapsed,
        }));
        return;
      }
      const raw = await pollOnce();
      if (cancelledRef.current) return;
      setState((prev) => {
        const next: JobPollState<TResult> = {
          ...prev,
          elapsedSeconds: elapsed,
          pollCount: prev.pollCount + 1,
        };
        if (raw) {
          next.raw = raw;
          next.phase = derivePhase(raw);
          if (next.phase === "completed") next.data = extractResult(raw);
          if (next.phase === "failed" && typeof raw.error === "string") next.error = raw.error;
        }
        return next;
      });
    };

    // Fire immediately so the user sees activity, then on the requested cadence.
    tick();
    const handle = window.setInterval(() => {
      // Stop polling once terminal state reached. We can't read state from
      // inside setInterval closure directly — check inside tick via setState.
      tick();
    }, intervalMs);

    return () => {
      cancelledRef.current = true;
      window.clearInterval(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, callServerTool]);

  // Stop polling after terminal state. We do this by watching state.phase in
  // a second effect that clears the interval if it somehow kept running
  // after completion. The primary interval is cleared on unmount above.
  useEffect(() => {
    if (state.phase === "completed" || state.phase === "failed") {
      cancelledRef.current = true;
    }
  }, [state.phase]);

  return state;
}
