import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test, expect } from '../../lib/fixtures';
import { createOtlpFixture, sendOtlpExport, type OtlpExport, type OtlpSpan } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// Sanitized native VS Code 1.143.1 payload, shared with Go/Python normalization tests.
const CAPTURE = new URL('../../../futureagi/tracer/utils/normalization/vscode-copilot-1.0.100-1.json', import.meta.url);
// TraceDetailDrawerV2 and TraceGrid consume these endpoints and tree envelope.
const TRACE_LIST = '/tracer/trace/list_traces_of_session/';
const UI_READY = 60_000;
interface DetailSpan { id: string; name: string; observation_type: string; total_tokens: number }
interface Entry { observation_span: DetailSpan; children: Entry[] }
interface Detail { result: { summary: { total_tokens: number }; observation_spans: Entry[] } }
const flatten = (entries: Entry[]): DetailSpan[] => entries.flatMap(e => [e.observation_span, ...flatten(e.children)]);

test('OBS-E2E-059: Copilot agent traces show request usage and optional timing detail', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-059', area: 'observe',
    userGoal: 'A developer inspects a VS Code Copilot turn with accurate token usage, an Agent label and optional internal timing detail',
    steps: [
      'send the sanitized native VS Code capture plus Strands, Pydantic and parent-only controls',
      'open the captured trace from the real Observe grid',
      'read the agent label and observed token summary with timing activity hidden',
      'show timing spans and hide them again without losing their stored data',
    ],
    backendChecks: [
      'all 91 captured spans retain their IDs; one agent, five model calls and 72 internal timing spans are stored',
      'trace detail and trace list report 88073 captured tokens, 193 Strands tokens, 193 Pydantic tokens and 193 parent-only tokens',
      'all 72 timing span IDs remain in the detail API while the tree hides them by default and shows their exact names on request',
    ],
  }),
}, async ({ page, request, actor, probe }, testInfo) => {
  // 2 SPAN_VISIBLE polls (30s) + four UI_READY chains (240s) + 90s I/O headroom.
  test.setTimeout(360_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-copilot-${suffix}`;
  const traceId = randomUUID();
  const native = JSON.parse(readFileSync(CAPTURE, 'utf8')) as OtlpExport;
  const nativeSpans = native.resourceSpans.flatMap(r => r.scopeSpans.flatMap(s => s.spans));
  const minStart = nativeSpans.reduce((a, s) => BigInt(s.startTimeUnixNano) < a ? BigInt(s.startTimeUnixNano) : a, BigInt(nativeSpans[0].startTimeUnixNano));
  const delta = BigInt(Date.now() - 120_000) * 1_000_000n - minStart;
  const capture = createOtlpFixture({ resourceSpans: native.resourceSpans.map(r => ({
    resource: { attributes: [
      { key: 'project_name', value: { stringValue: projectName } },
      { key: 'project_type', value: { stringValue: 'observe' } },
    ] },
    scopeSpans: r.scopeSpans.map(scope => ({ scope: scope.scope, spans: scope.spans.map(s => ({
      ...s, traceId: traceId.replaceAll('-', ''),
      startTimeUnixNano: String(BigInt(s.startTimeUnixNano) + delta),
      endTimeUnixNano: String(BigInt(s.endTimeUnixNano) + delta),
      attributes: [...(s.attributes ?? []),
        { key: 'session.id', value: { stringValue: `${projectName}-session` } },
      ],
    })) })),
  })) });
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };
  const seeded = await sendOtlpExport(request, cfg, capture);
  const cases = [{ key: 'capture', traceId, tokens: 88073 }];
  // Pydantic is an immediate-child baseline; Strands nests chat beneath an event loop.
  // Parent-only is intentionally unprofiled and must remain additive.
  for (const key of ['strands', 'pydantic', 'parent-only']) {
    const id = randomUUID();
    const start = String(BigInt(Date.now() - 30_000) * 1_000_000n);
    const end = String(BigInt(start) + 1_000_000_000n);
    const usage = [
      { key: 'gen_ai.usage.input_tokens', value: { intValue: '190' } },
      { key: 'gen_ai.usage.output_tokens', value: { intValue: '3' } },
      { key: 'gen_ai.cost.total', value: { doubleValue: 0 } },
    ];
    const rootId = '0000000000000001';
    const loopId = '0000000000000002';
    const spans: OtlpSpan[] = [{
      traceId: id.replaceAll('-', ''), spanId: rootId,
      name: key === 'pydantic' ? 'pydantic_ai.agent.run' : `${projectName}-${key}`,
      startTimeUnixNano: start, endTimeUnixNano: end,
      attributes: [...usage, { key: 'fi.span.kind', value: { stringValue: 'AGENT' } },
        ...(key === 'pydantic' ? [] : [{ key: 'gen_ai.operation.name', value: { stringValue: 'invoke_agent' } }]),
      ],
    }];
    if (key === 'strands') spans.push({
      traceId: id.replaceAll('-', ''), spanId: loopId, parentSpanId: rootId, name: 'event_loop',
      startTimeUnixNano: start, endTimeUnixNano: end,
      attributes: [{ key: 'fi.span.kind', value: { stringValue: 'CHAIN' } }],
    });
    if (key !== 'parent-only') spans.push({
      traceId: id.replaceAll('-', ''), spanId: '0000000000000003',
      parentSpanId: key === 'strands' ? loopId : rootId, name: 'chat',
      startTimeUnixNano: start, endTimeUnixNano: end,
      attributes: [...usage, { key: 'gen_ai.operation.name', value: { stringValue: 'chat' } }],
    });
    await sendOtlpExport(request, cfg, createOtlpFixture({ resourceSpans: [{
      resource: { attributes: [
        { key: 'project_name', value: { stringValue: projectName } },
        { key: 'project_type', value: { stringValue: 'observe' } },
      ] }, scopeSpans: [{ scope: { name: 'e2e-genai-controls' }, spans }],
    }] }));
    cases.push({ key, traceId: id, tokens: 193 });
  }
  await testInfo.attach('seeded-identities', { body: JSON.stringify({ projectName, seeded, cases }), contentType: 'application/json' });
  const timingIds = nativeSpans.filter(s => s.name.startsWith('session.timing.')).map(s => s.spanId).sort();
  const timingNames = nativeSpans.filter(s => s.name.startsWith('session.timing.')).map(s => s.name).sort();
  expect(timingIds).toHaveLength(72);
  await expect.poll(async () => {
    const rows = await probe.ch<{ id: string; observation_type: string }>(
      'SELECT id, observation_type FROM spans FINAL WHERE trace_id={t:String} ORDER BY id', { t: traceId });
    return {
      ids: rows.map(r => r.id),
      agent: rows.filter(r => r.observation_type === 'agent').length,
      llm: rows.filter(r => r.observation_type === 'llm').length,
      internal: rows.filter(r => r.observation_type === 'internal').map(r => r.id),
    };
  }, POLL.SPAN_VISIBLE).toEqual({ ids: nativeSpans.map(s => s.spanId).sort(), agent: 1, llm: 5, internal: timingIds });
  const projects = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
    [projectName, actor.organizationId, actor.workspaceId]);
  expect(projects).toHaveLength(1);
  const projectId = projects[0].id;
  await expect.poll(async () => {
    const result = await actor.api.get<{ result: { table: { trace_id: string; total_tokens: number }[] } }>(TRACE_LIST, {
      project_id: projectId, page_number: 0, page_size: 25, filters: '[]',
    });
    return result.result.table.map(r => [r.trace_id, r.total_tokens]).sort();
  }, POLL.SPAN_VISIBLE).toEqual(cases.map(c => [c.traceId, c.tokens]).sort());
  for (const c of cases) {
    const detail = await actor.api.get<Detail>(`/tracer/trace/${c.traceId}/`, { project_id: projectId });
    expect(detail.result.summary.total_tokens).toBe(c.tokens);
    if (c.key === 'capture') expect(flatten(detail.result.observation_spans)
      .filter(s => s.observation_type === 'internal').map(s => s.id).sort()).toEqual(timingIds);
    await testInfo.attach(`detail-${c.key}`, { body: JSON.stringify(detail.result), contentType: 'application/json' });
  }
  await test.step('open captured turn and inspect Agent and observed total', async () => {
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces`, { waitUntil: 'domcontentloaded' });
    await page.locator(`.clean-data-table:visible .ag-row[row-id="${traceId}"] [col-id="trace_name"]`).click();
    const drawer = page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByText(traceId, { exact: true }) });
    await drawer.getByRole('region', { name: 'Trace Tree', exact: true }).getByText('invoke_agent', { exact: true }).click();
    await expect(drawer.getByText('Type : agent', { exact: true })).toBeVisible({ timeout: UI_READY });
    await drawer.getByRole('region', { name: 'Trace Tree', exact: true }).getByText('invoke_agent', { exact: true }).click();
    // formatters.js abbreviates totals >= 10000; detail API above pins the exact integer.
    await expect(drawer.getByText('88.1K observed tokens', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(drawer.getByRole('button', { name: 'Show 72 timing spans', exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(drawer.getByRole('region', { name: 'Trace Tree', exact: true }).getByText(/^session\.timing\./)).toHaveCount(0, { timeout: UI_READY });
  }, { timeout: UI_READY });
  await test.step('show exact timing names then hide without deleting API data', async () => {
    const drawer = page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByText(traceId, { exact: true }) });
    await drawer.getByRole('button', { name: 'Show 72 timing spans', exact: true }).click();
    await expect(drawer.getByRole('region', { name: 'Trace Tree', exact: true }).getByText(/^session\.timing\./)).toHaveCount(72, { timeout: UI_READY });
    expect((await drawer.getByRole('region', { name: 'Trace Tree', exact: true }).getByText(/^session\.timing\./).allTextContents()).sort()).toEqual(timingNames);
    await testInfo.attach('timing-shown', { body: await page.screenshot(), contentType: 'image/png' });
    await drawer.getByRole('button', { name: 'Hide 72 timing spans', exact: true }).click();
    await expect(drawer.getByRole('region', { name: 'Trace Tree', exact: true }).getByText(/^session\.timing\./)).toHaveCount(0, { timeout: UI_READY });
    const detail = await actor.api.get<Detail>(`/tracer/trace/${traceId}/`, { project_id: projectId });
    expect(flatten(detail.result.observation_spans).filter(s => s.observation_type === 'internal').map(s => s.id).sort()).toEqual(timingIds);
    await testInfo.attach('timing-hidden', { body: await page.screenshot(), contentType: 'image/png' });
  }, { timeout: UI_READY });
});
