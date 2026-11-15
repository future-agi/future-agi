import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

const API = '/model-hub/skills/';

// Public release prerequisite, not a native application/complete-job claim.
// Pointer moves intentionally do not imply local files or threads recovered.
test('SKILLS-E2E-062: review a lower-number rollback with a retained retired child, reject a quarantined alternative and an ABA-stale packet', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-062', area: 'skills',
    userGoal: 'Choose and review an eligible historical context, reject an unsafe alternative and stale pointer authority, then deliberately roll back without declaring application recovery',
    steps: [
      'publish three immutable synthetic context releases containing the same retained child through public authoring APIs',
      'retire the source child and quarantine the alternative, checking the complete historical closure remains readable',
      'approve a rollback packet and exercise a real ABA sequence through independent author and reviewer APIs',
      'reject execution of the stale packet with no extra label event',
      'open a fresh request from the reviewer inbox, inspect its exact closure and deliberately move production to the lower-number release',
      'inspect the durable receipt and confirm the unobserved declared consumer remains unknown',
    ],
    backendChecks: [
      'Postgres retains the archived child identity and the exact historical dependency rather than substituting the current source',
      'quarantined alternative cannot be reviewed for release and stale ABA execution cannot create a label event',
      'one exact lower-number event matches the independently read durable receipt, while the latest immutable version remains unchanged',
      'public consumer accounting does not convert a pointer-only rollback into observed application recovery',
    ],
  }),
}, async ({ scopeActors, browser, request, scopeProbe: probe }, testInfo) => {
  test.setTimeout(660_000); // setup/release APIs 180s + eight bounded UI stages × 60s.
  const author = scopeActors.member;
  const reviewer = scopeActors.ownerA;
  const name = `e2e-j6-rollback-${Date.now().toString(36)}-${testInfo.workerIndex}`;
  const send = async (actor: typeof author, method: string, path: string, data?: unknown) => {
    const response = await request.fetch(`${E2E.apiUrl}${API}${path}`, {
      method, data, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() },
    });
    return { response, body: response.status() === 204 ? null : await response.json() };
  };
  const api = async (actor: typeof author, method: string, path: string, data?: unknown): Promise<any> => {
    const { response, body } = await send(actor, method, path, data);
    expect(response.ok(), `${method} ${path}: ${response.status()} ${JSON.stringify(body)}`).toBeTruthy();
    return body?.result;
  };
  const leaf = await api(reviewer, 'POST', 'publish/', { files: [{ path: 'SKILL.md', content: `---\nname: ${name}-leaf\ndescription: Apply the retained synthetic refund policy.\n---\nRefund exactly 25 units at most once.\n` }], labels: [], expected_release_revision: 0, acknowledge_warnings: true });
  const created = await api(author, 'POST', 'contexts/', { name, description: 'Synthetic recovery release fixture', tags: [], files: [{ path: 'AGENTS.md', content: 'Original reviewed synthetic policy. Reconcile completed business operations.\n' }] });
  const root = `contexts/${created.context_id}/`;
  const links = { revision: created.revision, links: [{ skill_id: leaf.skill.id, selector: { version: 1 } }], refresh_skill_ids: [] };
  const dependencyPreview = await api(author, 'POST', `${root}dependencies/preview/`, links);
  let draft = await api(author, 'POST', `${root}dependencies/apply/`, { ...links, expected_fingerprint: dependencyPreview.fingerprint });
  const publish = async (base: number | null) => {
    const head = await api(author, 'POST', `${root}draft/head-review/`, { revision: draft.revision });
    const fields = { revision: head.draft_revision, expected_digest: head.candidate_digest, expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint };
    const ack = await api(author, 'POST', `${root}draft/acknowledge-head/`, fields);
    return (await api(author, 'POST', `${root}draft/publish/`, { ...fields, revision: ack.revision, review_fingerprint: ack.review_fingerprint, base_version: base, note: '', acknowledge_warnings: true })).version;
  };
  const versions = [await publish(null)];
  for (const number of [2, 3]) {
    draft = await api(author, 'POST', `${root}draft/start/`, { revision: 0, from_version: number - 1 });
    draft = await api(author, 'PUT', `${root}draft/`, { revision: draft.revision, base_version: number - 1, description: 'Synthetic recovery release fixture', note: '', files: [{ path: 'AGENTS.md', content: `Synthetic policy revision ${number}. Reconcile completed business operations.\n` }] });
    versions.push(await publish(number - 1));
  }
  const packet = async (version: any, actor: any = author) => api(actor, 'POST', 'release-requests/preview/', {
    resource_id: created.context_id, kind: 'agent_context', candidate_version_id: version.id,
    label: 'production', target: null, evidence: [], external_pipeline: null,
  });
  const submit = async (preview: any, actor: any = author, recipient: any = reviewer) => api(actor, 'POST', 'release-requests/', {
    packet: preview.packet, expected_fingerprint: preview.fingerprint, recipient_ids: [recipient.userId], revision: 0,
  });
  const approve = async (preview: any, submitted: any, actor: any = reviewer) => api(actor, 'POST', `release-requests/${submitted.id}/decisions/`, {
    revision: submitted.revision, expected_fingerprint: preview.fingerprint, decision: 'approve', comment: 'Synthetic recovery prerequisite review',
  });
  const execute = async (preview: any, submitted: any, actor: any = reviewer) => api(actor, 'POST', `release-requests/${submitted.id}/execute/`, {
    revision: submitted.revision, expected_fingerprint: preview.fingerprint,
  });
  const move = async (version: any, requester: any = author, approver: any = reviewer) => {
    const preview = await packet(version, requester);
    const submitted = await submit(preview, requester, approver);
    await approve(preview, submitted, approver);
    return execute(preview, submitted, approver);
  };
  await move(versions[1]);
  const binding = await api(author, 'POST', `${root}bindings/`, { name: `${name}-unobserved`, kind: 'runtime', selector: { label: 'production' }, update_mode: 'new_thread', profile: 'ctxhub-support', profile_version: '0.1.0', repository: null, expected_report_interval_seconds: 3600 });
  const leafState = await api(reviewer, 'GET', `${leaf.skill.id}/`);
  await api(reviewer, 'DELETE', `${leaf.skill.id}/`, { expected_release_revision: leafState.release_revision });
  const safety = await api(reviewer, 'GET', `version-safety/${versions[2].id}/`);
  await api(reviewer, 'PUT', `version-safety/${versions[2].id}/`, { revision: safety.revision, quarantined: true, reason: 'Synthetic unsafe recovery alternative', replacement_version_id: versions[0].id });
  const refused = await send(author, 'POST', 'release-requests/preview/', { resource_id: created.context_id, kind: 'agent_context', candidate_version_id: versions[2].id, label: 'production', target: null, evidence: [], external_pipeline: null });
  expect(refused.response.ok(), 'quarantine must deny release preview').toBe(false);
  expect(JSON.stringify(refused.body)).toContain('context_quarantined');
  const closure = await api(reviewer, 'GET', `${root}versions/1/inspection/`);
  expect(closure.distribution_blocked).toBe(false);
  const retained = await probe.pg<any>('SELECT d.child_skill_id::text, d.child_version_id::text, s.deleted FROM model_hub_hubskillcontextdependency d JOIN model_hub_hubskill s ON s.id=d.child_skill_id WHERE d.parent_version_id=$1', [versions[0].id]);
  expect(retained).toEqual([{ child_skill_id: leaf.skill.id, child_version_id: leaf.version.id, deleted: true }]);
  const stalePreview = await packet(versions[0]);
  const staleRequest = await submit(stalePreview);
  await approve(stalePreview, staleRequest);
  // Production moves away and back through other candidates, so the approved stale request stays approved and only the
  // compare-and-swap check can refuse it.
  await move(versions[0]);
  await move(versions[1]);
  const eventCount = async () => (await probe.pg<any>('SELECT count(*)::int AS count FROM model_hub_hubskilllabelevent WHERE skill_id=$1', [created.context_id]))[0].count;
  const before = await eventCount();
  const stale = await send(reviewer, 'POST', `release-requests/${staleRequest.id}/execute/`, { revision: staleRequest.revision, expected_fingerprint: stalePreview.fingerprint });
  expect(stale.response.status(), 'ABA must reject the stale reviewed release revision').toBe(409);
  expect(JSON.stringify(stale.body), 'the approved stale packet is refused by the current-state check, not by its status').toMatch(/review_stale|release_conflict/);
  expect(await eventCount()).toBe(before);
  const freshPreview = await packet(versions[0]);
  const fresh = await submit(freshPreview);
  // The fresh packet from the same author closes the stale approved request, so no reviewer has to open and decline it.
  expect((await api(reviewer, 'GET', `release-requests/${staleRequest.id}/`)).status).toBe('superseded');
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 } });
  await context.addInitScript(authInitScript, { access: reviewer.tokens.access, refresh: reviewer.tokens.refresh, organizationId: reviewer.organizationId, workspaceId: reviewer.workspaceId });
  await context.addInitScript(id => sessionStorage.setItem('workspaceOrgId', id), reviewer.organizationId);
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  try {
    await page.goto('/dashboard/skills');
    await page.getByRole('tab', { name: 'Reviews', exact: true }).click();
    // The inbox can contain earlier executed and deliberately stale requests.
    // Choose by the stable request URL, not by a fixture-supplied navigation.
    const requestLink = page.locator(`a[href*="request=${fresh.id}"]`);
    if (await requestLink.count()) await requestLink.click();
    else {
      // Current inbox rows expose a button; the latest submitted row is first.
      await page.getByRole('button', { name, exact: true }).first().click();
    }
    await expect(page).toHaveURL(new RegExp(`request=${fresh.id}`));
    const acknowledged = page.getByRole('checkbox', { name: "I've checked this version, where it's going, the evidence and what the release will change.", exact: true });
    await expect(acknowledged).toBeEnabled();
    await expect(page.getByText('Original reviewed synthetic policy.', { exact: false }).first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('j6-lower-release-review.png') });
    await acknowledged.check();
    const decision = page.waitForResponse(res => res.url().endsWith(`/release-requests/${fresh.id}/decisions/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Approve release', exact: true }).click();
    expect((await decision).ok()).toBe(true);
    // The acknowledgement is keyed to the exact packet revision and fingerprint, so it carries
    // across pending -> approved for the unchanged revision; any renewal clears it (J4).
    await expect(acknowledged).toBeChecked();
    await page.getByRole('button', { name: 'Release to production', exact: true }).click();
    const executed = page.waitForResponse(res => res.url().endsWith(`/release-requests/${fresh.id}/execute/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Move production', exact: true }).click();
    expect((await executed).ok()).toBe(true);
    const receipt = await api(reviewer, 'GET', `release-requests/${fresh.id}/receipt/`);
    const event = await probe.pg<any>('SELECT from_version, to_version, actor_id::text FROM model_hub_hubskilllabelevent WHERE id=$1 AND skill_id=$2', [receipt.post_move_state.event_id, created.context_id]);
    expect(event, 'independent lower-number event oracle').toEqual([{ from_version: 2, to_version: process.env.J6_ORACLE_FAULT === 'rollback' ? 2 : 1, actor_id: reviewer.userId }]);
    expect(await eventCount()).toBe(before + 1);
    const state = await api(reviewer, 'GET', `${root}release-state/`);
    expect(state.latest_version).toBe(3);
    expect(state.labels.find((row: any) => row.name === 'production').version_id).toBe(versions[0].id);
    const consumers = await api(reviewer, 'GET', `${root}consumers/`);
    expect(consumers.results.find((row: any) => row.binding.id === binding.id).states).toEqual(['unknown']);
    await page.reload();
    await expect(page.getByText('Release receipt', { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('j6-lower-release-receipt.png') });
    await testInfo.attach('j6-rollback-observations', { body: JSON.stringify({ evidence_class: 'synthetic fixture', native_qualified: false, complete_job_qualified: false, versions, retained, closure, quarantine: { status: refused.response.status(), body: refused.body }, aba: { status: stale.response.status(), body: stale.body, events_before: before }, receipt, event, consumers }), contentType: 'application/json' });
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) await page.screenshot({ path: testInfo.outputPath('j6-rollback-failure.png') }).catch(() => {});
    await context.close();
  }
});
