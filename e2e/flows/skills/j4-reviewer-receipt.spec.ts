import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';

// Synthetic fixture: candidate bytes are a prerequisite; the author composes
// and assigns the packet through production UI before the reviewer arrives.
// This does not claim typed native evidence, race-matrix or 10x qualification.
const B = '/model-hub/skills/';

test('SKILLS-E2E-040: an assigned reviewer releases an exact packet and reopens its durable receipt', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-040', area: 'skills',
    userGoal: 'An independent assigned reviewer discovers a published candidate, approves its exact packet, deliberately releases it and reopens the durable receipt after the pointer changes',
    steps: [
      'seed baseline and candidate bytes, then compose and assign an exact packet through the author UI',
      'discover the request through Skills Reviews without a test-provided deep link',
      'inspect the loaded candidate diff and acknowledge the exact packet',
      'approve, observe a stale-execution refusal after policy ABA, then explicitly renew and independently reapprove the packet',
      'deliberately execute the authorized transition after renewed review',
      'reload and read the durable pre-move currentness and post-move event receipt',
    ],
    backendChecks: [
      'only the assigned reviewer sees the request and the production label remains on the baseline before release',
      'the exact before and candidate capability inspection is bound to the submitted packet fingerprint',
      'the independent reviewer decision binds to the submitted packet fingerprint and revision',
      'policy ABA refuses the old approval without a release event before explicit renewal',
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
  const evidence: Record<string, unknown> = { boundary: 'synthetic fixture; public API candidate setup followed by real author submission and reviewer release', name };
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
  await testInfo.attach('j4-synthetic-seed', { contentType: 'application/json', body: JSON.stringify({ id, name, authorId: author.userId, reviewerId: reviewer.userId, baseline: seed.version, candidate: candidate.version }) });
  const authorContext = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 } });
  await authorContext.addInitScript(authInitScript, { access: author.tokens.access, refresh: author.tokens.refresh, organizationId: author.organizationId, workspaceId: author.workspaceId });
  await authorContext.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), author.organizationId);
  const authorPage = await authorContext.newPage();
  authorPage.setDefaultTimeout(60_000);
  let preview;
  let submitted;
  try {
    await authorPage.goto('/dashboard/skills');
    await authorPage.getByRole('tab', { name: 'Reviews', exact: true }).click();
    await authorPage.getByRole('button', { name: 'Request review', exact: true }).click();
    await authorPage.getByLabel('Name', { exact: true }).click();
    await authorPage.getByRole('option', { name, exact: true }).click();
    await authorPage.getByLabel('Published candidate').click();
    await authorPage.getByRole('option', { name: /^v2/ }).click();
    await authorPage.getByLabel('Assigned reviewer').click();
    const reviewerOption = authorPage.getByRole('option', { name: `E2E ${scopeActors.prefix}-a`, exact: true });
    await reviewerOption.click();
    const preparedResponse = authorPage.waitForResponse(res => res.url().endsWith('/release-requests/preview/') && res.request().method() === 'POST');
    await authorPage.getByRole('button', { name: 'Prepare review', exact: true }).click();
    preview = (await (await preparedResponse).json()).result;
    await authorPage.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true }).check();
    await expect(authorPage.getByRole('button', { name: 'Send review request', exact: true })).toBeEnabled();
    await authorPage.screenshot({ path: `${output}/shots/00-author-packet.png` });
    const submission = authorPage.waitForResponse(res => res.url().endsWith('/release-requests/') && res.request().method() === 'POST');
    await authorPage.getByRole('button', { name: 'Send review request', exact: true }).click();
    const response = await submission;
    expect(response.status()).toBe(201);
    submitted = (await response.json()).result;
    await expect(authorPage.getByRole('button', { name, exact: true })).toBeVisible();
    await authorPage.evaluate(() => { const value = JSON.parse(localStorage.getItem('settings') || '{}'); localStorage.setItem('settings', JSON.stringify({ ...value, themeMode: 'dark' })); });
    await authorPage.reload();
    await authorPage.getByRole('button', { name: 'Request review', exact: true }).click();
    await expect(authorPage.getByLabel('Name', { exact: true })).toBeVisible();
    await authorPage.screenshot({ path: `${output}/shots/06-author-composer-dark.png` });
  } finally {
    if (!submitted) await authorPage.screenshot({ path: `${output}/shots/author-failure.png` }).catch(() => {});
    await authorContext.close();
  }
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
    const otherChecks = page.getByRole('button', { name: 'Other checks', exact: true });
    if (await otherChecks.count()) await otherChecks.click();
    await page.getByRole('button', { name: 'Compared content', exact: true }).click();
    await expect(page.getByText('No changes in recognized static declarations. This is not a safety certification.', { exact: true })).toBeVisible();
    const inspected = await api(reviewer, 'POST', 'release-requests/inspection/', {
      resource_id: id, kind: 'skill', candidate_version_id: candidate.version.id, label: 'production',
      target: null, evidence: [], external_pipeline: null, expected_fingerprint: preview.fingerprint,
    });
    evidence.inspection = inspected;
    if (process.env.J4_FALLIBILITY === 'inspection') inspected.capabilities.after_digest = seed.version.digest;
    expect({ fingerprint: inspected.fingerprint, before: inspected.capabilities.before_digest, after: inspected.capabilities.after_digest, changes: inspected.capabilities.changes })
      .toEqual({ fingerprint: preview.fingerprint, before: seed.version.digest, after: candidate.version.digest, changes: [] });
    await page.screenshot({ path: `${output}/shots/02-exact-review.png` });
    await acknowledged.check();
    const decisionResponse = page.waitForResponse(res => res.url().endsWith(`/release-requests/${submitted.id}/decisions/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Approve release', exact: true }).click();
    expect((await decisionResponse).ok()).toBe(true);
    const decisions = await probe.pg('SELECT d.actor_id::text AS reviewer_id, p.fingerprint AS packet_fingerprint, p.revision AS request_revision FROM model_hub_hubskillreleasedecision d JOIN model_hub_hubskillreleasepacket p ON p.id = d.packet_id WHERE p.request_id = $1', [submitted.id]);
    evidence.decisions = decisions;
    if (process.env.J4_FALLIBILITY === 'decision') decisions[0].packet_fingerprint = 'not-the-reviewed-packet';
    expect(decisions).toEqual([{ reviewer_id: reviewer.userId, packet_fingerprint: preview.fingerprint, request_revision: String(submitted.revision) }]);
    // The same reviewer's attestation of this unchanged revision survives the
    // pending -> approved refresh; it is not re-requested before release.
    await expect(acknowledged).toBeChecked();
    await page.getByRole('button', { name: 'Release to production', exact: true }).click();
    // Environmental race: policy A -> B -> A leaves visible protection unchanged
    // but must invalidate approval by monotonically advancing release revision.
    const protection = await api(reviewer, 'GET', `${id}/label-protection/`);
    const changed = await api(reviewer, 'PUT', `${id}/label-protection/`, { protected_label_names: [...protection.protected_label_names, 'j4-guard'], expected_release_revision: protection.release_revision });
    const restored = await api(reviewer, 'PUT', `${id}/label-protection/`, { protected_label_names: protection.protected_label_names, expected_release_revision: changed.release_revision });
    const staleResponse = page.waitForResponse(res => res.url().endsWith(`/release-requests/${submitted.id}/execute/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Move production', exact: true }).click();
    expect((await staleResponse).status()).toBe(409);
    // The refusal opens the renewal inspection for the assigned reviewer
    // directly; nothing is renewed or approved until they decide.
    await expect(page.getByRole('dialog').filter({ hasText: 'Renew release review' })).toBeVisible();
    const beforeRenewal = await probe.pg('SELECT id FROM model_hub_hubskilllabelevent WHERE skill_id = $1 AND to_version = 2', [id]);
    if (process.env.J4_FALLIBILITY === 'policy') beforeRenewal.push({ id: 'unexpected-event' });
    expect(beforeRenewal).toHaveLength(0);
    await page.screenshot({ path: `${output}/shots/05-stale-policy-refused.png` });
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByText('This changed since you opened it. Refresh and review again.', { exact: true })).toBeVisible();
    await page.screenshot({ path: `${output}/shots/review-stale.png` });
    const renewalContext = await scopeActors.openContext(browser, author);
    const renewalPage = await renewalContext.newPage();
    renewalPage.setDefaultTimeout(60_000);
    try {
      await renewalPage.goto('/dashboard/skills');
      await renewalPage.getByRole('tab', { name: 'Reviews', exact: true }).click();
      await renewalPage.getByRole('tab', { name: 'My requests', exact: true }).click();
      await renewalPage.getByRole('button', { name, exact: true }).click();
      await renewalPage.getByRole('button', { name: 'Revise request', exact: true }).click();
      await expect(renewalPage.getByRole('heading', { name: 'Renew release review', exact: true })).toBeVisible();
      await renewalPage.getByLabel('Published candidate').click();
      await renewalPage.getByRole('option', { name: /^v2/ }).click();
      await renewalPage.getByLabel('Assigned reviewer').click();
      await renewalPage.getByRole('option', { name: `E2E ${scopeActors.prefix}-a`, exact: true }).click();
      await renewalPage.getByRole('button', { name: 'Prepare review', exact: true }).click();
      await renewalPage.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true }).check();
      const renewed = renewalPage.waitForResponse(res => res.url().endsWith('/release-requests/') && res.request().method() === 'POST');
      await renewalPage.getByRole('button', { name: 'Send review request', exact: true }).click();
      const renewal = (await (await renewed).json()).result;
      expect(renewal).toMatchObject({ id: submitted.id, revision: submitted.revision + 1, status: 'pending' });
      evidence.renewal = renewal;
    } finally { await renewalContext.close(); }
    // Reviewer returns through the inbox, not a test-injected request URL.
    await page.goto('/dashboard/skills');
    await page.getByRole('tab', { name: 'Reviews', exact: true }).click();
    await page.getByRole('button', { name, exact: true }).click();
    await expect(acknowledged).not.toBeChecked();
    await acknowledged.check();
    const renewedDecision = page.waitForResponse(res => res.url().endsWith(`/release-requests/${submitted.id}/decisions/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Approve release', exact: true }).click();
    expect((await renewedDecision).ok()).toBe(true);
    await expect(acknowledged).toBeChecked();
    await page.getByRole('button', { name: 'Release to production', exact: true }).click();
    evidence.policyRace = { restored, beforeRenewal, staleStatus: 409 };
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
    expect(durable.currentness_at_action.release_revision).toBe(restored.release_revision);
    expect(durable.post_move_state.release_revision).toBe(restored.release_revision + 1);
    expect(durable).toEqual(receipt);
    await page.reload();
    await expect(page.getByText('Release receipt', { exact: true })).toBeVisible();
    await expect(page.getByText('Before the move', { exact: true })).toBeVisible();
    await expect(page.getByText('After the move', { exact: true })).toBeVisible();
    await expect(page.getByText('Review is stale or unavailable.', { exact: false })).not.toBeVisible();
    await page.screenshot({ path: `${output}/shots/03-durable-receipt-light.png` });
    await page.evaluate(() => { const value = JSON.parse(localStorage.getItem('settings') || '{}'); localStorage.setItem('settings', JSON.stringify({ ...value, themeMode: 'dark' })); });
    await page.reload();
    await expect(page.getByText('Release receipt', { exact: true })).toBeVisible();
    await page.screenshot({ path: `${output}/shots/04-durable-receipt-dark.png` });
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) await page.screenshot({ path: `${output}/shots/failure.png` }).catch(() => {});
    await context.close();
    await writeFile(`${output}/observations.json`, `${JSON.stringify(evidence, null, 2)}\n`);
  }
});
