// How Activity shows the engine's data events: exports to a warehouse
// (data_export) and pull pipelines (data_pipeline). They aren't tool calls, so
// they carry their own fields instead of arguments and outcome.

type P = Record<string, unknown>;

export interface DataEventView {
  label: string;
  input: string;
  result: string;
  failed: boolean;
  fields: [string, string][];
}

const WAREHOUSE: Record<string, string> = { snowflake: 'Snowflake', databricks: 'Databricks' };

function str(v: unknown): string {
  return v === undefined || v === null || v === '' ? '' : String(v);
}

// "SNOWFLAKE:NOVOMCP.PUBLIC.novomcp_activity" → "NOVOMCP.PUBLIC.novomcp_activity"
function tableOf(location: unknown): string {
  return str(location).replace(/^[A-Z]+:/, '');
}

function sourceOf(p: P): string {
  const src = str(p.source_tool);
  if (src === 'activity') return 'Activity calls';
  if (src === 'run_audit' || src === 'funnel_audit') return p.funnel_id ? `Run ${str(p.funnel_id).slice(-8)}` : 'A run';
  return src ? `${src} results` : '';
}

export function describeDataEvent(event: string | undefined, p: P): DataEventView | null {
  if (event === 'data_export') {
    const where = WAREHOUSE[str(p.connector_type)] || str(p.connector_type) || 'warehouse';
    const table = tableOf(p.target_location);
    const written = Number(p.rows_written ?? 0);
    const failedRows = Number(p.rows_failed ?? 0);
    const failed = p.success === false;
    return {
      label: `Export to ${where}`,
      input: [sourceOf(p), table].filter(Boolean).join(' → '),
      result: failed
        ? str(p.error) || 'failed'
        : `${written} ${written === 1 ? 'row' : 'rows'} written${failedRows ? `, ${failedRows} failed` : ''}`,
      failed,
      fields: (
        [
          ['Destination', where],
          ['Table', table],
          ['Source', sourceOf(p)],
          ['Rows sent', str(p.rows_sent)],
          ['Rows written', str(p.rows_written)],
          ['Rows failed', failedRows ? String(failedRows) : ''],
          ['Mode', p.write_mode === 'replace' ? 'Replaced the table contents' : 'Added rows'],
          ['Table created', p.created_table === true ? 'Yes, by this export' : ''],
          ['Export id', str(p.export_id)],
          ['Connection', str(p.connection_id)],
          ['Error', str(p.error)],
        ] as [string, string][]
      ).filter(([, v]) => v),
    };
  }
  if (event === 'data_pipeline') {
    const failed = p.status === 'failed';
    const tools = Array.isArray(p.processing_tools) ? (p.processing_tools as unknown[]).join(', ') : str(p.processing_tools);
    const summary = (p.audit_summary ?? {}) as P;
    return {
      label: 'Warehouse pipeline',
      input: [str(p.source_table), tools].filter(Boolean).join(' · '),
      result: failed
        ? str(p.error_message) || 'failed'
        : `${str(p.rows_pulled)} pulled, ${str(p.rows_processed)} processed${p.dest_table ? `, ${str(p.rows_pushed)} pushed` : ''}`,
      failed,
      fields: (
        [
          ['Pipeline', str(p.pipeline_id)],
          ['Source table', str(p.source_table)],
          ['Tools run', tools],
          ['Rows pulled', str(p.rows_pulled)],
          ['Rows processed', str(p.rows_processed)],
          ['Processing errors', p.processing_errors ? str(p.processing_errors) : ''],
          ['Included', str(summary.included)],
          ['Excluded', str(summary.excluded)],
          ['Pushed to', str(p.dest_table)],
          ['Rows pushed', p.dest_table ? str(p.rows_pushed) : ''],
          ['Error', str(p.error_message)],
        ] as [string, string][]
      ).filter(([, v]) => v),
    };
  }
  return null;
}
