import { randomUUID } from 'node:crypto';
import { test, expect } from '../lib/fixtures';
import { sendOtlpExport, selectOtlpSpans } from '../lib/otlp';
import { syntheticCopilotDetailsFixture, syntheticMixedFixture } from './native-otlp-fixtures';
import { E2E } from '../lib/env';
import { POLL } from '../lib/state-probe';

// H-NATIVE-OTLP managed harness checks, NOT OBS flows or live vendor evidence.
// Remote owned candidate stack only; source IDs are minted per test. No product
// routes are enabled. Collector normalization/schema pins:
// - fi-collector/exporter/clickhouse25exporter/converter.go:row, statusString, spanEventsJSON
// - fi-collector/pkg/normalization/{profiles_data,details}.go
// - futureagi/tracer/services/clickhouse/v2/schema/002_spans_v2.sql
const root = '0000000000000001';
const profile = 'github-copilot-cli-1.0.98';
interface Row {
  trace_id: string; id: string; parent_span_id: string; name: string;
  org_id: string; project_id: string; start_us: string; end_us: string;
  status: string; status_message: string; span_events: string; extra: string;
  profile: string; version: string;
}
const columns = `trace_id, id, parent_span_id, name, org_id, project_id,
  toUnixTimestamp64Micro(start_time) AS start_us, toUnixTimestamp64Micro(end_time) AS end_us,
  status, status_message, span_events, toString(attributes_extra) AS extra,
  attrs_string['fi.normalization.profile'] AS profile, toString(_version) AS version`;

