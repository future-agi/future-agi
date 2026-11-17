import { test, expect } from '../../lib/existing-customer';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedAgentListOrder } from '../../lib/simulate-seed';

// Runs as the existing customer, like OBS-E2E-034: the seed builds Agent
// Definitions through the ORM, and that org keeps every Simulate page.
// README first-paint budget.
const UI_READY = 60_000;
// frontend/src/api/agents/connectedAgents.js: the Connected agents tab reads this list.
const AGENT_LIST = '/simulate/agent-definitions/';
// frontend/src/sections/agent-playground/agentsTabs.js CONNECTED_AGENTS_PATH.
const CONNECTED_AGENTS = '/dashboard/agents/connected';

type AgentRow = { id: string; agent_name: string };
type AgentList = { results: AgentRow[] };

test('AGENT-E2E-002: an agent used after a newer one was created lists above it on Connected agents', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AGENT-E2E-002', area: 'agents',
    userGoal: 'A developer who reuses an older agent sees it at the top of Connected agents, not buried behind newer ones',
    steps: ['seed two Agent Definitions with one version each, then give the older one the later updated_at that a reuse writes (no hosted-agent infra runs in this harness - see e2e/lib/simulate-seed.ts)',
            'open Connected agents and search for the seeded names',
            'see the older agent listed before the newer one'],
    backendChecks: ['the seeded older definition has an earlier created_at and a later updated_at than the newer one, both scoped to the actor org',
                    'GET /simulate/agent-definitions/ returns the older definition before the newer one, matching the table'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(120_000);
  const seed = seedAgentListOrder({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('seeded-agents', { contentType: 'application/json', body: JSON.stringify(seed) });
  // Both names end in the same per-seed token, which narrows the list to this flow's two rows.
  const token = seed.olderName.replace('e2e-agent-order-older-', '');

  // Storage lane: the order the list should show is the updated_at order, not the creation order.
  const rows = await probe.pg<{ id: string; organization_id: string; created_at: Date; updated_at: Date }>(
    'SELECT id, organization_id, created_at, updated_at FROM simulate_agent_definition WHERE id = ANY($1::uuid[])',
    [[seed.olderId, seed.newerId]],
  );
  const older = rows.find((r) => r.id === seed.olderId)!;
  const newer = rows.find((r) => r.id === seed.newerId)!;
  expect(rows).toHaveLength(2);
  expect(older.organization_id).toBe(actor.organizationId);
  expect(newer.organization_id).toBe(actor.organizationId);
  expect(older.created_at.getTime()).toBeLessThan(newer.created_at.getTime());
  expect(older.updated_at.getTime()).toBeGreaterThan(newer.updated_at.getTime());

  // UI lane.
  await page.goto(CONNECTED_AGENTS);
  await page.getByPlaceholder('Search').fill(token);
  const names = page.getByText(new RegExp(`^e2e-agent-order-(older|newer)-${token}$`));
  await expect(names).toHaveText([seed.olderName, seed.newerName], { timeout: UI_READY });

  // API lane: the same list endpoint the table calls.
  const list = await actor.api.get<AgentList>(AGENT_LIST, { search: token, limit: 25 });
  expect(list.results.map((a) => a.agent_name)).toEqual([seed.olderName, seed.newerName]);
});
