import { randomUUID } from 'node:crypto';
import type { APIRequestContext, Page, TestInfo } from '@playwright/test';
import { expect } from '../../lib/fixtures';
import { createOtlpFixture, sendOtlpExport, sendTrace, type OtlpAttributes } from '../../lib/otlp';
import { syntheticCopilotDetailsFixture } from '../../harness/native-otlp-fixtures';
import { E2E } from '../../lib/env';
import { POLL, type StateProbe } from '../../lib/state-probe';
import type { TestActor } from '../../lib/provisioning';

// Synthetic source contracts: collector profiles_data.go and Copilot testdata.
// No vendor process, credentials, local evidence directory or model provider.
export const ids = ['0000000000000001', '0000000000000002', '0000000000000003', '0000000000000004', '0000000000000005'];
// TraceGrid / tracer/serializers/trace.py and trace_session.py wire contracts.
export const LIST = '/tracer/trace/list_traces_of_session/';
export const SESSIONS = '/tracer/trace-session/list_sessions/';
export const UI_READY = 60_000;
export interface NativeCase {
  key: string;
  name: string;
  traceId: string;
  spanIds: string[];
  session: string;
  conversation?: string;
  total: number;
  cost: number | null;
  display: string;
  profile: string;
  parents: string[];
  kinds: string[];
  prompt: number[];
  completion: number[];
  input?: string;
}
export interface StoredSpan {
  trace_id: string;
  id: string;
  parent_span_id: string;
  name: string;
  observation_type: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost: number | null;
  trace_session_id: string;
  profile: string;
  contribution: string;
  cost_kind: string;
  source_prompt: number;
  source_completion: number;
  input: string;
  output: string;
  span_events: string;
  extra: string;
}
export interface NativeSession {
  trace_session_id: string;
  external_session_id: string;
}
// Optional serializer-owned capture shape, independent of numeric cost.
export interface CaptureCoverage {
  scope: 'observed_requests'; source_summary_present: boolean;
  request_count: number; coverage_unknown: boolean;
}
export interface TracePage {
  result: {
    table: { trace_id: string; total_tokens: number; cost: number | null }[];
    trace_capture_coverage?: Record<string, Record<string, CaptureCoverage>>;
    metadata: { total_rows: number; total_rows_is_lower_bound?: boolean };
  };
}
export interface SessionPage {
  result: {
    table: { session_id: string; total_tokens: number; total_cost: number | null; total_traces_count: number; capture_coverage?: CaptureCoverage }[];
  };
}
export interface SessionHistory {
  result: {
    response: {
      trace_id: string;
      system_metrics: { total_tokens: number; input_tokens: number; output_tokens: number; total_cost: number | null };
    }[];
    session_metadata: { session_id: string; total_tokens: number; total_traces: number; total_cost: number | null };
  };
}
interface DetailSpan {
  id: string;
  parent_span_id: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost: number | null;
  span_events: { attributes: Record<string, unknown> }[];
  span_attributes: { 'fi.otel.links': { span_id: string }[] };
}
interface DetailEntry {
  observation_span: DetailSpan;
  children: DetailEntry[];
}
export interface TraceDetail {
  result: {
    capture_coverage?: CaptureCoverage;
    summary: {
      total_tokens: number;
      total_cost: number | null;
      total_prompt_tokens: number;
      total_completion_tokens: number;
      span_type_counts: { llm: number };
    };
    observation_spans: DetailEntry[];
  };
}
export const walk = (entries: DetailEntry[]): DetailSpan[] =>
  entries.flatMap((e) => [e.observation_span, ...walk(e.children)]);
export interface NativeSeed {
  cases: NativeCase[];
  stored: StoredSpan[];
  sessions: NativeSession[];
  projectId: string;
}

