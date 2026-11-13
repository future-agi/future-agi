import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// agent_playground/serializers/{graph,node,node_template}.py and frontend/utils/axios.js.
const GRAPHS = '/agent-playground/graphs/';
const TEMPLATES = '/agent-playground/node-templates/';
// e2e/README.md: first-paint budget, not a model or CDC wait.
const UI_READY = 60_000;
type Graph = { result: { id: string; name: string; active_version: {
  id: string; status: string; nodes: Array<{ id: string; name: string }>;
} } };

test('AGENT-E2E-002: node editor actions are named and remain guarded for viewers', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AGENT-E2E-002', area: 'agents',
    userGoal: 'Identify node deletion and editor close actions by hover or keyboard without allowing a viewer to delete the saved draft.',
    steps: [
      'seed one uniquely named draft and prompt node through the public API',
      'hover the canvas delete action, then reveal it using Tab with the pointer away',
      'open node settings, inspect the Close tooltip and activate close with the keyboard',
      'inspect the same draft as a viewer and attempt Delete and Backspace without deletion controls',
      'confirm owner deletion from the drawer of the existing draft',
    ],
    backendChecks: [
      'the graph detail retains the exact seeded node after owner inspection and viewer keyboard deletion attempts',
      'confirmed owner deletion addresses the saved version and leaves its node set empty',
    ],
  }),
}, async ({ browser, scopeActors }, testInfo) => {
  // 8 * UI_READY + 120s provisioning/API headroom.
  test.setTimeout(600_000);
  const { ownerA, viewer } = scopeActors;
  const name = `e2e-agent-editor-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const graph = (await ownerA.api.post<Graph>(GRAPHS, { name })).result;
  const versionId = graph.active_version.id;
  expect(graph.active_version.status).toBe('draft');
  const templates = await ownerA.api.get<{ result: { node_templates: Array<{ id: string; name: string }> } }>(TEMPLATES);
  // seed_node_templates.py ships llm_prompt; only configure a node, never execute a model.
  const template = templates.result.node_templates.find(item => item.name === 'llm_prompt');
  expect(template).toBeDefined();
  const nodeId = randomUUID();
  const nodeName = `${name}-node`;
  const versionPath = `${GRAPHS}${graph.id}/versions/${versionId}/`;
  const nodePath = `${versionPath}nodes/${nodeId}/`;
  await ownerA.api.post(`${versionPath}nodes/`, {
    id: nodeId, type: 'atomic', name: nodeName,
    node_template_id: template!.id, position: { x: 300, y: 120 },
  });
  await testInfo.attach('seeded-draft', { contentType: 'application/json',
    body: JSON.stringify({ graphId: graph.id, versionId, nodeId, nodeName }) });
  // frontend/routes/sections/dashboard.jsx: AgentPlayground > AgentBuilder.
  const route = `/dashboard/agents/playground/${graph.id}/build`;
  const ownerContext = await scopeActors.openContext(browser, ownerA);
  const page = await ownerContext.newPage();
  page.setDefaultTimeout(UI_READY);
  const canvasNode = page.locator(`.react-flow__node[data-id="${nodeId}"]`);
  const deleteButton = page.getByRole('button', { name: `Delete canvas node: ${nodeName}`, exact: true });

  await test.step('hover the named canvas delete action', async () => {
    await page.goto(route);
    await expect(canvasNode).toBeVisible({ timeout: UI_READY });
    await canvasNode.hover();
    await deleteButton.hover();
    await expect(page.getByRole('tooltip')).toHaveText('Delete node', { timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('Tab reveals delete without a mouse hover', async () => {
    await page.mouse.move(0, 0);
    // React Flow's node wrapper is keyboard focusable; Tab reaches its native button.
    await canvasNode.focus();
    await page.keyboard.press('Tab');
    await expect(deleteButton).toBeFocused({ timeout: UI_READY });
    await expect(deleteButton).toHaveCSS('opacity', '1', { timeout: UI_READY });
    await expect(page.getByRole('tooltip')).toHaveText('Delete node', { timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('close the inspected drawer using the keyboard', async () => {
    await page.keyboard.press('Escape');
    await canvasNode.getByText(nodeName, { exact: true }).click();
    const close = page.getByRole('button', { name: 'Close node editor', exact: true });
    await close.hover();
    await expect(page.getByRole('tooltip', { name: 'Close', exact: true })).toHaveText('Close', { timeout: UI_READY });
    await page.mouse.move(0, 0);
    await page.getByRole('button', { name: 'Delete node', exact: true }).focus();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused({ timeout: UI_READY });
    await page.keyboard.press('Enter');
    await expect(close).toHaveCount(0, { timeout: UI_READY });
  }, { timeout: UI_READY });

  const viewerContext = await scopeActors.openContext(browser, viewer);
  const readOnlyPage = await viewerContext.newPage();
  readOnlyPage.setDefaultTimeout(UI_READY);
  await test.step('viewer cannot delete by hover or keyboard', async () => {
    await readOnlyPage.goto(route);
    const node = readOnlyPage.locator(`.react-flow__node[data-id="${nodeId}"]`);
    await expect(node).toBeVisible({ timeout: UI_READY });
    await node.hover();
    await expect(readOnlyPage.getByRole('button', { name: /Delete canvas node:/ })).toHaveCount(0, { timeout: UI_READY });
    await node.getByText(nodeName, { exact: true }).click();
    await expect(readOnlyPage.getByRole('button', { name: 'Close node editor' })).toBeVisible({ timeout: UI_READY });
    await expect(readOnlyPage.getByRole('button', { name: 'Delete node', exact: true })).toHaveCount(0, { timeout: UI_READY });
    await node.focus();
    await readOnlyPage.keyboard.press('Delete');
    await readOnlyPage.keyboard.press('Backspace');
    await expect(node).toBeVisible({ timeout: UI_READY });
    await expect(readOnlyPage.getByRole('dialog')).toHaveCount(0, { timeout: UI_READY });
  }, { timeout: UI_READY });
  const retained = (await ownerA.api.get<Graph>(`${GRAPHS}${graph.id}/`)).result;
  expect(retained.active_version.id).toBe(versionId);
  expect(retained.active_version.nodes.map(node => ({ id: node.id, name: node.name })))
    .toEqual([{ id: nodeId, name: nodeName }]);

  await test.step('owner confirms deletion against the saved version', async () => {
    await canvasNode.getByText(nodeName, { exact: true }).click();
    await page.getByRole('button', { name: 'Delete node', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Delete Node', exact: true });
    await expect(dialog).toBeVisible({ timeout: UI_READY });
    const [response] = await Promise.all([
      page.waitForResponse(r => new URL(r.url()).pathname === nodePath && r.request().method() === 'DELETE', { timeout: UI_READY }),
      dialog.getByRole('button', { name: 'Delete', exact: true }).click(),
    ]);
    expect(response.ok()).toBe(true);
    await expect(canvasNode).toHaveCount(0, { timeout: UI_READY });
  }, { timeout: UI_READY });
  const deleted = (await ownerA.api.get<Graph>(`${GRAPHS}${graph.id}/`)).result;
  expect(deleted.active_version.id).toBe(versionId);
  expect(deleted.active_version.nodes).toEqual([]);
});
