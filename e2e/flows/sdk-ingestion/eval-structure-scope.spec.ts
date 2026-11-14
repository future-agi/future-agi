import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// sdk/urls.py: GetEvalStructureEvalIdView; APIKeyAuthentication uses this header pair.
const SDK_EVAL = '/sdk/api/v1/eval/';
// model_hub/serializers/contracts.py: EvalTemplateCreateV2RequestSerializer.
const CREATE = '/model-hub/eval-templates/create-v2/';
// frontend/src/sections/evals/components/EvalsListView.jsx row navigation.
const DETAIL = '/dashboard/evaluations/';
const UI_READY = 60_000;
interface Template { id: string; eval_id: number; name: string; criteria: string;
  organization_id: string; workspace_id: string }

test('SDK-E2E-001: SDK returns a saved evaluator structure only in its own workspace', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SDK-E2E-001', area: 'sdk-ingestion',
    userGoal: 'Inspect a saved legacy-numeric evaluator in the browser and retrieve its structure with API credentials, while evaluators of other organizations and workspaces are reported as not found',
    steps: ['create one evaluator in each of two isolated organizations and assign distinct legacy identities through the separate test-image fixture',
      'open the owner\'s evaluator detail and inspect its exact name and instructions',
      'retrieve the same evaluator using the owner API credentials',
      'check that other-organization, sibling-workspace, malformed and nonexistent evaluator IDs return the same not-found response and that anonymous requests are refused',
      'retrieve the other organization\'s evaluator with that organization\'s own credentials'],
    backendChecks: [
      'the created evaluator identity and organization/workspace are stored in Postgres and its name is visible in the owner browser',
      'the owner API credentials retrieve the exact saved evaluator ID, name and criteria',
      'other-organization and nonexistent evaluator requests get the identical not-found response while the other organization still retrieves its own evaluator',
    ],
  }),
}, async ({ browser, request, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(240_000); // 120s public setup + 60s UI_READY + 60s HTTP/assertion headroom.
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-sdk-scope-${suffix}-own`;
  const otherName = `e2e-sdk-scope-${suffix}-other`;
  const instructions = `Verify ${name} for {{output}}.`;
  const ids: Record<string, string> = {};
  const receipts: { target: string; status: number }[] = [];
  // These writes save definitions only; no endpoint here starts an evaluation/model call.
  for (const [label, actor, evalName] of [
    ['own', scopes.ownerA, name], ['other', scopes.ownerB, otherName],
    ['sibling', scopes.withWorkspace(scopes.ownerA, scopes.emptyWorkspace.id), `${name}-sibling`],
  ] as const) {
    const created = await actor.api.post<{ result: { id: string } }>(CREATE, {
      name: evalName, eval_type: 'llm', model: 'turing_large', output_type: 'pass_fail',
      instructions: label === 'own' ? instructions : `Verify ${otherName} for {{output}}.`,
      pass_threshold: 0.5, check_internet: false, error_localizer_enabled: false,
      template_format: 'mustache',
    });
    ids[label] = created.result.id;
    // Separate test-image-only harness extension models migrated legacy IDs.
    // create-v2 itself returns UUIDs and leaves this old integer key at zero.
    const legacy = await actor.api.post<{ id: string; eval_id: number }>(
      '/sdk/e2e/legacy-eval/', { template_id: ids[label] });
    expect(legacy.id).toBe(ids[label]);
    expect(legacy.eval_id).toBeGreaterThan(1000000000);
  }
  await testInfo.attach('seeded-evaluators', { contentType: 'application/json',
    body: JSON.stringify({ ids, name, otherName, ownOrganization: scopes.ownerA.organizationId,
      otherOrganization: scopes.ownerB.organizationId }) });
  const context = await scopes.openContext(browser, scopes.ownerA);
  const page = await context.newPage();
  try {
    const rows = await probe.pg<Template>(
      'SELECT id,eval_id,name,criteria,organization_id,workspace_id FROM model_hub_evaltemplate WHERE id IN ($1,$2,$3)',
      [ids.own, ids.other, ids.sibling]);
    expect(rows).toHaveLength(3);
    await testInfo.attach('stored-evaluator-identities', { contentType: 'application/json', body: JSON.stringify(rows) });
    const own = rows.find(row => row.id === ids.own)!;
    const other = rows.find(row => row.id === ids.other)!;
    const sibling = rows.find(row => row.id === ids.sibling)!;
    await test.step('storage and UI: owner scope and visible saved definition', async () => {
      expect(own).toMatchObject({ id: ids.own, name, organization_id: scopes.ownerA.organizationId,
        workspace_id: scopes.ownerA.workspaceId, criteria: instructions });
      expect(other.organization_id).toBe(scopes.ownerB.organizationId);
      await page.goto(`${DETAIL}${ids.own}`, { waitUntil: 'domcontentloaded' });
      await expect(page.getByText(name, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      // EvalDetailPage.jsx renders LLM instructions in the existing Quill editor.
      await expect(page.locator('.ql-editor').first()).toHaveText(instructions, { timeout: UI_READY });
      await testInfo.attach('owner-evaluator', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
    });
    const read = async (label: string, evalId: number | string, ownerIndex: number) => {
      const owner = scopes.owners[ownerIndex];
      const response = await request.get(`${E2E.apiUrl}${SDK_EVAL}${encodeURIComponent(evalId)}/`, {
        // Explicit supported scope: the shared fixture creates a second workspace.
        // Its separately documented implicit-preference fallback is not this flow's claim.
        headers: { 'X-Api-Key': owner.apiKey, 'X-Secret-Key': owner.secretKey,
          'X-Organization-Id': owner.organizationId, 'X-Workspace-Id': owner.workspaceId },
        maxRedirects: 0,
      });
      receipts.push({ target: label, status: response.status() });
      return { status: response.status(), body: await response.json() };
    };
    await test.step('SDK golden path: exact own structure', async () => {
      const response = await read('own', own.eval_id, 0);
      expect(response.status).toBe(200);
      expect(response.body.result).toMatchObject({ id: own.id, name, criteria: instructions,
        organization: scopes.ownerA.organizationId });
    });
    await test.step('SDK lookup: other-organization ID matches missing, its own organization still succeeds', async () => {
      // EvalTemplate.eval_id is the legacy integer lookup key, not its UUID PK.
      const missing = await read('missing', -2147483647, 0);
      const denied = await read('other', other.eval_id, 0);
      const otherOwn = await read('other-org-own-credentials', other.eval_id, 1);
      expect(missing.status).toBe(400);
      expect.soft(denied.status).toBe(missing.status);
      expect.soft(denied.body).toEqual(missing.body);
      for (const [label, evalId] of [['sibling', sibling.eval_id], ['malformed', 'not-an-integer'],
        ['decimal', '1.5'], ['overflow', '9'.repeat(100)]] as const) {
        const invalid = await read(label, evalId, 0);
        expect(invalid).toEqual(missing);
      }
      const anonymous = await request.get(`${E2E.apiUrl}${SDK_EVAL}${own.eval_id}/`, { maxRedirects: 0 });
      expect(anonymous.status()).toBe(401);
      expect(await anonymous.text()).not.toContain(instructions);
      expect(otherOwn.status).toBe(200);
      expect(otherOwn.body.result).toMatchObject({ id: other.id, name: otherName,
        organization: scopes.ownerB.organizationId });
    });
  } finally {
    await testInfo.attach('credential-free-http-statuses', { contentType: 'application/json', body: JSON.stringify(receipts) });
    await context.close();
  }
});