export async function seedNative(
  request: APIRequestContext,
  actor: TestActor,
  probe: StateProbe,
  testInfo: TestInfo,
): Promise<NativeSeed> {
  const prefix = `e2e-native-${testInfo.testId}-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = prefix;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName };
  const baseTime = BigInt(Date.now() - 120_000) * 1_000_000n;
  const cases: NativeCase[] = [];
  for (const [index, key] of ['copilot-known-1', 'copilot-known-2', 'copilot-zero', 'copilot-unknown'].entries()) {
    const traceId = randomUUID(),
      name = `${prefix}-${key}`;
    const conversation = key.startsWith('copilot-known') ? `${prefix}-multi-turn` : `${prefix}-${key}-fallback`;
    const session = key === 'copilot-zero' ? `${prefix}-preserved-explicit` : conversation;
    const start = baseTime + BigInt(index) * 2_000_000_000n;
    const f = structuredClone(
      syntheticCopilotDetailsFixture({
        projectName,
        traceId: traceId.replaceAll('-', ''),
        conversationId: conversation,
        startTimeUnixNano: String(start),
        endTimeUnixNano: String(start + 1_000_000_000n),
      }),
    );
    const members = f.resourceSpans[0].scopeSpans[0].spans;
    members[0].name = name;
    for (const [j, s] of members.entries()) {
      const attributes = [...(s.attributes ?? [])];
      if (key === 'copilot-zero') attributes.push({ key: 'session.id', value: { stringValue: session } });
      attributes.push(
        { key: 'input.value', value: { stringValue: `${name}-input-${j}` } },
        { key: 'output.value', value: { stringValue: `${name}-output-${j}` } },
      );
      if (j >= 1 && j <= 3) {
        if (key === 'copilot-unknown' && j === 1)
          attributes.push({ key: 'gen_ai.usage.cache_read_tokens', value: { intValue: '10' } });
        else
          attributes.push({
            key: 'gen_ai.cost.total',
            value: { doubleValue: key === 'copilot-zero' ? 0 : [0, 0.1, 0.2, 0.3][j] },
          });
      }
      s.attributes = attributes;
    }
    const payload = createOtlpFixture(f);
    await sendOtlpExport(request, cfg, payload);
    if (index === 0) await sendOtlpExport(request, cfg, payload); // unchanged duplicate delivery
    cases.push({
      key,
      name,
      traceId,
      spanIds: ids,
      session,
      conversation,
      total: 43071,
      cost: key === 'copilot-zero' ? 0 : key === 'copilot-unknown' ? null : 0.6,
      display: key === 'copilot-zero' ? '$0.00' : key === 'copilot-unknown' ? '-' : '$0.60',
      profile: 'github-copilot-cli-1.0.98',
      parents: ['', ids[0], ids[0], ids[0], ids[0]],
      kinds: ['agent', 'llm', 'llm', 'llm', 'chain'],
      prompt: [0, 14095, 14370, 14460, 0],
      completion: [0, 61, 52, 33, 0],
      input: `${name}-input-0`,
    });
  }
  for (const [index, key] of ['gemini-known', 'gemini-unknown'].entries()) {
    const traceId = randomUUID(),
      name = `${prefix}-${key}`,
      session = `${prefix}-${key}-session`;
    const identity: OtlpAttributes = { 'gen_ai.conversation.id': `${session}-ignored`, 'session.id': session };
    const usage: OtlpAttributes = {
      ...identity,
      'gen_ai.operation.name': 'llm_call',
      'gen_ai.usage.input_tokens': 100,
      'gen_ai.usage.output_tokens': 20,
      'gen_ai.request.model': 'synthetic-gemini',
      'gen_ai.provider.name': 'google',
      ...(index === 0 ? { 'gen_ai.cost.total': 0.25 } : { 'gen_ai.usage.cache_read_tokens': 5 }),
      'gen_ai.input.messages': [{ role: 'user', content: `${name}-whole-message` }],
      'input.value': `${name}-input`,
      'output.value': `${name}-output`,
    };
    const start = baseTime + BigInt(index + 4) * 2_000_000_000n;
    const seeded = await sendTrace(request, {
      ...cfg,
      traceId,
      rootSpanId: ids[0],
      childSpanId: ids[1],
      scope: { name: 'gemini-cli', version: 'v1' },
      resourceAttributes: { project_type: 'observe' },
      startTimeUnixNano: start,
      endTimeUnixNano: start + 1_000_000_000n,
      rootName: name,
      childName: 'llm_call',
      rootAttributes: { ...identity, 'gen_ai.operation.name': 'agent_call' },
      childAttributes: usage,
      additionalSpans: [
        {
          spanId: ids[2],
          parentSpanId: ids[0],
          name: 'schedule_tool_calls',
          attributes: { ...identity, 'gen_ai.operation.name': 'schedule_tool_calls' },
        },
        {
          spanId: ids[3],
          parentSpanId: ids[2],
          name: 'tool_call',
          status: { code: 2, message: 'synthetic tool error' },
          events: [
            {
              name: 'exception',
              attributes: [
                { key: 'exception.type', value: { stringValue: 'SyntheticToolError' } },
                { key: 'exception.message', value: { stringValue: 'FORBIDDEN-SYNTHETIC-EVENT-TEXT' } },
              ],
            },
          ],
          attributes: {
            ...identity,
            'gen_ai.operation.name': 'tool_call',
            'tool.name': 'synthetic_read_file',
            'tool.call.id': `${prefix}-tool`,
            'input.value': JSON.stringify({ path: 'synthetic.txt' }),
            'output.value': `${name}-tool-result`,
          },
        },
        { spanId: ids[4], parentSpanId: ids[0], name: 'llm_call_second', attributes: usage },
      ],
    });
    cases.push({
      key,
      name,
      traceId: seeded.traceId,
      spanIds: seeded.spanIds,
      session,
      total: 240,
      cost: index === 0 ? 0.5 : null,
      display: index === 0 ? '$0.50' : '-',
      profile: 'gemini-cli-tracer-v1',
      parents: ['', ids[0], ids[0], ids[2], ids[0]],
      kinds: ['agent', 'llm', 'chain', 'tool', 'llm'],
      prompt: [0, 100, 0, 0, 100],
      completion: [0, 20, 0, 0, 20],
      input: `${name}-input`,
    });
  }
  const legacyName = `${prefix}-legacy-control`,
    legacySession = `${prefix}-legacy-session`;
  const legacy = await sendTrace(request, {
    ...cfg,
    rootName: legacyName,
    rootSpanId: ids[0],
    childSpanId: ids[1],
    resourceAttributes: { project_type: 'observe' },
    rootAttributes: { 'session.id': legacySession },
    childAttributes: {
      'session.id': legacySession,
      'gen_ai.usage.input_tokens': 7,
      'gen_ai.usage.output_tokens': 3,
      'gen_ai.cost.total': 0.05,
    },
  });
  cases.push({
    key: 'legacy',
    name: legacyName,
    ...legacy,
    session: legacySession,
    total: 10,
    cost: 0.05,
    display: '$0.05',
    profile: '',
    parents: ['', ids[0]],
    kinds: ['span', 'llm'],
    prompt: [0, 7],
    completion: [0, 3],
  });
  await testInfo.attach('synthetic-source-identities', {
    body: JSON.stringify(cases),
    contentType: 'application/json',
  });
  const projects = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
    [projectName, actor.organizationId, actor.workspaceId],
  );
  expect(projects).toHaveLength(1);
  const projectId = projects[0].id;
  const read = () =>
    probe.ch<StoredSpan>(
      `SELECT trace_id, id, parent_span_id, name, observation_type, prompt_tokens, completion_tokens, total_tokens, cost, trace_session_id,
      attrs_string['fi.normalization.profile'] AS profile, attrs_string['fi.usage.contribution'] AS contribution,
      attrs_string['fi.cost.kind'] AS cost_kind, attrs_number['fi.source.summary.gen_ai.usage.input_tokens'] AS source_prompt,
      attrs_number['fi.source.summary.gen_ai.usage.output_tokens'] AS source_completion,
      input, output, span_events, toString(attributes_extra) AS extra FROM spans FINAL WHERE project_id={p:UUID} ORDER BY trace_id,id`,
      { p: projectId },
    );
  let stored: StoredSpan[] = [];
  await expect
    .poll(async () => {
      stored = await read();
      return stored.map((s) => `${s.trace_id}:${s.id}`).sort();
    }, POLL.SPAN_VISIBLE)
    .toEqual(cases.flatMap((c) => c.spanIds.map((id: string) => `${c.traceId}:${id}`)).sort());
  let sessions: NativeSession[] = [];
  await expect
    .poll(async () => {
      sessions = await probe.ch<NativeSession>(
        'SELECT trace_session_id,external_session_id FROM trace_sessions FINAL WHERE project_id={p:UUID} AND is_deleted=0',
        { p: projectId },
      );
      return sessions.map((s) => s.external_session_id).sort();
    }, POLL.SPAN_VISIBLE)
    .toEqual([...new Set(cases.map((c) => c.session))].sort());
  await testInfo.attach('native-storage', {
    body: JSON.stringify({ projectId, stored, sessions }),
    contentType: 'application/json',
  });
  return { cases, stored, sessions, projectId };
}

export async function shot(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  await testInfo.attach(name, { body: await page.screenshot(), contentType: 'image/png' });
}

// AG Grid's unlicensed CI image emits these exact five lines. Keep all counts in
// the report; other console errors, page errors and HTTP failures are failures.
const licenseLines = new Set([
  '****************************************************************************************************************************',
  '************************************************ AG Grid Enterprise License ************************************************',
  '************************************************** License Key Not Found ***************************************************',
  '* All AG Grid Enterprise features are unlocked for trial.                                                                  *',
  '* If you want to hide the watermark please email info@ag-grid.com for a trial license key.                                 *',
]);
export function browserErrors(page: Page, testInfo: TestInfo): () => Promise<void> {
  const errors = {
    page: [] as string[],
    console: [] as string[],
    http: [] as { path: string; status: number }[],
    requests: [] as { path: string; error: string | null }[],
  };
  page.on('pageerror', (e) => errors.page.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.console.push(m.text().replace(/https?:\/\/\S+/g, '[url]'));
  });
  page.on('requestfailed', (r) =>
    errors.requests.push({ path: new URL(r.url()).pathname, error: r.failure()?.errorText ?? null }),
  );
  page.on('response', (r) => {
    if (r.status() >= 400) errors.http.push({ path: new URL(r.url()).pathname, status: r.status() });
  });
  return async () => {
    await testInfo.attach('browser-observations', { body: JSON.stringify(errors), contentType: 'application/json' });
    expect(errors.page).toEqual([]);
    expect(errors.console.filter((m) => !licenseLines.has(m))).toEqual([]);
    expect(errors.http).toEqual([]);
    // Browser navigation legitimately cancels obsolete in-flight requests.
    expect(errors.requests.filter((r) => r.error !== 'net::ERR_ABORTED')).toEqual([]);
  };
}
