import type { StateProbe } from '../../../lib/state-probe';
import { requireUuid } from './identifiers';

type Probe = Pick<StateProbe, 'pg' | 'ch'>;

/** Ownership is PG tracer_project (e2e/flows/observe/trace-ingestion.spec.ts).
 * The trace tables partition by project, not org. Resolve ownership explicitly
 * instead of querying another org by a known trace ID. Poll project creation
 * separately; a missing/foreign project is an error, never an empty passing read.
 */
async function ownedProject(probe: Probe, organizationId: string, projectId: string): Promise<string> {
  requireUuid(organizationId);
  requireUuid(projectId);
  const rows = await probe.pg<{ id: string }>(`SELECT id::text FROM tracer_project
    WHERE id = $1 AND organization_id = $2 AND deleted = false`, [projectId, organizationId]);
  if (rows.length !== 1) throw new Error('Trace project is missing or not owned by the fixture org');
  return projectId;
}

export interface SpanRecord {
  span_id: string; trace_id: string; project_id: string; parent_span_id: string;
  name: string; status: string; model: string; provider: string;
  prompt_tokens: number; completion_tokens: number; total_tokens: number;
  input_length: number; output_length: number;
}

/** Provenance: tracer/services/clickhouse/v2/schema/002_spans_v2.sql.
 * ReplacingMergeTree(_version,is_deleted), id (NOT span_id), trace_id String.
 * FINAL is required; explicitly exclude tombstones. Read the entire minted
 * trace, not just its expected span IDs, so extra/misattributed spans fail sets.
 * Never read input/output, typed attribute values or object-storage URLs.
 */
export async function readTraceSpans(
  probe: Probe, organizationId: string, projectId: string, traceId: string,
): Promise<SpanRecord[]> {
  const project = await ownedProject(probe, organizationId, projectId);
  return probe.ch<SpanRecord>(`SELECT id AS span_id, trace_id, project_id,
    parent_span_id, name, status, model, provider, prompt_tokens, completion_tokens,
    total_tokens, input_length, output_length FROM spans FINAL
    WHERE project_id = {project:UUID} AND trace_id = {trace:String} AND is_deleted = 0
    ORDER BY id, start_time`, { project, trace: requireUuid(traceId) });
}

export interface TraceRecord { id: string; project_id: string; name: string | null }

/** Provenance: schema/015_traces_and_trace_dict.sql and
 * fi-collector/pkg/curatedwriter/curatedwriter.go:traceRow. UUID id, NO trace_id.
 * Curated write is separate from span insertion; poll independently with
 * POLL.SPAN_VISIBLE, never assume span visibility implies curated visibility.
 */
export async function readCuratedTrace(
  probe: Probe, organizationId: string, projectId: string, traceId: string,
): Promise<TraceRecord[]> {
  const project = await ownedProject(probe, organizationId, projectId);
  return probe.ch<TraceRecord>(`SELECT id, project_id, name
    FROM traces FINAL WHERE project_id = {project:UUID} AND id = {trace:UUID}
    AND is_deleted = 0 ORDER BY id`, { project, trace: requireUuid(traceId) });
}
