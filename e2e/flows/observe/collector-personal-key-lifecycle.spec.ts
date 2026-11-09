import { randomBytes, randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { createPersonalKey } from '../../lib/personal-key';
import { seedRetainedCredentials } from '../../lib/retained-credentials';
import { sendTraceResult, type TraceHttpResult } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned to accounts/views/keys.py:generate_secret_key/delete_secret_key and
// accounts/serializers/org_api_key.py:UserSecretKeySerializer on the personal issuer.
const DELETE_KEY = '/accounts/key/delete_secret_key/';
// SpanGrid.jsx:buildParams -> endpoints.project.getSpansForObserveProject.
const SPAN_LIST = '/tracer/observation-span/list_spans_observe/';
const SPAN_PAGE_SIZE = 25;
// README Writing a flow: one navigation/load budget, not a stage-wide wait.
const UI_READY = 60_000;
// Collector cache.go:newCache clamps primary-backed eligibility to 30 seconds.
const REVOCATION_BOUND = 30_000;
const HTTP_REQUEST = 5_000;

// The actor issuer and retained fixture carry one-time material. Do not persist
// API traffic in Playwright trace archives; attachments below are value-free.
test.use({ trace: 'off', video: 'off' });

test('OBS-E2E-061: personal traces remain visible while retired and revoked credentials are refused', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-061', area: 'observe',
    userGoal: 'A developer can ingest and inspect traces with a personal key, while retired credentials and a revoked personal key can no longer add traces.',
    steps: [
      'provision the actor with a session-created personal key and explicitly create two flow-specific personal keys',
      'ingest an independently identified trace and inspect it in Observe',
      'send retained synthetic system and retired credentials and require HTTP 401',
      'warm the personal key, start a monotonic clock before DELETE, and read its committed deleted state from primary Postgres',
      'require a completed export refusal within 30 seconds of the pre-DELETE boundary',
      'ingest with the eligible control, reload Observe and compare exact accepted identities with API and storage',
    ],
    backendChecks: [
      'primary Postgres binds the personal key to the signed-in person and org; latest spans and traces retain every minted project and identity',
      'valid retained synthetic system and retired credentials return 401 and remain absent from spans/traces FINAL throughout a full 15-second exclusion window after a positive export barrier',
      'committed personal-key deletion is followed by a completed 401 within 30000 ms of the pre-DELETE clock, while the independent control still ingests',
      'Observe exact accepted trace identities equal the real span-list API and latest storage identities, with every refused identity absent',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(540_000); // 6*UI_READY + 4*SPAN_VISIBLE + REVOCATION_BOUND + 90000 headroom.
  const req = await request.newContext({ timeout: HTTP_REQUEST });
  const name = `e2e-obs61-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const accepted: TraceHttpResult[] = [];
  const refused: TraceHttpResult[] = [];
  const receipts: { traceId: string; spanIds: string[]; status: number; elapsedMs: number }[] = [];
  const rootName = (traceId: string) => `${name}.${traceId}`;
  const exportWith = async (key: { apiKey: string; secretKey: string }, timeout = HTTP_REQUEST) => {
    const traceId = randomUUID();
    const result = await sendTraceResult(req, { collectorUrl: E2E.collectorUrl, ...key,
      projectName: name, traceId, rootSpanId: randomBytes(8).toString('hex'),
      childSpanId: randomBytes(8).toString('hex'), rootName: rootName(traceId),
      childName: `${name}.child.${traceId}`, resourceAttributes: { project_type: 'observe' } }, timeout);
    receipts.push({ traceId: result.traceId, spanIds: result.spanIds,
      status: result.status, elapsedMs: result.elapsedMs });
    return result;
  };
  try {
    const personal = await createPersonalKey(actor.api, `${name}-personal`);
    const control = await createPersonalKey(actor.api, `${name}-control`);
    const key = { apiKey: personal.api_key, secretKey: personal.secret_key };
    const controlKey = { apiKey: control.api_key, secretKey: control.secret_key };
    const retained = seedRetainedCredentials(actor, name);
    await testInfo.attach('seeded-identities', { contentType: 'application/json', body: JSON.stringify({
      projectName: name, organizationId: actor.organizationId, workspaceId: actor.workspaceId,
      personalId: personal.key_id, controlId: control.key_id,
      systemId: retained.system.id, retiredId: retained.retired.id,
    }) });

    // Read-only primary PG (E2E_PG_URL), never a replica or a fixture's claimed state.
    expect(await probe.pg('SELECT pg_is_in_recovery() AS replica')).toEqual([{ replica: false }]);
    const keyRows = await probe.pg<{ id: string; type: string; email: string; organization_id: string;
      workspace_id: string | null; enabled: boolean; deleted: boolean; retired: boolean; sealed: boolean }>(`
      SELECT k.id, k.type, u.email, k.organization_id, k.workspace_id, k.enabled, k.deleted,
        k.retired_at IS NOT NULL AS retired,
        k.secret_key = '' AND k.secret_key_hash <> '' AND k.secret_key_ciphertext = '' AS sealed
      FROM accounts_orgapikey k JOIN accounts_user u ON u.id = k.user_id
      WHERE k.id = ANY($1::uuid[]) ORDER BY k.id`,
    [[personal.key_id, control.key_id, retained.system.id, retained.retired.id]]);
    expect(keyRows).toHaveLength(4);
    for (const row of keyRows) {
      expect(row.email).toBe(actor.email);
      expect(row.organization_id).toBe(actor.organizationId);
      const synthetic = row.id === retained.system.id || row.id === retained.retired.id;
      expect(row.workspace_id).toBe(synthetic ? actor.workspaceId : null);
      expect(row.type).toBe(row.id === retained.system.id ? 'system' : 'user');
      expect(row.retired).toBe(row.id === retained.retired.id);
      expect([row.enabled, row.deleted, row.sealed]).toEqual([true, false, true]);
    }
    accepted.push(await exportWith(key));
    expect(accepted[0].status).toBe(200);
    await expect.poll(async () => (await probe.ch<{ id: string }>(
      'SELECT id FROM spans FINAL WHERE trace_id = {t:String} ORDER BY id',
      { t: accepted[0].traceId })).map(row => row.id), POLL.SPAN_VISIBLE)
      .toEqual([...accepted[0].spanIds].sort());
    await expect.poll(async () => (await probe.ch<{ id: string }>(
      'SELECT id FROM traces FINAL WHERE id = {t:UUID}', { t: accepted[0].traceId }))
      .map(row => row.id), POLL.SPAN_VISIBLE).toEqual([accepted[0].traceId]);
    const projects = await probe.pg<{ id: string; workspace_id: string }>(
      'SELECT id, workspace_id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND deleted=false',
      [name, actor.organizationId]);
    expect(projects).toHaveLength(1);
    expect(projects[0].workspace_id).toBe(actor.workspaceId);
    const projectId = projects[0].id;

    await test.step('Observe lists the minted project', async () => {
      await page.setViewportSize({ width: 1440, height: 1800 }); // render the 25-row page without virtual clipping
      await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded', timeout: UI_READY });
      await expect(page.getByText(name, { exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    // TraceGrid.jsx has separate mounted primary/compare grids; select only the visible one.
    const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');
    await test.step('Observe contains exactly the accepted trace', async () => {
      await page.getByText(name, { exact: true }).click({ timeout: UI_READY });
      await expect(traceNames).toHaveText([rootName(accepted[0].traceId)], { timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('The trace drawer exposes the exact minted root span', async () => {
      await traceNames.first().click({ timeout: UI_READY });
      await expect(page.getByText(accepted[0].spanIds[0], { exact: true }).first())
        .toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    for (const retainedKey of [retained.system, retained.retired]) {
      const result = await exportWith(retainedKey);
      refused.push(result);
      expect(result.status).toBe(401);
    }
    const warm = await exportWith(key);
    expect(warm.status).toBe(200);
    accepted.push(warm);
    // Start BEFORE the mutation request, never after its response/readback. The
    // commit must happen later, so this is conservative relative to the DB commit.
    const beforeDelete = performance.now();
    let deletionStatus: number;
    try {
      const response = await req.delete(`${E2E.apiUrl}${DELETE_KEY}`, {
        headers: { Authorization: `Bearer ${actor.tokens.access}`,
          'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId },
        data: { key_id: personal.key_id }, timeout: HTTP_REQUEST, maxRedirects: 0,
      });
      deletionStatus = response.status();
    } catch { throw new Error('Personal key deletion transport failed (details withheld)'); }
    expect(deletionStatus).toBe(200);
    expect(await probe.pg('SELECT deleted, enabled, deleted_at IS NOT NULL AS dated FROM accounts_orgapikey WHERE id=$1',
      [personal.key_id])).toEqual([{ deleted: true, enabled: false, dated: true }]);
    let refusalElapsed: number | undefined;
    while (performance.now() - beforeDelete < REVOCATION_BOUND) {
      const remaining = REVOCATION_BOUND - (performance.now() - beforeDelete);
      const attempt = await exportWith(key, Math.max(1, Math.min(HTTP_REQUEST, remaining)));
      const elapsed = performance.now() - beforeDelete;
      expect(elapsed).toBeLessThanOrEqual(REVOCATION_BOUND); // includes request duration
      if (attempt.status === 401) {
        refused.push(attempt);
        refusalElapsed = elapsed;
        break;
      }
      expect(attempt.status).toBe(200); // 5xx/timeouts are not revocation proof
      accepted.push(attempt);
      await delay(Math.min(2_000, Math.max(0, (REVOCATION_BOUND - elapsed) / 2)));
    }
    expect(refusalElapsed, 'a 401 must complete before the absolute revocation deadline').toBeDefined();
    await testInfo.attach('revocation-duration', { contentType: 'application/json',
      body: JSON.stringify({ refusalElapsedMs: refusalElapsed, boundMs: REVOCATION_BOUND }) });
    const live = await exportWith(controlKey);
    expect(live.status).toBe(200);
    accepted.push(live);

    const acceptedIds = accepted.map(row => row.traceId).sort();
    const expectedSpans = accepted.flatMap(row => row.spanIds.map(id => ({ id,
      trace_id: row.traceId, project_id: projectId, org_id: actor.organizationId })))
      .sort((a, b) => a.id.localeCompare(b.id));
    const spans = () => probe.ch<{ id: string; trace_id: string; project_id: string; org_id: string }>(
      'SELECT id, trace_id, project_id, org_id FROM spans FINAL WHERE project_id={p:UUID} ORDER BY id', { p: projectId });
    // schema/015_traces_and_trace_dict.sql: curated traces carry project_id, not org_id.
    const traces = () => probe.ch<{ id: string; project_id: string }>(
      'SELECT id, project_id FROM traces FINAL WHERE project_id={p:UUID} ORDER BY id', { p: projectId });
    // Independent tables are polled concurrently: one SPAN_VISIBLE budget.
    await Promise.all([
      expect.poll(spans, POLL.SPAN_VISIBLE).toEqual(expectedSpans),
      expect.poll(traces, POLL.SPAN_VISIBLE).toEqual(acceptedIds.map(id => ({ id,
        project_id: projectId }))),
    ]);
    // The positive liveness export is already present in BOTH tables. Hold absence
    // for the FULL visibility window, not an expect.poll that succeeds instantly.
    const exclusionEnd = performance.now() + POLL.SPAN_VISIBLE.timeout;
    do {
      for (const row of refused) {
        expect(await probe.ch('SELECT id FROM spans FINAL WHERE trace_id={t:String}', { t: row.traceId })).toEqual([]);
        expect(await probe.ch('SELECT id FROM traces FINAL WHERE id={t:UUID}', { t: row.traceId })).toEqual([]);
      }
      if (performance.now() >= exclusionEnd) break;
      await delay(Math.min(500, exclusionEnd - performance.now()));
    } while (true);

    // The API is the one the SpanGrid calls; exhaust its fixed-size pages so the
    // assertion stays exact even if several exports won the pre-expiry race.
    const apiRows: { span_id: string; trace_id: string }[] = [];
    for (let pageNumber = 0; ; pageNumber++) {
      expect(pageNumber).toBeLessThan(20);
      const rows = await probe.apiList<{ span_id: string; trace_id: string }>(SPAN_LIST, {
        project_id: projectId, page_number: pageNumber, page_size: SPAN_PAGE_SIZE, filters: '[]',
      });
      apiRows.push(...rows);
      if (rows.length < SPAN_PAGE_SIZE) break;
    }
    expect(apiRows.map(row => ({ id: row.span_id, trace_id: row.trace_id }))
      .sort((a, b) => a.id.localeCompare(b.id)))
      .toEqual(expectedSpans.map(({ id, trace_id }) => ({ id, trace_id })));
    // Normal publication refuses immediately; the bounded fallback normally adds
    // at most 15 traces. Do not silently accept a truncated UI page if it does not.
    expect(acceptedIds.length, 'exact UI set must fit the actual 25-row trace page').toBeLessThanOrEqual(25);
    // LLMTracingView.jsx:handleGroupByChange pins this URL shape.
    const traceUrl = `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`;
    await test.step('Observe shows the exact accepted trace identities', async () => {
      await page.goto(traceUrl, { waitUntil: 'domcontentloaded', timeout: UI_READY });
      await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI_READY })
        .toEqual(acceptedIds.map(rootName).sort());
      // traceGridRowId.js: a project-pinned AG Grid row-id is the bare trace_id.
      await expect.poll(async () => traceNames.evaluateAll(cells => cells.map(cell =>
        cell.closest('[row-id]')?.getAttribute('row-id')).sort()), { timeout: UI_READY })
        .toEqual(acceptedIds);
    }, { timeout: UI_READY });
    await test.step('Reload preserves the accepted set and excludes every refused identity', async () => {
      await page.reload({ waitUntil: 'domcontentloaded', timeout: UI_READY });
      await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI_READY })
        .toEqual(acceptedIds.map(rootName).sort());
      // traceGridRowId.js: a project-pinned AG Grid row-id is the bare trace_id.
      await expect.poll(async () => traceNames.evaluateAll(cells => cells.map(cell =>
        cell.closest('[row-id]')?.getAttribute('row-id')).sort()), { timeout: UI_READY })
        .toEqual(acceptedIds);
    }, { timeout: UI_READY });
    expect(await spans()).toEqual(expectedSpans);
    expect((await traces()).map(row => row.id)).toEqual(acceptedIds);
  } finally {
    await testInfo.attach('export-identities-statuses-durations', { contentType: 'application/json',
      body: JSON.stringify(receipts) });
    await req.dispose();
  }
});
