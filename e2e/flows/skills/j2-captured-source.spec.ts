import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';

const requestText = 'Synthetic customer requests a refund for order R-204. Check eligibility and use the refund tool.';
const toolText = 'Synthetic order R-204 is paid, refundable and has no chargeback.';
const followupText = 'Correction: do not issue the refund yet; provide eligibility only.';
const followupToolText = 'Eligibility confirmed; no refund has been issued.';
const capturedPrompt = `${requestText}\n\n${followupText}`;
const BASE = '/model-hub/skills/contexts/';
function assertCapture(row: any, expected: any) {
  expect(row.trace_ref).toMatchObject({ project_id: expected.projectId, trace_id: expected.traceId, span_id: expected.spanId });
  expect(row.trace_ref.start_time).toContain('.123456');
  expect(row.source_hashes.physical).toMatchObject({ project_id: expected.projectId, trace_id: expected.traceId, span_id: expected.spanId });
  expect(row.selected_text).toBeNull();
  expect(row.accepted_receipt.case_id).toBe(expected.caseId);
  expect(row.recorded_version_id).toBe(expected.versionId);
}
function assertCase(suite: any) {
  expect(suite.revision).toBe(1);
  expect(suite.cases).toHaveLength(1);
  expect(suite.cases[0]).toMatchObject({ prompt: capturedPrompt, reviewed: true, expected_trigger: null });
  expect(suite.cases[0].fixtures.map((fixture: any) => fixture.text)).toEqual([toolText, followupToolText]);
  expect(suite.cases[0].source_group_id).toMatch(/^trace:sha256:/);
  expect(JSON.stringify(suite)).not.toContain('Hidden system');
  expect(JSON.stringify(suite)).not.toContain('False refund success');
}