test('H-NATIVE-OTLP storage: child-first split and replay retain source IDs, details and timestamps',
  async ({ request, actor, probe }, testInfo) => {
    // Three SPAN_VISIBLE waits (45s) plus provisioning/I/O fit the 120s ceiling.
    const projectName = `e2e-native-split-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const traceId = randomUUID();
    const start = BigInt(Date.now() - 30_000) * 1_000_000n + 123_000n;
    const fixture = syntheticCopilotDetailsFixture({ projectName, traceId: traceId.replaceAll('-', ''),
      startTimeUnixNano: String(start), endTimeUnixNano: String(start + 1_000_000_000n),
      conversationId: `${projectName}-conversation` });
    const members = fixture.resourceSpans[0].scopeSpans[0].spans;
    const parent = selectOtlpSpans(fixture, [{ traceId: members[0].traceId, spanId: root }]);
    const children = selectOtlpSpans(fixture, members.slice(1).map(s => ({ traceId: s.traceId, spanId: s.spanId })));
    const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey };
    const read = () => probe.ch<Row>(`SELECT ${columns} FROM spans FINAL WHERE trace_id = {t:String} ORDER BY id`, { t: traceId });
    const childrenReceipt = await sendOtlpExport(request, cfg, children);
    await testInfo.attach('native-split-children', { body: JSON.stringify(childrenReceipt), contentType: 'application/json' });
    await expect.poll(async () => (await read()).map(s => [s.id, s.parent_span_id]), POLL.SPAN_VISIBLE)
      .toEqual(members.slice(1).map(s => [s.spanId, root]));
    const parentReceipt = await sendOtlpExport(request, cfg, parent);
    await testInfo.attach('native-split-parent', { body: JSON.stringify(parentReceipt), contentType: 'application/json' });
    await expect.poll(async () => (await read()).map(s => s.id), POLL.SPAN_VISIBLE).toEqual(members.map(s => s.spanId));
    const rows = await read();
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND workspace_id = $3',
      [projectName, actor.organizationId, actor.workspaceId]);
    expect(projects).toHaveLength(1);
    expect(rows.map(s => [s.id, s.parent_span_id, s.name, s.org_id, s.project_id, s.start_us, s.end_us, s.profile]))
      .toEqual(members.map(s => [s.spanId, s.parentSpanId ?? '', s.name, actor.organizationId, projects[0].id,
        String(start / 1000n), String((start + 1_000_000_000n) / 1000n), profile]));
    expect(rows.map(s => [s.status, s.status_message])).toEqual([
      ['ERROR', 'synthetic timeout'], ['UNSET', ''], ['UNSET', ''], ['UNSET', ''], ['UNSET', ''],
    ]);
    const iso = new Date(Number(start / 1_000_000n)).toISOString();
    const eventTime = `${iso.slice(0, 10)} ${iso.slice(11, 23)}123`;
    expect(JSON.parse(rows[0].span_events)).toEqual([{ name: 'exception', timestamp: eventTime,
      attributes: { 'exception.type': 'Timeout' }, dropped_attributes_count: 0 }]);
    expect(JSON.parse(rows[0].extra)['fi.otel.links']).toEqual([{ trace_id: members[0].traceId,
      span_id: members[1].spanId, attributes: { 'fi.agent.caused_by.work_id': 'synthetic-work' }, dropped_attributes_count: 0 }]);
    expect(rows.slice(1).map(s => JSON.parse(s.span_events))).toEqual([[], [], [], []]);
    // A newer physical version, not the previous successful write, must satisfy
    // replay. The whole source payload is unchanged across deliveries.
    await sendOtlpExport(request, cfg, fixture);
    await expect.poll(async () => (await read()).map((s, i) => BigInt(s.version) > BigInt(rows[i].version)), POLL.SPAN_VISIBLE)
      .toEqual([true, true, true, true, true]);
    expect((await read()).map(({ version, ...s }) => s)).toEqual(rows.map(({ version, ...s }) => s));
    await testInfo.attach('native-split-source-rows', { body: JSON.stringify(rows), contentType: 'application/json' });
  });

test('H-NATIVE-OTLP storage: mixed scope/resource groups keep all source pairs and project boundaries',
  async ({ request, actor, probe }, testInfo) => {
    const projectName = `e2e-native-mixed-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const siblingProjectName = `${projectName}-sibling`;
    const traceIds = [randomUUID(), randomUUID(), randomUUID()];
    const start = BigInt(Date.now() - 30_000) * 1_000_000n;
    const fixture = syntheticMixedFixture({ projectName, siblingProjectName,
      traceId: traceIds[0].replaceAll('-', ''), unknownTraceId: traceIds[1].replaceAll('-', ''),
      siblingTraceId: traceIds[2].replaceAll('-', ''), conversationId: `${projectName}-conversation`,
      startTimeUnixNano: String(start), endTimeUnixNano: String(start + 1_000_000_000n) });
    const receipt = await sendOtlpExport(request, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
    }, fixture);
    await testInfo.attach('native-mixed-seeded', { body: JSON.stringify(receipt), contentType: 'application/json' });
    const read = () => probe.ch<Row>(`SELECT ${columns} FROM spans FINAL
      WHERE trace_id IN ({a:String}, {b:String}, {c:String}) ORDER BY trace_id, id`,
    { a: traceIds[0], b: traceIds[1], c: traceIds[2] });
    await expect.poll(async () => (await read()).map(s => `${s.trace_id}:${s.id}`), POLL.SPAN_VISIBLE)
      .toEqual(receipt.spans.map(s => `${s.traceId}:${s.spanId}`).sort());
    const projects = await probe.pg<{ id: string; name: string }>(
      'SELECT id, name FROM tracer_project WHERE name = ANY($1) AND organization_id = $2 AND workspace_id = $3 ORDER BY name',
      [[projectName, siblingProjectName], actor.organizationId, actor.workspaceId]);
    expect(projects.map(p => p.name)).toEqual([projectName, siblingProjectName].sort());
    const rows = await read();
    for (const [index, traceId] of traceIds.entries()) {
      const source = rows.filter(s => s.trace_id === traceId);
      const projectId = projects.find(p => p.name === (index === 2 ? siblingProjectName : projectName))!.id;
      expect(source.map(s => [s.id, s.parent_span_id, s.project_id, s.org_id, s.profile]))
        .toEqual([1, 2, 3, 4, 5].map(i => [String(i).padStart(16, '0'), i === 1 ? '' : root,
          projectId, actor.organizationId, index === 1 ? '' : profile]));
    }
    await testInfo.attach('native-mixed-source-rows', { body: JSON.stringify(rows), contentType: 'application/json' });
  });
