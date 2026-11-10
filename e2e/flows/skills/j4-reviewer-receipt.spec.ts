import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';

// This proves the independent reviewer/receipt segment, not complete J4.
// API-created candidate and packet are prerequisites, NOT measured UI actions.
// No typed native evidence, target configuration or competitive claim is seeded.
const B = '/model-hub/skills/';

test('SKILLS-E2E-040: an assigned reviewer releases an exact packet and reopens its durable receipt', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-040', area: 'skills',
    userGoal: 'An independent assigned reviewer discovers a published candidate, approves its exact packet, deliberately releases it and reopens the durable receipt after the pointer changes',
    steps: [
      'seed a baseline, immutable candidate and assigned packet through public APIs as prerequisites only',
      'discover the request through Skills Reviews without a test-provided deep link',
      'inspect the loaded candidate diff and acknowledge the exact packet',
      'approve as the independent reviewer and deliberately execute the authorized transition',
      'reload and read the durable pre-move currentness and post-move event receipt',
    ],
    backendChecks: [
      'only the assigned reviewer sees the request and the production label remains on the baseline before release',
      'the independent reviewer decision binds to the submitted packet fingerprint and revision',
      'exactly one release event moves production to the candidate as the authorized reviewer',
      'the durable receipt preserves the pre-move release revision separately from the post-move event',
    ],
  }),
}, async ({ scopeActors, browser, request, scopeProbe: probe }, testInfo) => {
  test.setTimeout(660_000);
  const author = scopeActors.member;
  const reviewer = scopeActors.ownerA;
  const name = `e2e-j4-${Date.now().toString(36)}-${testInfo.workerIndex}`;
  const output = resolve(process.env.J4_EVIDENCE_DIR || testInfo.outputPath('j4'));
  await mkdir(`${output}/shots`, { recursive: true });
  await mkdir(`${output}/video`, { recursive: true });
  const evidence: Record<string, unknown> = { boundary: 'reviewer segment only; public API setup is not measured author submission', name };
  const api = async (actor: typeof author, method: string, path: string, data?: unknown) => {
    const response = await request.fetch(`${E2E.apiUrl}${B}${path}`, {
      method, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() }, data,
    });
    const body = await response.json();
    expect(response.ok(), JSON.stringify({ path, status: response.status(), body })).toBe(true);
    return body.result;
  };
  const files = (text: string) => [{ path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Use when reviewing the synthetic authorization policy before a release.\n---\n${text}\n` }];
  const seed = await api(reviewer, 'POST', 'publish/', { files: files('Previous reviewed policy.'), labels: ['production'], expected_release_revision: 0 });
  const id = seed.skill.id;
  const candidate = await api(author, 'POST', 'publish/', { files: files('Candidate policy requiring independent approval.'), labels: [], expected_release_revision: 1 });
  expect(candidate.skill.id).toBe(id);
  const preview = await api(author, 'POST', 'release-requests/preview/', { resource_id: id, kind: 'skill', candidate_version_id: candidate.version.id, label: 'production', target: null, evidence: [], external_pipeline: null });
  const submitted = await api(author, 'POST', 'release-requests/', { packet: preview.packet, expected_fingerprint: preview.fingerprint, recipient_ids: [reviewer.userId], revision: 0 });
  Object.assign(evidence, { id, candidate: candidate.version, packet: preview.packet, fingerprint: preview.fingerprint, requestId: submitted.id });
  const ownInbox = await api(author, 'GET', 'review-inbox/');
  const reviewerInbox = await api(reviewer, 'GET', 'review-inbox/');
  const labels = await probe.pg<{ number: number }>('SELECT v.number FROM model_hub_hubskilllabel l JOIN model_hub_hubskillversion v ON v.id = l.version_id WHERE l.skill_id = $1 AND l.name = $2', [id, 'production']);
  const recipientObservation = { authorVisible: ownInbox.results.some((row: { id: string }) => row.id === submitted.id), reviewerVisible: reviewerInbox.results.some((row: { id: string; unread: boolean }) => row.id === submitted.id && row.unread), production: labels[0]?.number };
  evidence.recipientObservation = recipientObservation;
  if (process.env.J4_FALLIBILITY === 'recipient') recipientObservation.reviewerVisible = false;
  expect(recipientObservation).toEqual({ authorVisible: false, reviewerVisible: true, production: 1 });
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 }, recordVideo: { dir: `${output}/video`, size: { width: 1440, height: 900 } } });
  await context.addInitScript(authInitScript, { access: reviewer.tokens.access, refresh: reviewer.tokens.refresh, organizationId: reviewer.organizationId, workspaceId: reviewer.workspaceId });
  await context.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), reviewer.organizationId);
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  try {
    await page.goto('/dashboard/skills');
    await page.getByRole('tab', { name: 'Reviews', exact: true }).click();
    await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
    await page.screenshot({ path: `${output}/shots/01-inbox.png` });
    await page.getByRole('button', { name, exact: true }).click();
    const acknowledged = page.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true });
    await expect(acknowledged).toBeEnabled();
    await expect(page.getByText('Candidate policy requiring independent approval.', { exact: false }).first()).toBeVisible();
    await page.screenshot({ path: `${output}/shots/02-exact-review.png` });
    await acknowledged.check();
    const decisionResponse = page.waitForResponse(res => res.url().endsWith(`/release-requests/${submitted.id}/decisions/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Approve exact packet', exact: true }).click();
    expect((await decisionResponse).ok()).toBe(true);
    const decisions = await probe.pg('SELECT d.actor_id::text AS reviewer_id, p.fingerprint AS packet_fingerprint, p.revision AS request_revision FROM model_hub_hubskillreleasedecision d JOIN model_hub_hubskillreleasepacket p ON p.id = d.packet_id WHERE p.request_id = $1', [submitted.id]);
    evidence.decisions = decisions;
    if (process.env.J4_FALLIBILITY === 'decision') decisions[0].packet_fingerprint = 'not-the-reviewed-packet';
    expect(decisions).toEqual([{ reviewer_id: reviewer.userId, packet_fingerprint: preview.fingerprint, request_revision: String(submitted.revision) }]);
    await expect(acknowledged).not.toBeChecked();
    await acknowledged.check();
    await page.getByRole('button', { name: 'Release approved packet', exact: true }).click();
    const executed = page.waitForResponse(res => res.url().endsWith(`/release-requests/${submitted.id}/execute/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Move production', exact: true }).click();
    const executionResponse = await executed;
    expect(executionResponse.ok()).toBe(true);
    const receipt = (await executionResponse.json()).result;
    evidence.receipt = receipt;
    const events = await probe.pg('SELECT id::text, from_version, to_version, actor_id::text FROM model_hub_hubskilllabelevent WHERE skill_id = $1 AND to_version = 2', [id]);
    evidence.events = events;
    if (process.env.J4_FALLIBILITY === 'event') events[0].to_version = 1;
    expect(events).toEqual([{ id: receipt.post_move_state.event_id, from_version: 1, to_version: 2, actor_id: reviewer.userId }]);
    const durable = await api(reviewer, 'GET', `release-requests/${submitted.id}/receipt/`);
    if (process.env.J4_FALLIBILITY === 'receipt') durable.currentness_at_action.release_revision = durable.post_move_state.release_revision;
    expect(durable).toEqual(receipt);
    expect(durable.currentness_at_action.release_revision).toBe(1);
    expect(durable.post_move_state.release_revision).toBe(2);
    await page.reload();
    await expect(page.getByText('Durable release receipt', { exact: true })).toBeVisible();
    await expect(page.getByText('Currentness before the move', { exact: true })).toBeVisible();
    await expect(page.getByText('Pointer state after the move', { exact: true })).toBeVisible();
    await expect(page.getByText('Review is stale or unavailable.', { exact: false })).not.toBeVisible();
    await page.screenshot({ path: `${output}/shots/03-durable-receipt-light.png` });
    await page.evaluate(() => { const value = JSON.parse(localStorage.getItem('settings') || '{}'); localStorage.setItem('settings', JSON.stringify({ ...value, themeMode: 'dark' })); });
    await page.reload();
    await expect(page.getByText('Durable release receipt', { exact: true })).toBeVisible();
    await page.screenshot({ path: `${output}/shots/04-durable-receipt-dark.png` });
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) await page.screenshot({ path: `${output}/shots/failure.png` }).catch(() => {});
    await context.close();
    await writeFile(`${output}/observations.json`, `${JSON.stringify(evidence, null, 2)}\n`);
  }
});