test('SKILLS-E2E-021: captured span identity and inputs reach context Tests without reconstruction', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-021', area: 'skills',
    userGoal: 'An incident responder carries an observed failure into a reviewed context case without retyping physical identities or captured request/tool text',
    steps: ['open a synthetic failure in Observe', 'open the review in the context the span was delivered from, with every supported segment preselected', 'review selected request and tool result; confirm expected behavior and workspace sharing', 'inspect the saved case on the published context Tests section'],
    backendChecks: ['PostgreSQL retains the exact physical span and recorded context identity while purging preview text after acceptance', 'the public context suite preserves request and tool chronology even when selected in reverse; excludes system and assistant text; the leaf suite route still refuses the context'],
  }),
}, async ({ page, actor, probe, request }, testInfo) => {
  test.setTimeout(360_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), actor.organizationId);
  page.setDefaultTimeout(60_000);
  const headers = { Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId };
  const read = async (path: string) => (await actor.api.get<{ result: any }>(path)).result;
  const post = async (path: string, data: unknown) => {
    const response = await request.post(`${E2E.apiUrl}${path}`, { headers: { ...headers, 'Idempotency-Key': randomUUID() }, data });
    expect(response.ok(), `fixture setup ${path}: ${await response.text()}`).toBe(true);
    return (await response.json()).result;
  };
  const name = `j2-capture-${Date.now().toString(36)}`;
  const created = await post(BASE, { name, description: 'Synthetic capture fixture, not native qualification', tags: [], files: [{ path: 'AGENTS.md', content: '# Refund policy\nConfirm the business receipt before reporting a refund.\n' }] });
  const root = `${BASE}${created.context_id}/`;
  const draft = await read(`${root}draft/`);
  const review = await post(`${root}draft/head-review/`, { revision: draft.revision });
  const ack = await post(`${root}draft/acknowledge-head/`, { revision: draft.revision, expected_digest: draft.digest, expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint });
  const publication = await post(`${root}draft/publish/`, { revision: ack.revision, expected_digest: draft.digest, base_version: draft.base_version, expected_head_version: review.expected_head_version, review_fingerprint: ack.review_fingerprint, note: 'Synthetic baseline', acknowledge_warnings: true });
  const version = publication.version;
  const projectName = `j2-retained-failure-${Date.now().toString(36)}`;
  const start = BigInt(Math.floor(Date.now() / 1000)) * 1_000_000_000n + 123456000n;
  const seeded = await sendTrace(request, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName, rootName: 'Synthetic refund failure', startTimeUnixNano: start,
    rootAttributes: { 'input.value': JSON.stringify({ messages: [{ role: 'system', content: 'Hidden system' }, { role: 'user', content: requestText }, { role: 'tool', content: toolText }, { role: 'user', content: followupText }, { role: 'tool', content: followupToolText }, { role: 'assistant', content: 'False refund success' }] }), 'output.value': 'False refund success', 'fi.context.id': created.context_id, 'fi.context.version_id': version.id, 'fi.context.digest': version.digest },
  });
  await expect.poll(async () => (await probe.ch<{ n: string }>('SELECT count() AS n FROM spans FINAL WHERE trace_id={t:String}', { t: seeded.traceId }))[0].n, POLL.SPAN_VISIBLE).toBe('2');
  const projects = await probe.pg<{ id: string }>('SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2', [projectName, actor.organizationId]);
  expect(projects).toHaveLength(1);
  await page.goto('/dashboard/observe');
  await page.getByText(projectName, { exact: true }).click();
  await page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Synthetic refund failure' }).click();
  await page.getByRole('button', { name: 'Save as test case', exact: true }).click();
  // The span names the context it was delivered from, so the review opens in it with no context choice,
  // and every supported request and tool-result segment starts selected.
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('checkbox')).toHaveCount(4);
  for (const box of await dialog.getByRole('checkbox').all()) await expect(box).toBeChecked();
  await expect(dialog.getByRole('textbox', { name: /Source project ID|Source trace ID|Source span ID|Span start time/ })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('J2-captured-identity.png'), animations: 'disabled' });
  // Selection order must not invert the later correction or tool chronology.
  for (const index of [0, 1, 2, 3]) await dialog.getByRole('checkbox').nth(index).uncheck();
  for (const index of [3, 2, 1, 0]) await dialog.getByRole('checkbox').nth(index).check();
  await dialog.getByRole('button', { name: 'Preview selected text', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: 'Redacted request', exact: true })).toHaveValue(capturedPrompt);
  await expect(dialog.getByRole('textbox', { name: 'Redacted tool result 1', exact: true })).toHaveValue(toolText);
  await expect(dialog.getByRole('textbox', { name: 'Redacted tool result 2', exact: true })).toHaveValue(followupToolText);
  await dialog.getByRole('textbox', { name: 'Case name', exact: true }).fill('Reviewed refund boundary');
  await dialog.getByRole('textbox', { name: 'Expected answer must contain', exact: true }).fill('receipt');
  await dialog.getByRole('checkbox', { name: /confirmed the expected behavior/ }).check();
  await dialog.getByRole('checkbox', { name: /workspace/ }).check();
  await page.screenshot({ path: testInfo.outputPath('J2-redaction-review.png'), animations: 'disabled' });
  const accepted = page.waitForResponse(res => res.url().includes('/captures/') && res.url().endsWith('/accept/') && res.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Save test case', exact: true }).click();
  expect((await accepted).ok()).toBe(true);
  const suite = await read(`${root}tests/`);
  assertCase(suite);
  expect(() => assertCase({ ...suite, cases: [{ ...suite.cases[0], prompt: 'reconstructed wrong request' }] })).toThrow();
  const rows = await probe.pg('SELECT trace_ref, source_hashes, selected_text, accepted_receipt, recorded_version_id FROM model_hub_hubskillcontextcapture WHERE context_id=$1 AND workspace_id=$2', [created.context_id, actor.workspaceId]);
  expect(rows).toHaveLength(1);
  const expected = { projectId: projects[0].id, traceId: seeded.traceId, spanId: seeded.spanIds[0], caseId: suite.cases[0].id, versionId: version.id };
  assertCapture(rows[0], expected);
  expect(() => assertCapture({ ...rows[0], trace_ref: { ...rows[0].trace_ref, span_id: 'other-span' } }, expected)).toThrow();
  const leaf = await request.get(`${E2E.apiUrl}/model-hub/skills/${created.context_id}/tests/`, { headers });
  expect(leaf.status()).toBe(404);
  await page.goto(`/dashboard/skills/contexts/${created.context_id}?version=1`);
  await page.getByRole('tab', { name: 'Tests', exact: true }).click();
  await expect(page.getByRole('table', { name: 'Context regression cases' })).toContainText(requestText);
  // The captured case sits beside the application target; text capture alone is never native evidence.
  // With no application registered the answer check is the default, so choose the application test first.
  await page.getByRole('radio', { name: /^Test on the application/ }).check();
  await expect(page.getByText('Application target', { exact: true })).toBeVisible();
  await expect(page.getByText('Test cases', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('J2-context-tests.png'), animations: 'disabled' });
  await page.evaluate(() => { const settings = JSON.parse(localStorage.getItem('settings') || '{}'); settings.themeMode = 'dark'; localStorage.setItem('settings', JSON.stringify(settings)); });
  await page.reload();
  await page.getByRole('tab', { name: 'Tests', exact: true }).click();
  await expect(page.getByRole('table', { name: 'Context regression cases' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('J2-context-tests-dark.png'), animations: 'disabled' });
  await testInfo.attach('J2-capture-independent', { body: JSON.stringify({ evidenceClass: 'synthetic fixture', suite, capture: rows[0], expected, fallibility: ['wrong-source-rejected', 'changed-request-rejected'], nativeQualification: false }), contentType: 'application/json' });
  // Cloud-local configured input for a fresh registered-driver attempt. Never fetch credentials.
  if (process.env.J2_DRIVER_FIXTURE_OUTPUT) await writeFile(process.env.J2_DRIVER_FIXTURE_OUTPUT, JSON.stringify({ appUrl: E2E.appUrl, apiUrl: E2E.apiUrl, contextId: created.context_id, version: 1, projectName, projectId: projects[0].id, traceId: seeded.traceId, spanId: seeded.spanIds[0], actor: { access: actor.tokens.access, refresh: actor.tokens.refresh, organizationId: actor.organizationId, workspaceId: actor.workspaceId } }), { mode: 0o600 });
});
