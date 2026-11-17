import { randomUUID } from 'node:crypto';
import { request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';

// Pinned source: ExperimentRunTracesPanel.jsx; experiment-run-traces.js;
// experiment_run_api.py / experiment_run_traces.py. External runs do not execute a model.
const RUNS = '/model-hub/experiment-runs/';
const headerPath = (id: string) => `${RUNS}${id}/`;
const tracesPath = (id: string) => `${RUNS}${id}/traces/`;
const UI_READY = 60_000;
type Header = { id: string; label: string; dataset_version_id: string; status: string;
  experiment_id: string | null; task_kind: string; metadata: { name: string }; plan_size: number };
type Trace = { trace_id: string; root_span_id: string; example_id: string; trial_number: number;
  item_status: string; trace_run_id: string; root_span_name: string; span_count: number; project_id: string };
type TracePage = { run_id: string; traces: Trace[]; not_joined: unknown[]; items_with_trace: number;
  limit: number; offset: number; has_more: boolean };

test('@DSX-WALK-SRC-01: Open and page a recorded SDK run without exposing another organization’s traces', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-SRC-01', area: 'dsx-walks',
    userGoal: 'Open a dataset SDK run and inspect exactly its recorded traces in the owning organization',
    steps: ['seed two organizations with dataset versions and recorded SDK runs through served APIs',
      'send synthetic run-tagged spans to the rig collector',
      'open the dataset run link, page forward and back, and reload',
      'open a foreign run link as the second organization and see the load error'],
    backendChecks: ['run headers retain the exact dataset version, name, plan size and completed status',
      'both trace pages match the recorded example, trace, root, run, status and span count',
      'foreign header, trace and result-write requests return 404; owning reads remain unchanged'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(900_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  expect(new URL(E2E.collectorUrl).hostname).toMatch(/^(localhost|127\.0\.0\.1)$/);
  const collector = await request.newContext();
  const seeded: { dataset: string; version: string; run: string; name: string; label: string;
    expected: Omit<Trace, 'project_id'>[] }[] = [];
  try {
    for (const [actor, count, label] of [[scopes.ownerA, 51, 'a'], [scopes.ownerB, 1, 'b']] as const) {
      const name = `dsx-sdk-${label}-${suffix}`;
      // ManuallyCreateDataset.submit's served request shape.
      const created = await scopes.send<{ result: { dataset_id: string } }>(actor, 'POST',
        '/model-hub/develops/create-dataset-manually/', {
          dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: count, number_of_columns: 1,
        });
      expect(created.status).toBe(200);
      const dataset = created.body.result.dataset_id;
      const table = await actor.api.get<{ result: { table: { row_id: string }[] } }>(
        `/model-hub/develops/${dataset}/get-dataset-table/`, { page_size: 100, current_page_index: 0 });
      expect(table.result.table).toHaveLength(count);
      const examples = table.result.table.map(row => ({ row: row.row_id, example: randomUUID() }));
      const version = await scopes.send<{ result: { id: string } }>(actor, 'POST',
        `/model-hub/datasets/${dataset}/versions/`, { description: name,
          items: examples.map(({ row, example }) => ({ example_id: example, legacy_row_id: row,
            input: { question: `Synthetic question ${example}` }, expected: { answer: 'synthetic' } })) });
      expect(version.status).toBe(201);
      const run = randomUUID();
      const made = await scopes.send<{ result: Header }>(actor, 'POST', RUNS, {
        run_id: run, dataset_version_id: version.body.result.id,
        expected_items: examples.map(({ example }) => ({ example_id: example, trial_number: 1 })),
        evaluator_metric_ids: [], metadata: { name },
      });
      expect(made.status).toBe(200);
      expect(made.body.result.id).toBe(run);
      const owner = scopes.owners.find(candidate => candidate.organizationId === actor.organizationId)!;
      const expected: Omit<Trace, 'project_id'>[] = [];
      for (const { example } of examples) {
        const root = `${name}-root-${example}`;
        const trace = await sendTrace(collector, {
          collectorUrl: E2E.collectorUrl, apiKey: owner.apiKey, secretKey: owner.secretKey,
          projectName: name, resourceAttributes: { project_type: 'observe' }, rootName: root,
          rootAttributes: { 'fi.experiment.run_id': run, 'input.value': 'Synthetic input',
            'output.value': 'Synthetic output' },
        });
        expected.push({ trace_id: trace.traceId, root_span_id: trace.spanIds[0], example_id: example,
          trial_number: 1, item_status: 'completed', trace_run_id: run, root_span_name: root, span_count: 2 });
      }
      const recorded = await scopes.send(actor, 'POST', `${headerPath(run)}results/`, {
        items: expected.map(row => ({ example_id: row.example_id, trial_number: 1,
          receipt_key: `${name}-${row.example_id}`, status: 'completed', output: 'Synthetic output',
          trace_id: row.trace_id, root_span_id: row.root_span_id, score_refs: [] })),
      });
      expect(recorded.status).toBe(200);
      expected.sort((left, right) => left.example_id.localeCompare(right.example_id));
      seeded.push({ dataset, version: version.body.result.id, run, name, label: made.body.result.label, expected });
      await testInfo.attach(`seeded-sdk-${label}`, { contentType: 'application/json', body: JSON.stringify({
        organizationId: actor.organizationId, workspaceId: actor.workspaceId, ...seeded[seeded.length - 1],
      }) });
      const header = await actor.api.get<{ result: Header }>(headerPath(run));
      expect(header.result).toMatchObject({ id: run, dataset_version_id: version.body.result.id,
        experiment_id: null, task_kind: 'external', status: 'completed', metadata: { name }, plan_size: count });
      for (const offset of count > 50 ? [0, 50] : [0]) {
        await expect.poll(async () => {
          const page = await actor.api.get<{ result: TracePage }>(tracesPath(run), { limit: 50, offset });
          return page.result.traces.map(({ project_id: _project, ...row }) => ({
            trace_id: row.trace_id, root_span_id: row.root_span_id, example_id: row.example_id,
            trial_number: row.trial_number, item_status: row.item_status, trace_run_id: row.trace_run_id,
            root_span_name: row.root_span_name, span_count: row.span_count,
          }));
        }, POLL.SPAN_VISIBLE).toEqual(expected.slice(offset, offset + 50));
        const page = (await actor.api.get<{ result: TracePage }>(tracesPath(run), { limit: 50, offset })).result;
        expect(page).toMatchObject({ run_id: run, not_joined: [], items_with_trace: count,
          limit: 50, offset, has_more: offset + 50 < count });
      }
    }
  } finally { await collector.dispose(); }

  for (const [actor, own, foreign] of [[scopes.ownerA, seeded[0], seeded[1]],
    [scopes.ownerB, seeded[1], seeded[0]]] as const) {
    const context = await scopes.openContext(browser, actor);
    await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
      ? route.continue() : route.abort('blockedbyclient'));
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(UI_READY);
      const url = `/dashboard/develop/${own.dataset}?tab=experiments&run=${own.run}`;
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      const panel = page.getByTestId('experiment-run-traces-panel');
      await expect(panel).toHaveAttribute('data-run-id', own.run);
      await expect(panel.getByText(`SDK run ${own.label} ${own.name}`, { exact: true })).toBeVisible();
      await expect(panel.getByTestId('experiment-run-traces-count')).toHaveText(`${own.expected.length} items with a trace`);
      const rows = panel.getByTestId('experiment-run-trace-row');
      await expect(rows).toHaveCount(Math.min(50, own.expected.length));
      await expect(rows.first()).toHaveAttribute('data-trace-id', own.expected[0].trace_id);
      await expect(rows.first()).toHaveAttribute('data-trace-run-id', own.run);
      await expect(rows.first().getByRole('cell').nth(3)).toHaveText(own.expected[0].root_span_name);
      if (own.expected.length > 50) {
        await panel.getByRole('button', { name: 'Next', exact: true }).click();
        await expect(rows).toHaveCount(1);
        await expect(rows.first()).toHaveAttribute('data-trace-id', own.expected[50].trace_id);
        await expect(panel.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
        await panel.getByRole('button', { name: 'Previous', exact: true }).click();
        await expect(rows).toHaveCount(50);
      }
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(rows.first()).toHaveAttribute('data-trace-id', own.expected[0].trace_id);
      for (const path of [headerPath(foreign.run), tracesPath(foreign.run)])
        expect((await scopes.send(actor, 'GET', path)).status).toBe(404);
      expect((await scopes.send(actor, 'POST', `${headerPath(foreign.run)}results/`, {
        items: [{ example_id: foreign.expected[0].example_id, trial_number: 1,
          receipt_key: `foreign-${suffix}`, status: 'completed', output: 'must not persist' }],
      })).status).toBe(404);
      await page.goto(`/dashboard/develop/${own.dataset}?tab=experiments&run=${foreign.run}`);
      await expect(panel.getByTestId('experiment-run-traces-error')).toHaveText(
        'This run or its traces could not be loaded. It may be in another workspace, or the trace store may be unavailable; reload to retry.');
      await expect(panel.getByTestId('experiment-run-trace-row')).toHaveCount(0);
      const readback = await actor.api.get<{ result: Header }>(headerPath(own.run));
      expect(readback.result).toMatchObject({ id: own.run, dataset_version_id: own.version,
        status: 'completed', metadata: { name: own.name }, plan_size: own.expected.length });
    } finally { await context.close(); }
  }
});
