import { request } from '@playwright/test';
import { test, expect } from '../lib/fixtures';
import { sendTrace } from '../lib/otlp';
import { POLL } from '../lib/state-probe';
import { E2E } from '../lib/env';

test('seeded OTLP trace lands in ClickHouse under the actor org', async ({ actor, probe }, testInfo) => {
  const req = await request.newContext();
  const projectName = `e2e-${testInfo.testId}`;
  const seeded = await sendTrace(req, {
    collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
  });

  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);

  const spans = await probe.ch<{ id: string; org_id: string }>(
    'SELECT id, org_id FROM spans FINAL WHERE trace_id = {t:String} ORDER BY id', { t: seeded.traceId });
  expect(spans.map(s => s.id)).toEqual([...seeded.spanIds].sort());
  expect(new Set(spans.map(s => s.org_id))).toEqual(new Set([actor.organizationId]));

  // Collector auto-created the project in PG for the actor's org.
  const projects = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
    [projectName, actor.organizationId]);
  expect(projects).toHaveLength(1);
  await req.dispose();
});
