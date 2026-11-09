import { test, expect } from '../lib/fixtures';
import { sendTrace } from '../lib/otlp';
import { POLL } from '../lib/state-probe';
import { E2E } from '../lib/env';
import {
  SEEDED_ATTRIBUTE, liveSessionRows, planSharedSession, projectServedByUnkeyedSessionRead, seedSharedSession,
  tombstoneSeededSession,
} from '../lib/seeded-clickhouse';

// Live half of lib/seeded-clickhouse.ts (offline half: scripts/seeded-clickhouse.test.mjs).
// One internal trace-session UUID in two projects of one organization: the state a
// project-keyed lookup (F54 row-source liveness, model_hub/services/row_sources.py
// _live_records -> resolve_session_fields(project_ids=[record.project_id])) must
// survive, and that sendTrace cannot make (the collector keys the id by project).
const LIST = '/tracer/trace-session/list_sessions/';
interface ListRow { session_id: string; total_traces_count: number; first_message: string | null; last_message: string | null }
interface Count { project_id: string; n: string; marked?: string }

// trace_session_dict_reader.resolve_session_fields' SELECT (no remap row exists for a
// seeded id, so its remap join resolves each id to itself), with its project clause
// (keyed) and without it (the deliberately project-unkeyed control).
const SESSION_FIELDS = `SELECT toString(r.input_id) AS input_id, toString(ts.project_id) AS project_id
  FROM (SELECT arrayJoin({ids:Array(UUID)}) AS input_id) AS r
  INNER JOIN trace_sessions AS ts FINAL ON ts.trace_session_id = r.input_id
  WHERE ts.is_deleted = 0`;

