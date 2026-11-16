import { randomUUID } from 'node:crypto';
import { test, expect } from '../lib/fixtures';
import { sendTrace, type SeededTrace, type SendTraceConfig } from '../lib/otlp';
import { POLL } from '../lib/state-probe';
import { E2E } from '../lib/env';

// Collector contract: exporter/clickhouse25exporter/converter.go resolves
// fi.span.kind, stores UUID trace IDs / hex span IDs, and derives per-span
// user/session identities. Its user gate requires resource project_type=observe.
// Storage: tracer/services/clickhouse/v2/schema/002_spans_v2.sql (microseconds).
interface ShapeRow {
  id: string; parent_span_id: string; name: string; observation_type: string;
  org_id: string; project_id: string; start_us: string; end_us: string;
  attrs_string: Record<string, string>; attrs_number: Record<string, number>;
  attrs_bool: Record<string, number>; extra: string; version: string;
}

test('OTLP preserves typed root/child attributes, exact timestamps and stable replay IDs',
  async ({ request, actor, probe }, testInfo) => {
    const projectName = `e2e-otlp-shape-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Two days old, with a non-millisecond component; still within source retention.
    const start = BigInt(Date.now() - 2 * 86_400_000) * 1_000_000n + 123_000n;
    const cfg: SendTraceConfig = {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
      traceId: randomUUID().replaceAll('-', '').toUpperCase(),
      rootSpanId: 'ABCDEF0000000001', childSpanId: 'ABCDEF0000000002',
      startTimeUnixNano: start, endTimeUnixNano: String(start + 75_000_000n),
      rootName: `${projectName}.voice`, childName: `${projectName}.tool`,
      resourceAttributes: { project_type: 'observe' },
      rootAttributes: {
        'fi.span.kind': 'CONVERSATION', 'call.status': 'completed',
        'fixture.string': '001', 'fixture.number': 0, 'fixture.bool': false,
        'fixture.array': ['001', 0, false, { nested: ['x', 1, true] }, [], {}],
        'fixture.object': { 'part.dot': { 'slash/key': '雪' }, enabled: true },
      },
      childAttributes: {
        'fi.span.kind': 'tool', 'fixture.string': '002', 'fixture.number': 2.5,
        'fixture.bool': true, 'fixture.array': [true, 2, 'child'],
        'fixture.object': { child: { values: [false, '03'] } },
      },
    };
    const seeded = await sendTrace(request, cfg);
    await testInfo.attach('seeded-trace', { contentType: 'application/json',
      body: JSON.stringify({ ...seeded, organizationId: actor.organizationId,
        startTimeUnixNano: String(start), endTimeUnixNano: cfg.endTimeUnixNano }) });
    const read = () => probe.ch<ShapeRow>(`
      SELECT id, parent_span_id, name, observation_type, org_id, project_id,
        toUnixTimestamp64Micro(start_time) AS start_us,
        toUnixTimestamp64Micro(end_time) AS end_us,
        attrs_string, attrs_number, attrs_bool, toString(attributes_extra) AS extra,
        toString(_version) AS version
      FROM spans FINAL WHERE trace_id = {t:String} ORDER BY id`, { t: seeded.traceId });
    await expect.poll(async () => (await read()).map(row => row.id), POLL.SPAN_VISIBLE)
      .toEqual(['abcdef0000000001', 'abcdef0000000002']);
    const rows = await read();
    await testInfo.attach('source-spans', { body: JSON.stringify(rows), contentType: 'application/json' });
    expect(seeded.traceId).toBe(cfg.traceId!.toLowerCase().replace(
      /(.{8})(.{4})(.{4})(.{4})(.{12})/, '$1-$2-$3-$4-$5'));
    expect(rows.map(row => [row.name, row.parent_span_id, row.observation_type])).toEqual([
      [`${projectName}.voice`, '', 'conversation'],
      [`${projectName}.tool`, 'abcdef0000000001', 'tool'],
    ]);
    expect(rows.map(row => [row.org_id, row.start_us, row.end_us])).toEqual([
      [actor.organizationId, String(start / 1000n), String(start / 1000n + 75_000n)],
      [actor.organizationId, String(start / 1000n), String(start / 1000n + 75_000n)],
    ]);
    expect(rows.map(row => [row.attrs_string['fixture.string'], row.attrs_number['fixture.number'],
      row.attrs_bool['fixture.bool']])).toEqual([['001', 0, 0], ['002', 2.5, 1]]);
    expect(rows.map(row => JSON.parse(row.extra)['fixture.array'])).toEqual([
      ['001', 0, false, { nested: ['x', 1, true] }, [], {}], [true, 2, 'child'],
    ]);
    expect(rows.map(row => JSON.parse(row.extra)['fixture.object'])).toEqual([
      { 'part.dot': { 'slash/key': '雪' }, enabled: true }, { child: { values: [false, '03'] } },
    ]);
    expect(rows[0].attrs_string['call.status']).toBe('completed');
    expect(rows[1].attrs_string).not.toHaveProperty('call.status'); // No implicit inheritance.
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [projectName, actor.organizationId]);
    expect(projects).toHaveLength(1);
    expect(rows.map(row => row.project_id)).toEqual([projects[0].id, projects[0].id]);

    // Re-ingest the SAME wire facts, not SQL rows. Wait for newer physical versions
    // so an assertion cannot pass by observing only the first export.
    expect(await sendTrace(request, cfg)).toEqual(seeded);
    await expect.poll(async () => (await read()).map((row, index) =>
      BigInt(row.version) > BigInt(rows[index].version)), POLL.SPAN_VISIBLE).toEqual([true, true]);
    const replay = await read();
    await testInfo.attach('replayed-source-spans', { body: JSON.stringify(replay), contentType: 'application/json' });
    expect(replay.map(({ version, ...row }) => row)).toEqual(rows.map(({ version, ...row }) => row));
  });

test('OTLP groups two traces by session/user and keeps different sessions and projects separate',
  async ({ request, actor, probe }, testInfo) => {
    const prefix = `e2e-otlp-group-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const fixtures = [
      { project: prefix, session: 'shared', user: '001' },
      { project: prefix, session: 'shared', user: '001' },
      { project: prefix, session: 'other', user: '002' },
      { project: `${prefix}-sibling`, session: 'shared', user: '001' },
    ];
    const seeded: SeededTrace[] = [];
    for (const fixture of fixtures) {
      const attributes = { 'session.id': fixture.session, 'user.id': fixture.user, 'user.id.type': 'custom' };
      seeded.push(await sendTrace(request, {
        collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: fixture.project, resourceAttributes: { project_type: 'observe' },
        rootAttributes: attributes, childAttributes: attributes,
      }));
    }
    await testInfo.attach('seeded-groups', { body: JSON.stringify(seeded), contentType: 'application/json' });
    interface GroupRow {
      trace_id: string; id: string; name: string; parent_span_id: string; observation_type: string;
      project_id: string; org_id: string; trace_session_id: string | null; end_user_id: string | null;
      session: string; user: string; latency_ms: number;
    }
    const read = () => probe.ch<GroupRow>(`
      SELECT trace_id, id, name, parent_span_id, observation_type, project_id, org_id, latency_ms,
        trace_session_id, end_user_id, attrs_string['session.id'] AS session, attrs_string['user.id'] AS user
      FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String}, {c:String}, {d:String})`,
    { a: seeded[0].traceId, b: seeded[1].traceId, c: seeded[2].traceId, d: seeded[3].traceId });
    await expect.poll(async () => (await read()).map(row => row.id).sort(), POLL.SPAN_VISIBLE)
      .toEqual(seeded.flatMap(trace => trace.spanIds).sort());
    const rows = await read();
    await testInfo.attach('source-groups', { body: JSON.stringify(rows), contentType: 'application/json' });
    const roots = seeded.map(trace => rows.find(row => row.id === trace.spanIds[0])!);
    for (const [index, trace] of seeded.entries()) {
      const root = roots[index];
      const child = rows.find(row => row.id === trace.spanIds[1])!;
      expect(root.trace_session_id).toMatch(/^[0-9a-f-]{36}$/);
      expect(root.end_user_id).toMatch(/^[0-9a-f-]{36}$/);
      expect([root.name, root.parent_span_id]).toEqual(['e2e.root', '']);
      expect([child.name, child.parent_span_id, child.observation_type])
        .toEqual(['e2e.llm-call', root.id, 'llm']);
      expect([child.trace_session_id, child.end_user_id, child.project_id])
        .toEqual([root.trace_session_id, root.end_user_id, root.project_id]);
      for (const row of [root, child]) {
        expect(row.latency_ms).toBe(50);
        expect([row.trace_id, row.org_id, row.session, row.user])
          .toEqual([trace.traceId, actor.organizationId, fixtures[index].session, fixtures[index].user]);
      }
    }
    expect(roots[0].trace_session_id).toBe(roots[1].trace_session_id);
    expect(roots[0].end_user_id).toBe(roots[1].end_user_id);
    expect(new Set([roots[0].trace_session_id, roots[2].trace_session_id, roots[3].trace_session_id]).size).toBe(3);
    expect(new Set([roots[0].end_user_id, roots[2].end_user_id, roots[3].end_user_id]).size).toBe(3);
    expect(roots.slice(0, 3).map(row => row.project_id)).toEqual(Array(3).fill(roots[0].project_id));
    expect(roots[3].project_id).not.toBe(roots[0].project_id);
  });

test('OTLP rejects malformed IDs, timestamps and non-finite attributes before sending', async ({ request }) => {
  const cfg = { collectorUrl: 'http://localhost:0', apiKey: 'unused', secretKey: 'unused', projectName: 'unused' };
  const cases: [Partial<SendTraceConfig>, RegExp][] = [
    [{ traceId: '0'.repeat(32) }, /32 nonzero hex/],
    [{ traceId: 'bad-id' }, /32 nonzero hex/],
    [{ rootSpanId: 'bad-id' }, /16 nonzero hex/],
    [{ rootSpanId: '1234567890abcdef', childSpanId: '1234567890abcdef' }, /must differ/],
    [{ startTimeUnixNano: '-1' }, /unsigned decimal/],
    [{ startTimeUnixNano: 0n }, /positive uint64/],
    [{ startTimeUnixNano: '18446744073709551616' }, /positive uint64/],
    [{ startTimeUnixNano: 2n, endTimeUnixNano: 1n }, /must not precede/],
    [{ rootAttributes: { invalid: [Infinity] } }, /finite scalars/],
  ];
  for (const [invalid, error] of cases) await expect(sendTrace(request, { ...cfg, ...invalid })).rejects.toThrow(error);
});
