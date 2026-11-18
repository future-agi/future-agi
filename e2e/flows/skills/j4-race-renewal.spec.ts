import { openRequestButton } from '../../lib/skills-r3/release-inbox.mjs';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { SkillsFixtures, answerRule } from '../../lib/skills-fixtures';

// Synthetic fixture: the released baseline, published candidate, two shared
// text results, the registered application target and the author's packet are
// public-API prerequisites. The assigned reviewer then works only in the UI.
// Races are injected through public APIs and the E2E-image retention clock.
const B = '/model-hub/skills/';
const ACK = /^I've checked the (?:changes|\d+ changed files?), the evidence and what this release will affect\.$/;
const fallible = (name: string) => process.env.J4_FALLIBILITY === name;

test('SKILLS-E2E-041: an approved context packet is refused after each race and released only after explicit reviewer renewal', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-041', area: 'skills',
    userGoal: 'An assigned reviewer cannot release an approval that a safety, target, annotation, role or retention race made stale, renews it explicitly with the exact changes shown, and releases only the renewed and re-approved packet',
    steps: [
      'seed a released context, a published candidate and two shared text results',
      'the workspace owner registers the application target from its manifest file in the context Consumers tab',
      'seed the exact author packet assigned to the reviewer and the owner',
      'discover the request in Skills Reviews and approve it',
      'after each race, attempt the release, see the refusal open the exact changes, then either renew and approve separately or approve the renewed revision in one inspected decision',
      'after a transient role loss, attempt the release and see the refusal',
      'release the final renewed packet and reopen the durable receipt',
    ],
    backendChecks: [
      'a safety quarantine A-B-A refuses the old approval with no label event',
      'a target configuration A-B-A refuses the old approval with no label event',
      'a late annotation refuses the old approval with no label event',
      'the target registered in the UI stores the exact manifest and its server-derived digest',
      'a demoted approver cannot release and an assigned, still-authorized releaser cannot use the demoted approval',
      'an expired attached result refuses the old approval and renewal removes only that result',
      'each renewal creates a pending revision whose fingerprint matches the inspected renewal and needs a new decision',
      'exactly one release event moves production to the candidate as the assigned reviewer, bound to the last renewed packet',
    ],
  }),
}, async ({ scopeActors, browser, request, scopeProbe: probe }, testInfo) => {
  test.setTimeout(900_000);
  const admin = scopeActors.ownerA;
  const author = scopeActors.member;
  const reviewer = scopeActors.viewer;
  const name = `e2e-j4r-${Date.now().toString(36)}-${testInfo.workerIndex}`;
  const output = resolve(process.env.J4_EVIDENCE_DIR || testInfo.outputPath('j4-races'));
  await mkdir(`${output}/shots`, { recursive: true });
  const evidence: Record<string, unknown> = { boundary: 'synthetic fixture; public API prerequisites; reviewer UI journey', name };
  const raw = async (actor: typeof author, method: string, path: string, data?: unknown, base = B) => {
    const response = await request.fetch(`${E2E.apiUrl}${base}${path}`, { method, data, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() } });
    const body = await response.json().catch(() => ({}));
    return { status: response.status(), body };
  };
  const api = async (actor: typeof author, method: string, path: string, data?: unknown, base = B) => {
    const { status, body } = await raw(actor, method, path, data, base);
    expect(status < 300, JSON.stringify({ path, status, body })).toBe(true);
    return body.result;
  };
  await api(admin, 'POST', 'organization/members/role/', { user_id: reviewer.userId, ws_level: 8, workspace_id: admin.workspaceId }, '/accounts/');
  const created = await api(author, 'POST', 'contexts/', { name, description: 'Refund policy', tags: [], files: [{ path: 'AGENTS.md', content: 'Refunds above 100 USD need a human approval.\n' }] });
  const id = created.context_id;
  const publish = async () => {
    const draft = await api(author, 'GET', `contexts/${id}/draft/`);
    const head = await api(author, 'POST', `contexts/${id}/draft/head-review/`, { revision: draft.revision });
    const ack = { revision: draft.revision, expected_digest: head.candidate_digest, expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint };
    const acknowledged = await api(author, 'POST', `contexts/${id}/draft/acknowledge-head/`, ack);
    return (await api(author, 'POST', `contexts/${id}/draft/publish/`, { ...ack, revision: acknowledged.revision, review_fingerprint: acknowledged.review_fingerprint, base_version: acknowledged.base_version, note: 'fixture', acknowledge_warnings: true })).version;
  };
  const submit = async (candidateId: string, recipient: string, target: unknown, evidenceRefs: unknown[]) => {
    const preview = await api(author, 'POST', 'release-requests/preview/', { resource_id: id, kind: 'agent_context', candidate_version_id: candidateId, label: 'production', target, evidence: evidenceRefs, external_pipeline: null });
    const sent = await api(author, 'POST', 'release-requests/', { packet: preview.packet, expected_fingerprint: preview.fingerprint, recipient_ids: [recipient] });
    return { preview, sent };
  };
  const v1 = await publish();
  const baseline = await submit(v1.id, admin.userId, null, []);
  await api(admin, 'POST', `release-requests/${baseline.sent.id}/decisions/`, { revision: 1, expected_fingerprint: baseline.preview.fingerprint, decision: 'approve', comment: 'baseline' });
  await api(admin, 'POST', `release-requests/${baseline.sent.id}/execute/`, { revision: 1, expected_fingerprint: baseline.preview.fingerprint });
  await api(author, 'POST', `contexts/${id}/draft/start/`, { revision: 0, from_version: 1 });
  const started = await api(author, 'GET', `contexts/${id}/draft/`);
  await api(author, 'PUT', `contexts/${id}/draft/`, { revision: started.revision, base_version: 1, files: [{ path: 'AGENTS.md', content: 'Refunds above 100 USD need a human approval.\nCheck the order status first.\n' }], description: 'Refund policy', note: '' });
  const draft = await api(author, 'GET', `contexts/${id}/draft/`);
  const fake = new SkillsFixtures();
  const prompts = [`${name}: refund 250 USD?`, `${name}: unknown order refund?`];
  await fake.register(prompts.map((prompt) => answerRule(prompt, 0, { eligible: false })));
  await fake.connect(author.api);
  const runs: string[] = [];
  for (const prompt of prompts) {
    const config = { candidate: { kind: 'draft', revision: draft.revision, digest: draft.digest },
      cases: { kind: 'ad_hoc', cases: [{ id: randomUUID(), title: prompt.slice(-18), source_group_id: randomUUID(), prompt, fixtures: [], expected_trigger: null, reviewed: true,
        checks: [{ id: randomUUID(), kind: 'json_equals', critical: true, config: { pointer: '/eligible', value: false } }] }] },
      baselines: [{ kind: 'production' }], competitors: [], harness: 'contexts-text-v1.2', model: { model_id: 'gpt-4o-mini', temperature: 0, max_output_tokens: 128 },
      judge: null, profile: 'quick', repetitions: 1, limits: { max_total_tokens: 150000, max_estimated_cost_usd: '1' }, purpose: 'regression', parent_run_id: null };
    const preview = await api(author, 'POST', `contexts/${id}/test-runs/preview/`, config);
    const run = await api(author, 'POST', `contexts/${id}/test-runs/`, { configuration: config, expected_fingerprint: preview.fingerprint, acknowledge_provider_processing: true,
      inventory_review: { fingerprint: preview.fingerprint, reviewed_all_files: true, no_known_answer_keys: true, classifications: [], false_positive_secret_findings: [] } });
    await expect.poll(async () => (await api(author, 'GET', `contexts/${id}/test-runs/${run.id}/`)).status, POLL.ASYNC_JOB).toBe('completed');
    await api(author, 'POST', `contexts/${id}/test-runs/${run.id}/share/`, { acknowledge_snapshot_sharing: true });
    runs.push(run.id);
  }
  await fake.dispose();
  const v2 = await publish();
  expect(v2.digest).toBe(draft.digest);
  const wire = JSON.parse(await readFile(resolve(import.meta.dirname, '../../../frontend/src/api/skills/__tests__/r3_l07/wire-fixtures.json'), 'utf8'));
  const manifest = { ...wire.NativeConfiguration, observable_boundaries: ['model_request', 'root_delivery', 'checkpoint', 'child_selection', 'tool_result'].sort() };
  const manifestPath = `${output}/runtime-manifest.json`;
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2));
  // The workspace owner registers the target through the product UI.
  const ownerContext = await scopeActors.openContext(browser, admin);
  const ownerPage = await ownerContext.newPage();
  await ownerPage.setViewportSize({ width: 1440, height: 900 });
  ownerPage.setDefaultTimeout(60_000);
  let binding;
  try {
    await ownerPage.goto('/dashboard/skills');
    await ownerPage.getByRole('tab', { name: 'Agent contexts', exact: true }).click();
    await ownerPage.getByRole('button', { name, exact: true }).click();
    await ownerPage.getByRole('tab', { name: 'Connected apps', exact: true }).click();
    await ownerPage.getByRole('button', { name: 'Connect an app', exact: true }).click();
    const dialog = ownerPage.getByRole('dialog');
    await dialog.getByLabel('Target name', { exact: true }).fill('Refund agent');
    await dialog.getByLabel('Config file', { exact: true }).setInputFiles(manifestPath);
    await expect(dialog.getByText(manifest.entrypoint, { exact: true })).toBeVisible();
    await ownerPage.screenshot({ path: `${output}/shots/00-register-target.png` });
    const created = ownerPage.waitForResponse((res) => new URL(res.url()).pathname.endsWith(`/contexts/${id}/bindings/`) && res.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Connect app', exact: true }).click();
    binding = (await (await created).json()).result;
    await expect(ownerPage.getByText('Registered Refund agent · configuration r1', { exact: true })).toBeVisible();
  } finally { await ownerContext.close(); }
  const stored = await probe.pg<{ configuration: unknown; configuration_digest: string; kind: string; revision: number }>('SELECT configuration, configuration_digest, kind, revision::integer AS revision FROM model_hub_hubskillcontextbinding WHERE context_id=$1 AND retired_at IS NULL', [id]);
  const registration = { rows: stored.length, configuration: stored[0]?.configuration, digest: stored[0]?.configuration_digest, kind: stored[0]?.kind, revision: stored[0]?.revision };
  if (fallible('registration')) registration.configuration = { ...manifest, tools_digest: 'typed-by-hand' };
  expect(registration).toEqual({ rows: 1, configuration: manifest, digest: binding.configuration_digest, kind: 'runtime', revision: 1 });
  evidence.registration = registration;
  const target = { binding_id: binding.id, environment_id: null, revision: binding.revision, configuration_digest: binding.configuration_digest };
  // The owner is also assigned, so it is a still-authorized releaser that can
  // address the request when the approver loses authority.
  const preview2 = await api(author, 'POST', 'release-requests/preview/', { resource_id: id, kind: 'agent_context', candidate_version_id: v2.id, label: 'production', target, evidence: runs.map((source_id) => ({ source: 'skills_text', source_id, annotation_revision: 0 })), external_pipeline: null });
  const packet = { preview: preview2, sent: await api(author, 'POST', 'release-requests/', { packet: preview2.packet, expected_fingerprint: preview2.fingerprint, recipient_ids: [reviewer.userId, admin.userId] }) };
  const requestId = packet.sent.id;
  const events = () => probe.pg<{ id: string }>('SELECT id::text FROM model_hub_hubskilllabelevent WHERE skill_id=$1 AND to_version=2', [id]);
  const context = await scopeActors.openContext(browser, reviewer);
  const page: Page = await context.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });
  page.setDefaultTimeout(60_000);
  const diagnostics: string[] = [];
  page.on('console', (message) => { if (message.type() === 'error') diagnostics.push(`console:${message.text().slice(0, 300)}`); });
  page.on('response', (res) => { if (res.url().includes('/model-hub/skills/release-requests/') || res.status() >= 400) diagnostics.push(`${Date.now() % 100000} ${res.status()} ${res.request().method()} ${new URL(res.url()).pathname.replace('/model-hub/skills/', '')}`); });
  const ack = page.getByRole('checkbox', { name: ACK, exact: true });
  const attempt = async (acknowledge = true) => {
    await page.getByRole('button', { name: 'Release to production', exact: true }).click();
    // A returning reviewer attests in the final move dialog if the exact packet was not checked in this session.
    if (acknowledge && await ack.isVisible()) await ack.check();
    const response = page.waitForResponse((res) => new URL(res.url()).pathname.endsWith(`/release-requests/${requestId}/execute/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Move production', exact: true }).click();
    const result = await response;
    await expect(page.getByRole('button', { name: 'Move production', exact: true })).toHaveCount(0);
    return { status: result.status(), code: (await result.json()).code };
  };
  const approve = async () => {
    await expect(page.getByRole('button', { name: 'Renew request', exact: true })).toHaveCount(0);
    await ack.check();
    const decided = page.waitForResponse((res) => new URL(res.url()).pathname.endsWith(`/release-requests/${requestId}/decisions/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Approve release', exact: true }).click();
    expect((await decided).ok()).toBe(true);
    // Attestation of an unchanged revision survives the approval refresh.
    // Wait for that server-backed refresh to settle before the environment
    // injects the next race, so the race lands after the approved state is
    // shown (as in the registered driver), not inside the refresh itself.
    await expect(page.getByRole('button', { name: 'Release to production', exact: true })).toBeEnabled();
    await expect(page.getByText('You checked this release when you approved it.', {exact:true})).toBeVisible();
  };
  const renewals: unknown[] = [];
  const renew = async (label: string, { reloaded = false } = {}) => {
    const before = await api(reviewer, 'GET', `release-requests/${requestId}/renewal/`);
    const dialog = page.getByRole('dialog').filter({ hasText: 'Renew request' });
    // A refusal opens the inspection directly; a reloaded page offers the button.
    if (reloaded) await page.getByRole('button', { name: 'Renew request', exact: true }).click();
    await expect(dialog).toBeVisible();
    for (const row of before.changes) await expect(dialog.locator(`tr[data-field="${row.item}"]`)).toBeVisible();
    await page.screenshot({ path: `${output}/shots/renew-${label}.png` });
    await dialog.getByRole('checkbox', { name: "I've checked every change since the last approval, and it needs approving again.", exact: true }).check();
    await dialog.getByRole('button', { name: 'Renew and decide later', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    const after = await api(reviewer, 'GET', `release-requests/${requestId}/`);
    const observed = { label, revision: after.revision, status: after.status, fingerprint: after.fingerprint, inspected: before.fingerprint, from: before.revision, removed: before.removed_evidence, items: before.changes.map((row: { item: string }) => row.item) };
    if (fallible('renewal')) observed.status = 'approved';
    expect(observed).toMatchObject({ revision: before.revision + 1, status: 'pending', fingerprint: before.fingerprint });
    renewals.push(observed);
    return observed;
  };
  // One-step path: inspect, acknowledge and approve the renewed revision in a
  // single decision bound to the inspected fingerprint.
  const renewAndApprove = async (label: string) => {
    const before = await api(reviewer, 'GET', `release-requests/${requestId}/renewal/`);
    const dialog = page.getByRole('dialog').filter({ hasText: 'Renew request' });
    await expect(dialog).toBeVisible();
    for (const row of before.changes) await expect(dialog.locator(`tr[data-field="${row.item}"]`)).toBeVisible();
    const approveRenewed = dialog.getByRole('button', { name: 'Approve again', exact: true });
    await expect(approveRenewed).toBeDisabled();
    await dialog.getByRole('checkbox', { name: "I've checked every change since the last approval, and it needs approving again.", exact: true }).check();
    await approveRenewed.click();
    await expect(dialog).toHaveCount(0);
    const after = await api(reviewer, 'GET', `release-requests/${requestId}/`);
    const decided = await probe.pg<{ actor_id: string; decision: string }>('SELECT d.actor_id::text, d.decision FROM model_hub_hubskillreleasedecision d JOIN model_hub_hubskillreleasepacket p ON p.id=d.packet_id WHERE p.request_id=$1 AND p.revision=$2', [requestId, after.revision]);
    const observed = { label, oneStep: true, revision: after.revision, status: after.status, fingerprint: after.fingerprint, inspected: before.fingerprint, from: before.revision, decisions: decided, items: before.changes.map((row: { item: string }) => row.item) };
    if (fallible('renewal')) observed.decisions = [];
    expect(observed).toMatchObject({ revision: before.revision + 1, status: 'approved', fingerprint: before.fingerprint, decisions: [{ actor_id: reviewer.userId, decision: 'approve' }] });
    // The renewed packet itself is attested on the page before release.
    await expect(page.getByText('Approved. Confirm the move to release it.', {exact:true})).toBeVisible();
    renewals.push(observed);
    return observed;
  };
  const races: Record<string, unknown> = {};
  try {
    await page.goto('/dashboard/skills');
    await page.getByRole('tab', { name: 'Reviews', exact: true }).click();
    await openRequestButton(page, name).click();
    await approve();
    // Safety policy A -> B -> A.
    const safety = await api(admin, 'GET', `version-safety/${v2.id}/`);
    const held = await api(admin, 'PUT', `version-safety/${v2.id}/`, { revision: safety.revision, quarantined: true, reason: 'hold', replacement_version_id: null });
    await api(admin, 'PUT', `version-safety/${v2.id}/`, { revision: held.revision, quarantined: false, reason: 'cleared', replacement_version_id: null });
    races.policy = { attempt: await attempt(), events: (await events()).length };
    if (fallible('policy')) races.policy = { attempt: { status: 200, code: null }, events: 1 };
    expect(races.policy).toEqual({ attempt: { status: 409, code: 'release_conflict' }, events: 0 });
    await page.screenshot({ path: `${output}/shots/01-policy-refused.png` });
    await renew('policy');
    await approve();
    // Target configuration A -> B -> A.
    const body = (configuration: unknown) => ({ name: 'Refund agent', kind: 'runtime', selector: { label: 'production' }, update_mode: 'new_thread', profile: 'native-runtime', profile_version: '1', repository: null, expected_report_interval_seconds: 3600, configuration });
    const changed = await api(admin, 'PATCH', `contexts/bindings/${binding.id}/`, { revision: binding.revision, binding: body({ ...manifest, tools_digest: `sha256:${'f'.repeat(64)}` }) });
    await api(admin, 'PATCH', `contexts/bindings/${binding.id}/`, { revision: changed.revision, binding: body(manifest) });
    races.target = { attempt: await attempt(), events: (await events()).length };
    if (fallible('target')) races.target = { attempt: { status: 200, code: null }, events: 1 };
    expect(races.target).toEqual({ attempt: { status: 409, code: 'impact_changed' }, events: 0 });
    const targetRenewal = await renew('target');
    expect(targetRenewal.items).toEqual(expect.arrayContaining(['target', 'transition.expected_impact_fingerprint']));
    await approve();
    // Late annotation on the first attached result.
    await api(admin, 'POST', `evidence/skills_text/${runs[0]}/annotations/`, { annotation_revision: 0, check_id: null, attempt_id: null, verdict: 'agree', cited_text: '', comment: 'late' });
    races.annotation = { attempt: await attempt(), events: (await events()).length };
    if (fallible('annotation')) races.annotation = { attempt: { status: 200, code: null }, events: 1 };
    expect(races.annotation).toEqual({ attempt: { status: 409, code: 'annotation_conflict' }, events: 0 });
    await renewAndApprove('annotation');
    // Transient role loss.
    await api(admin, 'POST', 'organization/members/role/', { user_id: reviewer.userId, ws_level: 1, workspace_id: admin.workspaceId }, '/accounts/');
    // No execution receipt may exist for any refused attempt.
    const receiptBefore = (await raw(author, 'GET', `release-requests/${requestId}/receipt/`)).status;
    const roleAttempt = await attempt();
    const detail = await probe.pg<{ revision: number; fingerprint: string }>('SELECT p.revision::integer AS revision, p.fingerprint FROM model_hub_hubskillreleasepacket p JOIN model_hub_hubskillreleaserequest r ON r.id=p.request_id AND r.revision=p.revision WHERE r.id=$1', [requestId]);
    const ownerProbe = await raw(admin, 'POST', `release-requests/${requestId}/execute/`, { revision: detail[0].revision, expected_fingerprint: detail[0].fingerprint });
    races.role = { attempt: roleAttempt, owner: { status: ownerProbe.status, code: ownerProbe.body.code }, events: (await events()).length, receiptBefore };
    if (fallible('role')) races.role = { attempt: { status: 200, code: null }, owner: { status: 200, code: null }, events: 1, receiptBefore: 200 };
    expect(races.role).toEqual({ attempt: { status: 403, code: 'permission_denied' }, owner: { status: 403, code: 'permission_denied' }, events: 0, receiptBefore: 404 });
    await api(admin, 'POST', 'organization/members/role/', { user_id: reviewer.userId, ws_level: 8, workspace_id: admin.workspaceId }, '/accounts/');
    // Retention expiry of the second attached result (E2E-image clock seam).
    const expired = await api(admin, 'POST', 'model-hub/e2e/j4/evidence-clock/', { source: 'skills_text', source_id: runs[1] }, '/');
    races.expiry = { attempt: await attempt(false), events: (await events()).length, purged: expired.payload_purged };
    if (fallible('expiry')) races.expiry = { attempt: { status: 200, code: null }, events: 1, purged: true };
    expect(races.expiry).toEqual({ attempt: { status: 410, code: 'evidence_expired' }, events: 0, purged: true });
    // Reopen in dark mode: detailed evidence is gone, so only explicit
    // renewal is offered on the reloaded page.
    await page.evaluate(() => { const value = JSON.parse(localStorage.getItem('settings') || '{}'); localStorage.setItem('settings', JSON.stringify({ ...value, themeMode: 'dark' })); });
    await page.reload();
    await expect(page.getByText('Attached evidence expired after this request was made.', { exact: true })).toBeVisible();
    await expect(page.getByText('Paused', { exact: true })).toBeVisible();
    await expect(page.getByText(/Current candidate shown for renewal/)).toBeVisible();
    await page.screenshot({ path: `${output}/shots/03-renewal-required-dark.png` });
    await page.screenshot({ path: `${output}/shots/renewal-required.png` });
    diagnostics.push('--- renew expiry');
    const expiryRenewal = await renew('expiry-dark', { reloaded: true });
    diagnostics.push('--- renewed expiry');
    expect(expiryRenewal.removed).toEqual([{ source: 'skills_text', source_id: runs[1], reason: 'evidence_expired' }]);
    const remaining = await api(reviewer, 'GET', `release-requests/${requestId}/`);
    expect(remaining.packet.evidence.map((ref: { source_id: string }) => ref.source_id)).toEqual([runs[0]]);
    await approve();
    await page.getByRole('button', { name: 'Release to production', exact: true }).click();
    if (await ack.isVisible()) await ack.check();
    const executed = page.waitForResponse((res) => new URL(res.url()).pathname.endsWith(`/release-requests/${requestId}/execute/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Move production', exact: true }).click();
    const response = await executed;
    expect(response.ok()).toBe(true);
    const receipt = (await response.json()).result;
    const final = await api(reviewer, 'GET', `release-requests/${requestId}/receipt/`);
    const moved = await probe.pg<{ id: string; from_version: number; actor_id: string }>('SELECT id::text, from_version, actor_id::text FROM model_hub_hubskilllabelevent WHERE skill_id=$1 AND to_version=2', [id]);
    const release = { events: moved, receiptEvent: final.post_move_state.event_id, fingerprint: final.currentness_at_action.packet_fingerprint, renewed: remaining.fingerprint, revision: final.revision };
    if (fallible('event')) release.events = [...moved, ...moved];
    expect(release).toEqual({ events: [{ id: receipt.post_move_state.event_id, from_version: 1, actor_id: reviewer.userId }], receiptEvent: receipt.post_move_state.event_id,
      fingerprint: remaining.fingerprint, renewed: remaining.fingerprint, revision: 5 });
    await page.reload();
    await expect(page.getByText('Release receipt', { exact: true })).toBeVisible();
    await page.screenshot({ path: `${output}/shots/02-durable-receipt.png` });
    Object.assign(evidence, { races, renewals, release });
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) await page.screenshot({ path: `${output}/shots/failure.png` }).catch(() => {});
    await context.close();
    await writeFile(`${output}/observations.json`, `${JSON.stringify({ ...evidence, races, renewals, diagnostics }, null, 2)}\n`);
  }
});
