import { randomUUID } from 'node:crypto';
import type { APIRequestContext, TestInfo } from '@playwright/test';
import { expect } from '../../lib/fixtures';
import { createOtlpFixture, sendOtlpExport, sendTrace } from '../../lib/otlp';
import { syntheticCopilotDetailsFixture } from '../../harness/native-otlp-fixtures';
import { E2E } from '../../lib/env';
import { POLL, type StateProbe } from '../../lib/state-probe';
import type { TestActor } from '../../lib/provisioning';
import { ids } from './native-source';

// Approved OBS-E2E-054 lifecycle. Reuse the maintained synthetic OTLP seeder;
// no vendor process, fake SQL results, new endpoint, harness or infrastructure.
export async function* nativeCaptureStages(
  request: APIRequestContext, actor: TestActor, probe: StateProbe, testInfo: TestInfo,
) {
  const projectName = `e2e-capture-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const traceId = randomUUID(), name = `${projectName}-turn`, session = `${projectName}-session`;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName };
  const start = BigInt(Date.now() - 30_000) * 1_000_000n;
  const full = structuredClone(syntheticCopilotDetailsFixture({
    projectName, traceId: traceId.replaceAll('-', ''), conversationId: session,
    startTimeUnixNano: String(start), endTimeUnixNano: String(start + 1_000_000_000n),
  }));
  const members = full.resourceSpans[0].scopeSpans[0].spans;
  members[0].name = name;
  // A source-only cost is deliberately present yet remains NONADDITIVE.
  members[0].attributes = [...(members[0].attributes ?? []),
    { key: 'gen_ai.cost.total', value: { doubleValue: 17 } }];
  members[1].attributes = [...(members[1].attributes ?? []),
    { key: 'gen_ai.usage.cache_read_tokens', value: { intValue: '10' } }];
  for (const index of [2, 3]) members[index].attributes = [...(members[index].attributes ?? []),
    { key: 'gen_ai.cost.total', value: { doubleValue: index === 2 ? 0.2 : 0.3 } }];
  const rootOnly = structuredClone(full);
  rootOnly.resourceSpans[0].scopeSpans[0].spans = [members[0]];
  const children = structuredClone(full);
  children.resourceSpans[0].scopeSpans[0].spans = members.slice(1);
  const legacy = await sendTrace(request, {
    ...cfg, rootName: `${projectName}-legacy`, resourceAttributes: { project_type: 'observe' },
    rootAttributes: { 'session.id': `${projectName}-legacy-session` },
    childAttributes: { 'session.id': `${projectName}-legacy-session`,
      'gen_ai.usage.input_tokens': 7, 'gen_ai.usage.output_tokens': 3, 'gen_ai.cost.total': 0.05 },
  });
  const projects = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
    [projectName, actor.organizationId, actor.workspaceId],
  );
  expect(projects).toHaveLength(1);
  const projectId = projects[0].id;
  await testInfo.attach('capture-lifecycle-identities', {
    body: JSON.stringify({ projectId, traceId, name, spanIds: ids, session, legacy }), contentType: 'application/json',
  });
  let previousRootVersion = 0n;
  for (const [stage, payload, expectedIds, count, tokens, cost] of [
    ['root-only', rootOnly, [ids[0]], 0, 0, 0],
    ['children-later', children, ids, 3, 43071, null],
    ['replay', full, ids, 3, 43071, null],
  ] as const) {
    await sendOtlpExport(request, cfg, createOtlpFixture(payload));
    type Row = { id: string; version: string; source_present: number; source_prompt: number;
      source_completion: number; source_cost: number; total_tokens: number; contribution: string };
    let rows: Row[] = [];
    await expect.poll(async () => {
      rows = await probe.ch<Row>(
        `SELECT id, toString(_version) AS version, total_tokens,
         mapContains(attrs_number,'fi.source.summary.gen_ai.usage.input_tokens') AS source_present,
         attrs_number['fi.source.summary.gen_ai.usage.input_tokens'] AS source_prompt,
         attrs_number['fi.source.summary.gen_ai.usage.output_tokens'] AS source_completion,
         attrs_number['fi.source.summary.gen_ai.cost.total'] AS source_cost,
         attrs_string['fi.usage.contribution'] AS contribution
         FROM spans FINAL WHERE project_id={p:UUID} AND trace_id={t:String} AND is_deleted=0 ORDER BY id`,
        { p: projectId, t: traceId },
      );
      return { ids: rows.map((r) => r.id), replayed: stage !== 'replay' ||
        BigInt(rows.find((r) => r.id === ids[0])?.version ?? '0') > previousRootVersion };
    }, POLL.SPAN_VISIBLE).toEqual({ ids: [...expectedIds], replayed: true });
    const root = rows[0];
    expect([root.source_present, root.source_prompt, root.source_completion, root.source_cost,
      root.total_tokens, root.contribution]).toEqual([1, 42925, 146, 17, 0, 'none']);
    previousRootVersion = BigInt(root.version);
    let sessionId = '';
    await expect.poll(async () => {
      const sessions = await probe.ch<{ trace_session_id: string }>(
        'SELECT trace_session_id FROM trace_sessions FINAL WHERE project_id={p:UUID} AND external_session_id={s:String} AND is_deleted=0',
        { p: projectId, s: session },
      );
      sessionId = sessions[0]?.trace_session_id ?? '';
      return sessions.length;
    }, POLL.SPAN_VISIBLE).toBe(1);
    await testInfo.attach(`capture-storage-${stage}`, { body: JSON.stringify(rows), contentType: 'application/json' });
    yield { stage, projectId, traceId, sessionId, count, tokens, cost, legacyTraceId: legacy.traceId };
  }
}
