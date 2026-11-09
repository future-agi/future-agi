import { createHash } from 'node:crypto';
import { test, expect } from '../../lib/walk/fixtures';
import { E2E } from '../../lib/env';
import { gatewayProfile, PROFILE_SERVES_MCP } from '../../lib/profile';
import { flowAnnotation } from '../../lib/flow-meta';
import { orgConfigRows, zeroRowVerdicts } from '../../lib/walk/census';
import { JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP } from './publication-contract';

// Static skip is evaluated at collection, before scopeActors can seed or attach.
test.skip(!JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP);

// 708 add.38 adds these separate J00 legs; W00 is not duplicated.
// Product pins: bbaf95c973 frontend/src/routes/paths.js, mcp/tabs.js,
// mcp/virtual/VirtualServersTab.jsx, connectors/IntegrationsSection.jsx,
// guardrails/logs/TriggeredRequestsTab.jsx. No new product selectors.
const UI_READY = 60_000;
const OFF = 'MCP is turned off for this deployment';
const legs = [
  { id: 'L8', title: 'Virtual servers remain unconfigured', route: '/dashboard/gateway/mcp?tab=virtual' },
  { id: 'L9', title: 'Connectors have no configured connections', route: '/dashboard/gateway/integrations' },
  { id: 'L10', title: 'Guardrail history has no fabricated runs', route: '/dashboard/gateway/guardrails/logs' },
] as const;
type Status = { enabled: boolean; servers: unknown[]; tools: number; sessions: number; reason?: string };
type Catalog = { results: { connected: boolean; servers: unknown[] }[]; next: string | null; total_pages: number; count: number };
const fingerprint = (v: unknown) => `sha256:${createHash('sha256').update(JSON.stringify(v)).digest('hex')}`;
test.use({ trace: 'off', screenshot: 'off', video: 'off' });

