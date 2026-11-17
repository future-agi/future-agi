import { test, expect } from '../../lib/fixtures';
import { ApiError } from '../../lib/api-client';
import { flowAnnotation } from '../../lib/flow-meta';

const UI_READY = 60_000;
const TOOL_GROUPS_PATH = '/mcp/config/tool-groups/';
const TOOL_LIST_PATH = '/mcp/internal/tools/';
const TOOL_CALL_PATH = '/mcp/internal/tool-call/';

interface ToolGroupsEnvelope {
  status: boolean;
  result: { enabled_groups: string[] };
}
interface ToolListEnvelope {
  status: boolean;
  result: { tools: { name: string; category: string }[] };
}

test('SET-E2E-001: admin restricts MCP tool groups for connected clients', {
  tag: ['@flow', '@smoke'],
  annotation: flowAnnotation({
    id: 'SET-E2E-001', area: 'settings',
    userGoal: 'An admin restricts which MCP tool groups are available to connected clients',
    steps: ['open Settings MCP Server', 'expand Tool Groups',
            'turn off Datasets & Knowledge Bases', 'save the selection'],
    backendChecks: [
      'GET /mcp/config/tool-groups/ equals the initial group set minus datasets',
      'PG mcp_server_mcptoolgroupconfig.enabled_groups equals that exact set for the actor connection',
      'GET /mcp/internal/tools/ equals the initial tool set minus all dataset tools',
      'POST /mcp/internal/tool-call/ list_datasets returns 403 while whoami and list_projects still succeed',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Navigation + accordion + save + 2 × UI_READY can outrun the 120s default
  // on a loaded stack; fail on the assertion that ran out instead.
  test.setTimeout(240_000);

  const config = await actor.api.get<{
    result: { id: string };
  }>('/mcp/config/');
  const initialGroups = await actor.api.get<ToolGroupsEnvelope>(TOOL_GROUPS_PATH);
  const initialTools = await actor.api.get<ToolListEnvelope>(TOOL_LIST_PATH);
  expect(initialGroups.result.enabled_groups).toContain('datasets');
  expect(initialGroups.result.enabled_groups).toContain('observability');
  const expectedGroups = initialGroups.result.enabled_groups
    .filter((group) => group !== 'datasets').sort();
  const expectedTools = initialTools.result.tools
    .filter((tool) => tool.category !== 'datasets').map((tool) => tool.name).sort();
  expect(initialTools.result.tools.find((tool) => tool.name === 'list_datasets')?.category)
    .toBe('datasets');
  expect(expectedTools).toContain('list_projects');
  await testInfo.attach('mcp-selection', {
    body: JSON.stringify({ connectionId: config.result.id, organizationId: actor.organizationId,
      initialGroups: initialGroups.result.enabled_groups, expectedGroups, expectedTools }),
    contentType: 'application/json',
  });

  await test.step('UI: open MCP Server and restrict the datasets group', async () => {
    await page.goto('/dashboard/settings/mcp-server', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('MCP Server', { exact: true }).first())
      .toBeVisible({ timeout: UI_READY });

    await page.getByRole('button', { name: /Tool Groups/i }).click();
    const datasetsRow = page.locator('div').filter({
      hasText: /^Datasets & Knowledge Bases$/,
    }).filter({ has: page.getByRole('checkbox') });
    await expect(datasetsRow.getByRole('checkbox')).toBeChecked({ timeout: UI_READY });
    await datasetsRow.getByRole('checkbox').click();
    await expect(datasetsRow.getByRole('checkbox')).not.toBeChecked({ timeout: UI_READY });

    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByText('Tool groups updated successfully'))
      .toBeVisible({ timeout: UI_READY });
  });

  await test.step('API: tool-groups and tool list match the saved selection', async () => {
    const groups = await actor.api.get<ToolGroupsEnvelope>(TOOL_GROUPS_PATH);
    expect(groups.status).toBe(true);
    expect([...groups.result.enabled_groups].sort()).toEqual(expectedGroups);

    const listed = await actor.api.get<ToolListEnvelope>(TOOL_LIST_PATH);
    const names = listed.result.tools.map((tool) => tool.name);
    expect(listed.status).toBe(true);
    expect(names.sort()).toEqual(expectedTools);
  });

  await test.step('storage: PG preserves every unaffected group for the actor connection', async () => {
    const rows = await probe.pg<{ enabled_groups: string[] }>(
      `SELECT c.enabled_groups
         FROM mcp_server_mcptoolgroupconfig c
         JOIN mcp_server_mcpconnection conn ON c.connection_id = conn.id
        WHERE conn.organization_id = $1
          AND conn.id = $2
          AND conn.deleted = false
          AND c.deleted = false`,
      [actor.organizationId, config.result.id],
    );
    expect(rows).toHaveLength(1);
    expect([...rows[0].enabled_groups].sort()).toEqual(expectedGroups);
  });

  await test.step('API: disabled group is 403; unaffected tools still run', async () => {
    for (const tool_name of ['whoami', 'list_projects']) {
      const result = await actor.api.post<{ status: boolean }>(
        TOOL_CALL_PATH, { tool_name, params: {} },
      );
      expect(result.status).toBe(true);
    }

    let disabled: unknown;
    try {
      await actor.api.post(
        TOOL_CALL_PATH, { tool_name: 'list_datasets', params: {} },
      );
    } catch (err) {
      disabled = err;
    }
    expect(disabled).toBeInstanceOf(ApiError);
    expect((disabled as ApiError).status).toBe(403);
  });
});
