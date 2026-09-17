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
  result: { tools: { name: string }[] };
}

test('SET-E2E-001: admin restricts MCP tool groups for connected clients', {
  tag: ['@flow', '@smoke'],
  annotation: flowAnnotation({
    id: 'SET-E2E-001', area: 'settings',
    userGoal: 'An admin restricts which MCP tool groups are available to connected clients',
    steps: ['open Settings MCP Server', 'expand Tool Groups',
            'turn off Datasets & Knowledge Bases', 'save the selection'],
    backendChecks: [
      'GET /mcp/config/tool-groups/ omits datasets from enabled_groups',
      'PG mcp_server_mcptoolgroupconfig.enabled_groups matches that selection for the actor org',
      'GET /mcp/internal/tools/ still lists whoami and no longer lists list_datasets',
      'POST /mcp/internal/tool-call/ list_datasets returns 403 while whoami still succeeds',
    ],
  }),
}, async ({ page, actor, probe }) => {
  // Navigation + accordion + save + 2 × UI_READY can outrun the 120s default
  // on a loaded stack; fail on the assertion that ran out instead.
  test.setTimeout(240_000);

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
    await expect(datasetsRow.getByRole('checkbox')).not.toBeChecked();

    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByText('Tool groups updated successfully'))
      .toBeVisible({ timeout: UI_READY });
  });

  await test.step('API: tool-groups and tool list match the saved selection', async () => {
    const groups = await actor.api.get<ToolGroupsEnvelope>(TOOL_GROUPS_PATH);
    expect(groups.status).toBe(true);
    expect(groups.result.enabled_groups).toContain('context');
    expect(groups.result.enabled_groups).not.toContain('datasets');

    const listed = await actor.api.get<ToolListEnvelope>(TOOL_LIST_PATH);
    const names = listed.result.tools.map((tool) => tool.name);
    expect(names).toContain('whoami');
    expect(names).not.toContain('list_datasets');
  });

  await test.step('storage: PG row for the actor org dropped datasets', async () => {
    const rows = await probe.pg<{ enabled_groups: string[] }>(
      `SELECT c.enabled_groups
         FROM mcp_server_mcptoolgroupconfig c
         JOIN mcp_server_mcpconnection conn ON c.connection_id = conn.id
        WHERE conn.organization_id = $1
          AND conn.deleted = false
          AND c.deleted = false`,
      [actor.organizationId],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].enabled_groups).toContain('context');
    expect(rows[0].enabled_groups).not.toContain('datasets');
  });

  await test.step('API: disabled group is 403; context tools still run', async () => {
    const whoami = await actor.api.post<{ status: boolean }>(
      TOOL_CALL_PATH, { tool_name: 'whoami', params: {} },
    );
    expect(whoami.status).toBe(true);

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