for (const leg of legs) {
  const id = `JRN-E2E-000.${leg.id}`;
  test(`${id}: J00.${leg.id} ${leg.title}`, {
    tag: ['@flow', '@journey', '@journey-J00'],
    annotation: [flowAnnotation({ id, area: 'journeys', userGoal: `A fresh organization sees the honest default state: ${leg.title.toLowerCase()}`,
      steps: ['Read the fresh organization configuration and backing rows without enabling anything',
        `Open ${leg.route} and assert the exact empty or deployment-unavailable state`,
        'Read the same organization again; visiting the page must not create configuration or history'],
      backendChecks: ['No MCP servers, virtual servers or search configured; no credentials, connect state, approvals, tool calls or integration connections',
        leg.id === 'L10' ? 'Guardrail policy/config and request history tables are empty; history API is empty, or an unavailable route is 404'
          : 'MCP status has no enabled organization or servers; absent MCP profile explicitly reports gateway_mcp_disabled',
        leg.id === 'L9' ? 'Every catalogue page reports no connected or added app; MCP-off UI exposes no app or Connect action'
          : 'UI contains no fabricated data row; configuration and row counts unchanged after navigation'] }),
      { type: 'journey-flow-id', description: 'JRN-E2E-000' },
      { type: 'journey-contract', description: JSON.stringify({ journey: 'J00', leg: leg.id,
        amendment: '708 add.38', applies_when: 'always', absence: 'assert explicit unavailable state plus unchanged empty backend; never infer absence from an error' }) }],
  }, async ({ scopeActors: scopes, scopeProbe: probe, walk, request }, testInfo) => {
    // Two UI_READY waits (120s) plus fresh-actor/backend reads and navigation (60s).
    test.setTimeout(180_000);
    const actor = scopes.ownerA;
    await testInfo.attach('fresh-org', { contentType: 'application/json', body: JSON.stringify({ org: fingerprint(actor.organizationId) }) });
    const before = await orgConfigRows(probe, actor.organizationId);
    for (const row of before) {
      expect(Object.keys(row.mcp?.servers ?? {}), 'no configured MCP servers').toEqual([]);
      expect(Object.keys(row.mcp?.virtual_servers ?? {}), 'no configured virtual servers').toEqual([]);
      expect(row.mcp?.search, 'search remains absent').toBeUndefined();
    }
    const zeroRows = (await zeroRowVerdicts(probe, actor.organizationId)).filter(row =>
      ['mcp credentials', 'mcp oauth connect', 'mcp approvals', 'mcp tool calls', 'integrations exports'].includes(row.feature));
    expect(zeroRows.every(row => row.verdict === 'OFF' || row.verdict === 'ABSENT'), 'no configured connector rows').toBe(true);
    // GatewayViewSet.list: GeneralMethods success_response([virtual gateway]).
    const gateways = await scopes.send<{ result: { id: string }[] }>(actor, 'GET', '/agentcc/gateways/');
    expect(gateways.status, 'gateway list available').toBe(200);
    expect(gateways.body.result).toHaveLength(1);
    const gateway = gateways.body.result[0].id;
    const status = await scopes.send<{ result: Status }>(actor, 'GET', `/agentcc/gateways/${gateway}/mcp-status/`);
    expect(status.status).toBe(200);
    expect(status.body.result.enabled, 'fresh org MCP is disabled').toBe(false);
    expect(status.body.result.servers, 'fresh org server list is empty').toEqual([]);
    expect(status.body.result.tools).toBe(0);
    expect(status.body.result.sessions).toBe(0);
    const mcpAbsent = !PROFILE_SERVES_MCP[gatewayProfile()];
    if (mcpAbsent) expect(status.body.result.reason, 'explicit profile absence, not gateway outage').toBe('gateway_mcp_disabled');
    else expect(status.body.result.reason, 'served MCP has no outage masquerading as empty').toBeUndefined();

    if (leg.id === 'L9') {
      // ConnectorCatalogListResponseSerializer: top-level results, paginated.
      let total = 0;
      for (let page = 1; ; page++) {
        expect(page <= 50, 'bounded complete catalogue pagination').toBe(true);
        const catalog = await scopes.send<Catalog>(actor, 'GET', '/agentcc/connectors/catalog/', undefined, { limit: 200, page });
        expect(catalog.status, 'catalogue read remains available').toBe(200);
        for (const app of catalog.body.results) {
          expect(app.connected, 'no connected catalogue app').toBe(false);
          expect(app.servers, 'no added catalogue app').toEqual([]);
        }
        total += catalog.body.results.length;
        if (!catalog.body.next) { expect(total).toBe(catalog.body.count); break; }
        expect(page < catalog.body.total_pages, 'catalogue next page valid').toBe(true);
      }
    }
    let historyAbsent = false;
    if (leg.id === 'L10') {
      // models/org_config.py:35 stores guardrails as JSON, not a separate table.
      const configs = await probe.pg<{ guardrails: Record<string, unknown> }>(
        'SELECT guardrails FROM agentcc_org_config WHERE organization_id = $1 AND NOT deleted', [actor.organizationId]);
      for (const config of configs) expect(config.guardrails, 'no configured guardrail checks').toEqual({});
      // AgentccGuardrailPolicy and RequestLog Meta.db_table, scoped by org.
      for (const table of ['agentcc_guardrail_policy', 'agentcc_request_log']) {
        const [{ n }] = await probe.pg<{ n: string }>(`SELECT count(*)::text AS n FROM ${table} WHERE organization_id = $1`, [actor.organizationId]);
        expect(Number(n), 'fresh org guardrail/history row count').toBe(0);
      }
      // TriggeredRequestsTab uses this exact endpoint and query; no gateway_id.
      const response = await request.get(`${E2E.apiUrl}/agentcc/request-logs/`, { headers: scopes.headers(actor),
        params: { guardrail_triggered: true, limit: 100, page: 1, ordering: '-started_at' }, maxRedirects: 0 });
      historyAbsent = response.status() === 404;
      if (!historyAbsent) {
        expect(response.status(), 'history read is not an outage').toBe(200);
        const body = await response.json();
        expect(body.count, 'history total is zero').toBe(0);
        expect(body.results, 'history contains no runs').toEqual([]);
      }
    }
    const context = await walk.open(actor, `j00-${leg.id}`);
    const page = await context.newPage();
    await page.goto(leg.route, { waitUntil: 'domcontentloaded' });
    if (leg.id !== 'L10' && mcpAbsent) {
      await expect(page.getByText(OFF, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByRole('button', { name: /^(Connect|New virtual server|Custom MCP server)$/ })).toHaveCount(0);
      if (leg.id === 'L9') await expect(page.getByText("Apps connect through the gateway's MCP, so none can be connected here.", { exact: true })).toBeVisible({ timeout: UI_READY });
    } else if (leg.id === 'L8') {
      await expect(page.getByText('No virtual servers yet', { exact: true })).toBeVisible({ timeout: UI_READY });
    } else if (leg.id === 'L9') {
      // A served catalogue may list available apps, but Connected is exactly empty.
      await page.getByRole('button', { name: /^Connected/ }).click();
      await expect(page.getByText('No connected apps yet', { exact: true })).toBeVisible({ timeout: UI_READY });
    } else if (historyAbsent) {
      await expect(page, 'unserved history UI is unavailable').toHaveURL(/\/404\/?$/, { timeout: UI_READY });
    } else {
      await expect(page.getByText('No request has matched a rule yet', { exact: true })).toBeVisible({ timeout: UI_READY });
    }
    // GatewayGrid (AG Grid): only data rows, not headers or skeleton rows.
    await expect(page.locator('.ag-center-cols-container .ag-row'), 'no fabricated rows').toHaveCount(0);
    expect(fingerprint(await orgConfigRows(probe, actor.organizationId)), 'page does not configure the org').toBe(fingerprint(before));
    const after = (await zeroRowVerdicts(probe, actor.organizationId)).filter(row => zeroRows.some(prior => prior.feature === row.feature));
    expect(after, 'page does not create connection state').toEqual(zeroRows);
    if (leg.id === 'L10') {
      const configs = await probe.pg<{ guardrails: Record<string, unknown> }>(
        'SELECT guardrails FROM agentcc_org_config WHERE organization_id = $1 AND NOT deleted', [actor.organizationId]);
      for (const config of configs) expect(config.guardrails, 'page does not configure guardrails').toEqual({});
      for (const table of ['agentcc_guardrail_policy', 'agentcc_request_log']) {
        const [{ n }] = await probe.pg<{ n: string }>(`SELECT count(*)::text AS n FROM ${table} WHERE organization_id = $1`, [actor.organizationId]);
        expect(Number(n), 'page does not fabricate guardrail history or policies').toBe(0);
      }
    }
    await testInfo.attach('surface-census', { contentType: 'application/json', body: JSON.stringify({
      leg: leg.id, org: fingerprint(actor.organizationId), state: (leg.id === 'L10' ? historyAbsent : mcpAbsent) ? 'ABSENT' : 'OFF', unchanged: true }) });
  });
}
