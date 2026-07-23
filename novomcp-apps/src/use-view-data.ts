/**
 * Shared data-extraction helper for MCP App viewers.
 *
 * Every viewer reads the tool payload from the same place — toolResult's
 * `structuredContent`, with a fall-through to `toolInputs` (streaming /
 * pre-result display) and finally an empty object so downstream reads
 * don't have to null-check. This hook centralizes that chain so payload-
 * shape or SDK changes only require editing one file.
 *
 * Viewers with a multi-path fallback (content[].text JSON parsing, deep
 * nested analysis extraction) — md-results, jobs, credit-usage,
 * pipeline-audit, molecule-viewer — keep their bespoke logic because the
 * extra paths are load-bearing for async-job responses whose schema may
 * not pass SDK validation.
 */

/**
 * Accepts either the full ViewProps object or just { toolInputs, toolResult }.
 * Callers typed against ViewProps<T> pass `props` directly; callers that
 * don't have a typed shape can pass the minimal pair.
 */
interface MinimalProps<T> {
  toolInputs?: T | null;
  toolResult?: unknown;
}

export function useViewData<T>(props: MinimalProps<T>): T {
  const { toolInputs, toolResult } = props;
  const fromResult =
    toolResult && typeof toolResult === "object"
      ? ((toolResult as Record<string, unknown>).structuredContent as T | undefined)
      : undefined;
  return fromResult ?? toolInputs ?? ({} as T);
}
