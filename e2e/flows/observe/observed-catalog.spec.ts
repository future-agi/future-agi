import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

interface ValuePage {
  result: { values: { value: string | number | boolean; type: string }[] };
}

test('OBS-E2E-004: live observed attributes retain types and work in the filter UI', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-004', area: 'observe',
    userGoal: 'Discover and filter live attributes without activating a catalog revision',
    steps: ['ingest typed attributes using a newly provisioned API key',
      'wait for catalog values through the authenticated API',
      'search the property and value pickers', 'filter the span table'],
    backendChecks: ['real collector, Kafka consumer and catalog reader share the same scope',
      'numeric-looking strings remain distinct from numbers; booleans remain booleans',
      'the selected suggestion filters authoritative spans to the expected root'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000);
  const req = await request.newContext();
  try {
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-catalog-${suffix}`;
    const rootName = `catalog.alpha-${suffix}`;
    const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
      secretKey: actor.secretKey, projectName };
    const alpha = await sendTrace(req, { ...cfg, rootName,
      rootAttributes: { customer_id: '12345678', support_region: 'alpha-west', enabled: true } });
    const beta = await sendTrace(req, { ...cfg, rootName: `catalog.beta-${suffix}`,
      rootAttributes: { customer_id: 12345678, support_region: 'beta-east', enabled: false } });

    await expect.poll(async () => {
      const rows = await probe.ch<{ n: string }>(
        'SELECT count() AS n FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String})',
        { a: alpha.traceId, b: beta.traceId });
      return Number(rows[0].n);
    }, POLL.SPAN_VISIBLE).toBe(4);
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [projectName, actor.organizationId]);
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;
    const values = async (key: string) => (await actor.api.post<ValuePage>(
      '/tracer/dashboard/filter_values/', {
        property_id: `custom_attribute:${key}`, source: 'traces',
        project_ids: projectId, page_size: 25,
      })).result.values;

    await test.step('API: live values retain their types without manual activation', async () => {
      await expect.poll(async () => (await values('customer_id'))
        .map(({ value, type }) => `${type}:${JSON.stringify(value)}`).sort(),
      POLL.ASYNC_JOB).toEqual(['number:12345678', 'string:"12345678"']);
      await expect.poll(async () => (await values('enabled'))
        .map(({ value, type }) => `${type}:${value}`).sort(),
      POLL.ASYNC_JOB).toEqual(['boolean:false', 'boolean:true']);
    });

    await test.step('UI: search and select a live attribute suggestion', async () => {
      await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=spans`,
        { waitUntil: 'domcontentloaded' });
      const spanNames = page.locator('.clean-data-table:visible .ag-row [col-id="span_name"]');
      await expect(spanNames).toHaveCount(4, { timeout: 60_000 });
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      await page.getByPlaceholder('Search properties...').fill('support_region');
      await page.locator('[data-filter-property-option="support_region"]').click();
      await page.locator('[data-filter-value-trigger="support_region"]').click();
      await page.getByPlaceholder('Search values...').fill('alpha');
      await page.locator('[data-filter-value-option="alpha-west"]').click();
      await page.keyboard.press('Escape');
      await expect(spanNames).toHaveText([rootName], { timeout: 60_000 });
    });
  } finally {
    await req.dispose();
  }
});