test('seeded ClickHouse puts one session UUID in two projects: keyed reads return their own row, an unkeyed read both',
  async ({ request, actor, probe }, testInfo) => {
    // SPAN_VISIBLE polls (15 s each) and a handful of API reads; no browser.
    test.setTimeout(300_000);
    const prefix = `e2e-seeded-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const names = { a: `${prefix}-a`, b: `${prefix}-b`, c: `${prefix}-c` };
    const chat = `${prefix}-chat`;
    // Three projects of the actor's organization, made by the product's own ingest. A and
    // B get no session.id (no collector session rows); C gets a collector session to shadow.
    for (const [key, projectName] of Object.entries(names)) {
      const attributes: Record<string, string> = key === 'c' ? { 'session.id': chat } : {};
      await sendTrace(request, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName, resourceAttributes: { project_type: 'observe' }, rootAttributes: attributes, childAttributes: attributes });
    }
    let projects: { id: string; name: string }[] = [];
    await expect.poll(async () => {
      projects = await probe.pg<{ id: string; name: string }>(
        'SELECT id::text AS id, name FROM tracer_project WHERE name = ANY($1) AND organization_id = $2 AND workspace_id = $3',
        [Object.values(names), actor.organizationId, actor.workspaceId]);
      return projects.length;
    }, POLL.SPAN_VISIBLE).toBe(3);
    const idOf = (name: string) => projects.find(p => p.name === name)!.id;
    const [a, b, c] = [idOf(names.a), idOf(names.b), idOf(names.c)];

    const plan = planSharedSession({ orgId: actor.organizationId, projects: [{ projectId: a, turns: 1 }, { projectId: b, turns: 2 }] });
    const sid = plan.traceSessionId;
    const seeded = await seedSharedSession(testInfo, plan);
    expect(seeded.receipt.tables.spans).toMatchObject({ planned: 6, inserted: 6, alreadyPresent: 0 });
    expect(seeded.receipt.tables.trace_sessions).toMatchObject({ planned: 2, inserted: 2, alreadyPresent: 0 });

    await test.step('the same internal id lands in both projects of one organization', async () => {
      expect((await liveSessionRows(sid)).map(r => [r.project_id, r.external_session_id]))
        .toEqual([a, b].sort().map(p => [p, plan.externalSessionId]));
      expect(await probe.ch(`SELECT toString(project_id) AS project_id, e2e_seed_id FROM trace_sessions FINAL
        WHERE trace_session_id = {s:UUID} ORDER BY project_id`, { s: sid }))
        .toEqual([a, b].sort().map(project_id => ({ project_id, e2e_seed_id: plan.seedId })));
      const spans = await probe.ch<Count>(`SELECT toString(project_id) AS project_id, count() AS n,
          countIf(attrs_string[{attr:String}] = {seed:String} AND org_id = {org:UUID}) AS marked
        FROM spans FINAL WHERE trace_session_id = {s:UUID} GROUP BY project_id`,
      { s: sid, attr: SEEDED_ATTRIBUTE, seed: plan.seedId, org: actor.organizationId });
      expect(Object.fromEntries(spans.map(r => [r.project_id, [Number(r.n), Number(r.marked)]])))
        .toEqual({ [a]: [2, 2], [b]: [4, 4] });
    });

    // spans_per_session (008) is fed by the inserts (countState), so a re-run must not
    // insert again. Where a stack lacks the view, raw() (physical rows) carries the proof.
    const views = await probe.ch<{ n: string }>(`SELECT count() AS n FROM system.tables
      WHERE database = currentDatabase() AND name = 'spans_per_session'`);
    const hasRollup = views[0]?.n === '1';
    await testInfo.attach('rollup.json', { contentType: 'application/json',
      body: JSON.stringify({ spans_per_session: hasRollup ? 'present' : 'absent on this stack' }) });
    const rollup = async () => (hasRollup ? Object.fromEntries((await probe.ch<Count>(`SELECT
        toString(project_id) AS project_id, countMerge(span_count) AS n FROM spans_per_session
        WHERE trace_session_id = {s:UUID} GROUP BY project_id`, { s: sid })).map(r => [r.project_id, Number(r.n)]))
      : { [a]: 2, [b]: 4 });
    const raw = () => probe.ch<{ spans: string; sessions: string }>(`SELECT
        (SELECT count() FROM spans WHERE trace_session_id = {s:UUID}) AS spans,
        (SELECT count() FROM trace_sessions WHERE trace_session_id = {s:UUID}) AS sessions`, { s: sid });
    expect(await rollup()).toEqual({ [a]: 2, [b]: 4 });
    expect(await raw()).toEqual([{ spans: '6', sessions: '2' }]);

    await test.step('each project-keyed read returns only that project\'s row', async () => {
      for (const p of [a, b]) {
        expect((await liveSessionRows(sid, { projectId: p })).map(r => r.project_id)).toEqual([p]);
        expect(await probe.ch(`${SESSION_FIELDS} AND ts.project_id = {p:UUID}`, { ids: `['${sid}']`, p }))
          .toEqual([{ input_id: sid, project_id: p }]);
      }
      // The API the Sessions grid calls, per project: its own traces and messages only (the
      // list computes both from root spans: query_builders/session_list.py).
      for (const [p, seededProject] of [[a, plan.projects[0]], [b, plan.projects[1]]] as const) {
        await expect.poll(async () => {
          const body = await actor.api.get<{ result: { table: ListRow[] } }>(LIST, { project_id: p, page_size: 25, page_number: 0 });
          return body.result.table.filter(r => r.session_id === sid)
            .map(r => [r.total_traces_count, r.first_message, r.last_message]);
        }, POLL.SPAN_VISIBLE).toEqual([[seededProject.turns.length, seededProject.turns[0].input,
          seededProject.turns[seededProject.turns.length - 1].input]]);
      }
    });

    await test.step('the same read without its project clause sees both rows (the discriminating control)', async () => {
      const unkeyed = await probe.ch<{ input_id: string; project_id: string }>(SESSION_FIELDS, { ids: `['${sid}']` });
      expect(unkeyed.map(r => r.input_id)).toEqual([sid, sid]);
      expect(unkeyed.map(r => r.project_id).sort()).toEqual([a, b].sort());
      // resolve_session_fields builds {input_id: row} by overwrite, so an unkeyed read
      // keeps whichever row came last. Recorded: which project that was on this stack.
      const collapsed = Object.fromEntries(unkeyed.map(r => [r.input_id, r.project_id]));
      // The product's own unkeyed resolver (F54's session tab endpoint) keeps exactly one of
      // the two projects; a control puts its record in the OTHER one. Recorded per stack.
      const served = await projectServedByUnkeyedSessionRead(actor.api, plan);
      expect([a, b]).toContain(served);
      await testInfo.attach('unkeyed-reads.json', { contentType: 'application/json', body: JSON.stringify({
        projects: { a, b }, rowOrder: unkeyed.map(r => (r.project_id === a ? 'a' : 'b')),
        dictCollapsesTo: collapsed[sid] === a ? 'a' : 'b',
        sessionTabServes: served === a ? 'a' : 'b' }, null, 2) });
    });

    await test.step('the same plan again writes nothing', async () => {
      const before = await raw();
      const again = await seedSharedSession(testInfo, plan);
      expect(again.receipt.tables.spans).toMatchObject({ inserted: 0, alreadyPresent: 6 });
      expect(again.receipt.tables.trace_sessions).toMatchObject({ inserted: 0, alreadyPresent: 2 });
      expect(await raw()).toEqual(before);
      expect(await rollup()).toEqual({ [a]: 2, [b]: 4 });
    });

    await test.step('a tombstone leaves the id live in the other project only, under FINAL', async () => {
      expect(await tombstoneSeededSession(testInfo, plan, a)).toEqual({ tombstoned: true });
      expect((await liveSessionRows(sid)).map(r => r.project_id)).toEqual([b]);
      expect(await liveSessionRows(sid, { projectId: a })).toEqual([]);
      expect(await probe.ch(SESSION_FIELDS, { ids: `['${sid}']` })).toEqual([{ input_id: sid, project_id: b }]);
      expect(await tombstoneSeededSession(testInfo, plan, a)).toEqual({ tombstoned: false });
      const resumed = await seedSharedSession(testInfo, plan);
      expect(resumed.receipt.tables.trace_sessions).toMatchObject({ inserted: 0, alreadyPresent: 2 });
      expect((await liveSessionRows(sid)).map(r => r.project_id)).toEqual([b]);
    });

    await test.step('a collector session is shadowed into another project, never overwritten', async () => {
      interface SessionRow { trace_session_id: string; external_session_id: string; version: string }
      let collector: SessionRow[] = [];
      await expect.poll(async () => {
        collector = await probe.ch<SessionRow>(`SELECT toString(trace_session_id) AS trace_session_id, external_session_id,
          toString(version) AS version FROM trace_sessions FINAL WHERE project_id = {p:UUID} AND is_deleted = 0`, { p: c });
        return collector.map(r => r.external_session_id);
      }, POLL.SPAN_VISIBLE).toEqual([chat]);
      const shared = collector[0].trace_session_id;
      const intoOwn = planSharedSession({ orgId: actor.organizationId, traceSessionId: shared, externalSessionId: chat,
        projects: [{ projectId: c }] });
      await expect(seedSharedSession(testInfo, intoOwn)).rejects.toThrow(/not seeded by this plan/);
      expect(await probe.ch(`SELECT count() AS n FROM spans WHERE attrs_string[{attr:String}] = {seed:String}`,
        { attr: SEEDED_ATTRIBUTE, seed: intoOwn.seedId })).toEqual([{ n: '0' }]);
      // Collector writes AFTER planning, with the same external identity and a higher
      // version. It still owns that row: neither seeding nor deletion may claim it.
      await sendTrace(request, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: names.c, resourceAttributes: { project_type: 'observe' },
        rootAttributes: { 'session.id': chat }, childAttributes: { 'session.id': chat } });
      await expect.poll(async () => {
        collector = await probe.ch<SessionRow>(`SELECT toString(trace_session_id) AS trace_session_id, external_session_id,
          toString(version) AS version FROM trace_sessions FINAL WHERE project_id = {p:UUID} AND is_deleted = 0`, { p: c });
        return collector.length === 1 && collector[0].version > intoOwn.version;
      }, POLL.SPAN_VISIBLE).toBe(true);
      expect(await probe.ch(`SELECT e2e_seed_id FROM trace_sessions FINAL
        WHERE project_id = {p:UUID} AND trace_session_id = {s:UUID}`, { p: c, s: shared }))
        .toEqual([{ e2e_seed_id: '' }]);
      await expect(seedSharedSession(testInfo, intoOwn)).rejects.toThrow(/not seeded by this plan/);
      await expect(tombstoneSeededSession(testInfo, intoOwn, c)).rejects.toThrow(/holds no row seeded/);
      expect(await probe.ch(`SELECT count() AS n FROM spans WHERE attrs_string[{attr:String}] = {seed:String}`,
        { attr: SEEDED_ATTRIBUTE, seed: intoOwn.seedId })).toEqual([{ n: '0' }]);
      expect(await probe.ch(`SELECT count() AS n FROM trace_sessions
        WHERE project_id = {p:UUID} AND trace_session_id = {s:UUID} AND is_deleted = 1`, { p: c, s: shared }))
        .toEqual([{ n: '0' }]);
      await testInfo.attach('newer-collector-ownership.json', { contentType: 'application/json', body: JSON.stringify({
        planVersion: intoOwn.version, collectorVersion: collector[0].version, marker: '',
        seedRefused: true, tombstoneRefused: true, seededSpans: 0, tombstones: 0 }) });
      const shadow = planSharedSession({ orgId: actor.organizationId, traceSessionId: shared, externalSessionId: chat,
        projects: [{ projectId: b }] });
      await seedSharedSession(testInfo, shadow);
      expect((await liveSessionRows(shared)).map(r => r.project_id)).toEqual([b, c].sort());
      for (const p of [b, c]) expect((await liveSessionRows(shared, { projectId: p })).map(r => r.project_id)).toEqual([p]);
      expect(await probe.ch(`SELECT toString(trace_session_id) AS trace_session_id, external_session_id,
        toString(version) AS version FROM trace_sessions FINAL WHERE project_id = {p:UUID} AND is_deleted = 0`, { p: c }))
        .toEqual(collector);
    });
  });
