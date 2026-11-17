import { expect, request, type TestInfo } from '@playwright/test';
import type { ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { sendTrace } from '../../lib/otlp';

// tracer/views/dataset.py::add_to_new_dataset and the accepted source-context adapter.
const IMPORT = '/tracer/dataset/add_to_new_dataset/';
const FIELDS = ['name', 'input', 'output'] as const;
const MAPPING = FIELDS.map(name => ({ col_name: name, span_field: name, data_type: 'text' }));
type Column = { id: string; name: string };
type Table = { result: { column_config: Column[]; table: { row_id: string }[] } };
export type ImportedSource = {
  type: 'trace'; label: string; project_id: string; trace_id: string; span_id: string; expired: false;
};
export type ImportedRow = {
  source: ImportedSource;
  cells: Record<string, string>;
};

// owed-733-rerun: credentials only through the existing harness Actor/collector
// abstraction. No key creation/listing/type checks or direct ClickHouse writes.
export async function importApiRows(scopes: ScopeActors, probe: StateProbe, info: TestInfo, name: string) {
  const a = scopes.ownerA;
  const actor = scopes.owners.find(owner => owner.organizationId === a.organizationId)!;
  const roots: { id: string; trace: string; name: string; input: string; output: string }[] = [];
  const collector = await request.newContext();
  try {
    for (let i = 0; i < 2; i++) {
      const expected = { name: `${name}-root-${i}`, input: `${name}-question-${i}`, output: `${name}-answer-${i}` };
      const trace = await sendTrace(collector, {
        collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: name, resourceAttributes: { project_type: 'observe' },
        rootName: expected.name, childName: `${name}-child-${i}`,
        rootAttributes: { 'input.value': expected.input, 'output.value': expected.output },
      });
      await expect.poll(async () => probe.ch<{ id: string; name: string; input: string; output: string }>(
        `SELECT id, name, input, output FROM spans FINAL
         WHERE trace_id={trace:String} AND id={span:String}`,
        { trace: trace.traceId, span: trace.spanIds[0] }), POLL.SPAN_VISIBLE)
        .toEqual([{ id: trace.spanIds[0], ...expected }]);
      roots.push({ id: trace.spanIds[0], trace: trace.traceId, ...expected });
    }
  } finally { await collector.dispose(); }

  const projects = await probe.pg<{ id: string }>(
    'SELECT id::text AS id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
    [name, a.organizationId, a.workspaceId]);
  expect(projects).toHaveLength(1);
  const project = projects[0].id;
  const dataset = (await a.api.post<{ result: { dataset_id: string } }>(IMPORT, {
    new_dataset_name: `${name}-imported`, project, source_kind: 'trace',
    trace_ids: roots.map(root => root.trace), mapping_config: MAPPING,
  })).result.dataset_id;
  // Import is a worker job; use ASYNC_JOB rather than a CDC budget for direct PG.
  await expect.poll(async () => (await probe.pg<{ n: string }>(
    'SELECT count(*)::text AS n FROM model_hub_row WHERE dataset_id=$1::uuid AND NOT deleted', [dataset]))[0].n,
  POLL.ASYNC_JOB).toBe('2');
  const table = (await a.api.get<Table>(`/model-hub/develops/${dataset}/get-dataset-table/`)).result;
  const column = table.column_config.find(c => c.name === 'name')!;
  expect(column).toBeDefined();
  const persisted = await probe.pg<{ row_id: string; value: string }>(
    `SELECT r.id::text AS row_id, c.value FROM model_hub_row r
     JOIN model_hub_cell c ON c.row_id=r.id AND c.column_id=$2::uuid AND NOT c.deleted
     WHERE r.dataset_id=$1::uuid AND NOT r.deleted`, [dataset, column.id]);
  expect(persisted.map(row => row.value).sort()).toEqual(roots.map(root => root.name).sort());
  const rowIds = roots.map(root => persisted.find(row => row.value === root.name)!.row_id);
  expect(table.table.map(row => row.row_id).sort()).toEqual([...rowIds].sort());
  const expectedRows: ImportedRow[] = roots.map(root => ({
    source: { type: 'trace', label: `Trace ${root.trace.slice(0, 8)}`, project_id: project,
      trace_id: root.trace, span_id: root.id, expired: false },
    cells: Object.fromEntries(FIELDS.map(field => {
      const col = table.column_config.find(c => c.name === field);
      expect(col).toBeDefined();
      return [col!.id, root[field]];
    })),
  }));
  await info.attach('row-api-import-identities', { contentType: 'application/json', body: JSON.stringify({
    dataset, rowIds, project, roots, ingestion: 'collector', marker: 'owed-733-rerun',
    organizationId: a.organizationId, workspaceId: a.workspaceId,
  }) });
  return { dataset, column, rowIds, values: roots.map(root => root.name), expectedRows };
}
