import { randomUUID } from 'node:crypto';
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import pg from 'pg';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue } from '../../../lib/skills-r3/canonical.mjs';

export async function readSkillResult({ apiUrl, headers, path, signal }) {
  const response = await fetch(`${apiUrl}/model-hub/skills/${path}`, { headers, signal });
  requireValue(response.ok, 'Independent public observation unavailable');
  return (await response.json()).result;
}

/** J4 public-UI driver (synthetic fixture). Three people use the product UI:
 * the workspace owner configures review authority and the application target
 * (cold only), the author publishes and submits, and the independent reviewer
 * arrives through Skills -> Reviews, decides, renews and releases. Races are
 * injected by the environment through public APIs or the test-image retention
 * clock seam; they are recorded as faults, not as operator actions, and every
 * refused attempt, renewal and re-approval the operator performs is counted.
 * API/PG witnesses are read independently of what the UI displays. No
 * competitive claim is made here.
 * Private VM input: admin/author/reviewer {access,refresh,organizationId,
 * workspaceId,userId,name}, cohort, contextId, contextName, baselineVersion,
 * manifestPath, targetName, bindingId (configured), evidenceIds, appUrl,
 * apiUrl, pgUrl, outputDir, provenance.
 */
export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J4', 'J4 runs only on the approved cloud Linux VM');
  requireValue(process.env.J4_DRIVER_INPUT, 'Provide a private cloud-local driver input');
  const input = JSON.parse(await readFile(process.env.J4_DRIVER_INPUT, 'utf8'));
  for (const key of ['appUrl', 'apiUrl', 'pgUrl']) requireValue(new URL(input[key]).hostname === 'localhost', 'Use the owned full stack only');
  requireValue(new Set([input.admin.userId, input.author.userId, input.reviewer.userId]).size === 3, 'Independent owner, author and reviewer required');
  requireValue(input.cohort === registration.cohort && input.evidenceIds.length === 2, 'Driver input differs from preregistration');
  const output = resolve(input.outputDir, runId);
  await mkdir(output, { recursive: true });
  const contexts = [];
  let browser;
  const pages = {};
  let published;
  let submitted;
  let detail;
  let receipt;
  let bindingId = input.bindingId;
  let baselineObservation;
  const refusals = [];
  const restored = { role: null, binding: null };
  const db = new pg.Client({ connectionString: input.pgUrl });
  let connected = false;
  const sql = async (text, values) => {
    if (!connected) { await db.connect(); connected = true; }
    return (await db.query(text, values)).rows;
  };
  const headers = (actor) => ({ Authorization: `Bearer ${actor.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId });
  const read = (path, signal, actor = input.reviewer) => readSkillResult({ apiUrl: input.apiUrl, headers: headers(actor), path, signal });
  // Environment-only calls (fault injection and control probes). Never an operator action.
  const environment = async (actor, method, path, body, base = '/model-hub/skills/') => {
    const response = await fetch(`${input.apiUrl}${base}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body),
      headers: { ...headers(actor), 'Content-Type': 'application/json', 'Idempotency-Key': randomUUID() } });
    const value = await response.json().catch(() => ({}));
    return { status: response.status, code: value.code ?? null, result: value.result };
  };
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    return digest(value);
  };
  const open = async (actor) => {
    if (!browser) browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 }, recordVideo: { dir: resolve(output, 'video'), size: { width: 1440, height: 900 } } });
    contexts.push(context);
    await context.addInitScript((user) => {
      localStorage.setItem('accessToken', user.access);
      localStorage.setItem('refreshToken', user.refresh);
      localStorage.setItem('rememberMe', 'true');
      sessionStorage.setItem('organizationId', user.organizationId);
      sessionStorage.setItem('workspaceId', user.workspaceId);
      sessionStorage.setItem('workspaceOrgId', user.organizationId);
    }, actor);
    const page = await context.newPage();
    page.setDefaultTimeout(45_000);
    return page;
  };
  const page = async (role) => { pages[role] = pages[role] || await open(input[role]); return pages[role]; };
  const ack = (target) => target.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true });
  const responseFor = (target, suffix, method = 'POST') => {
    const pending = target.waitForResponse((res) => new URL(res.url()).pathname.endsWith(suffix) && res.request().method() === method, { timeout: 60_000 });
    pending.catch(() => {});
    return pending;
  };
  const decisionFor = (value) => ({ packet_sha256: digest(value.packet), preconditions_sha256: digest({ fingerprint: value.fingerprint, revision: value.revision }) });
  const labelEvents = () => sql('SELECT id::text FROM model_hub_hubskilllabelevent WHERE skill_id=$1 AND to_version=$2', [input.contextId, published.number]);
  const shot = (target, name) => target.screenshot({ path: resolve(output, `${name}.png`) });
  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      const actorFor = { 'configure-review-authority': 'admin', 'publish-candidate': 'author', 'share-and-submit': 'author' };
      const actor = actorFor[stage.id] || 'reviewer';
      const op = new MeasuredOperator({ ledger, actor, step: stage.id, phase: stage.phase, signal });
      const navigate = (target, name, url, operator = op) => operator.transition(name, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: operator.actor, step: stage.id, phase: stage.phase, target: name }, () => target.goto(url)));
      // Open a context from Skills -> Agent contexts by its visible name; no ID is typed.
      const openContext = async (target, name, operator = op) => {
        await navigate(target, 'skills-home', '/dashboard/skills', operator);
        await operator.transition('context-library', () => operator.click(target.getByRole('tab', { name: 'Agent contexts', exact: true }), 'open-context-library'));
        await operator.transition(name, () => operator.click(target.getByRole('button', { name: input.contextName, exact: true }), 'open-context'));
        await expect(target.getByRole('tab', { name: 'Consumers', exact: true })).toBeVisible();
      };
      const choose = async (target, label, option, name, operator = op) => {
        await operator.click(target.getByLabel(label, { exact: true }), `${name}-open`);
        await operator.click(target.getByRole('option', { name: option, exact: typeof option === 'string' }), `${name}-select`);
      };
      const stamp = async (name, value, decision = undefined, source = 'api') => {
        const artifactHash = await record(name, value);
        ledger.stage({ step: stage.id, phase: stage.phase, source, artifactHash, actionSequences: [], ...(decision ? { decision } : {}) });
      };
      const cancel = () => { void browser?.close().catch(() => {}); };
      signal.addEventListener('abort', cancel, { once: true });
      // Reviewer helpers. Each is a counted operator primitive.
      // The reviewer's attestation for an unchanged revision persists across
      // the pending -> approved refresh; it is clicked (and counted) only
      // when the UI actually requires it.
      const attestIfRequired = async (reviewer, operator, name) => {
        if (!(await ack(reviewer).isChecked())) await operator.click(ack(reviewer), name);
        await expect(ack(reviewer)).toBeChecked();
      };
      // (UX iteration 3, REV1/REV7) Digests and identities sit under named disclosures ("Compared content", "Version details",
      // and "Details" in the share confirmation). The same visibility assertions run once the disclosure is open; the
      // disclosure is read inside the inspection body, not counted as an operator primitive, so action counts do not rise.
      const reveal = async (scope, name) => {
        const toggle = scope.getByRole('button', { name, exact: true }).first();
        if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
      };
      const attempt = async (reviewer, operator, name) => {
        await attestIfRequired(reviewer, operator, `${name}-acknowledge`);
        await operator.click(reviewer.getByRole('button', { name: 'Release to production', exact: true }), `${name}-open-release`);
        const response = responseFor(reviewer, `/release-requests/${submitted.id}/execute/`);
        await operator.click(reviewer.getByRole('button', { name: 'Move production', exact: true }), `${name}-move`);
        const result = await response;
        // The confirmation closes on every outcome; the refusal shows beside the decision.
        await expect(reviewer.getByRole('button', { name: 'Move production', exact: true })).toHaveCount(0);
        return { status: result.status(), code: (await result.json().catch(() => ({}))).code ?? null };
      };
      // The server refusal opens the renewal inspection directly (no click).
      // The reviewer inspects every change, acknowledges it and approves the
      // renewed revision in one decision bound to the inspected fingerprint.
      const renew = async (reviewer, operator, name) => {
        const dialog = reviewer.getByRole('dialog').filter({ hasText: 'Renew release review' });
        await expect(dialog.getByText('Renew release review', { exact: true })).toBeVisible();
        // Independent read of exactly what the dialog shows.
        const preview = await read(`release-requests/${submitted.id}/renewal/`, signal);
        const items = preview.changes.map((row) => row.item);
        const removed = preview.removed_evidence.map((row) => `removed-${row.source_id}`);
        requireValue(items.length + removed.length > 0, 'Renewal must expose at least one changed precondition');
        const decision = decisionFor({ packet: preview.packet, fingerprint: preview.fingerprint, revision: preview.revision + 1 });
        const kept = preview.packet.evidence.map((ref) => ref.source_id);
        await operator.review(`${name}-changed-preconditions`, [...items.map((_, i) => `change-${i}`), ...removed.map((_, i) => `removed-${i}`)], removed.map((_, i) => `removed-${i}`), async () => {
          for (const item of items) await expect(dialog.locator(`tr[data-field="${item}"]`)).toBeVisible();
          for (const row of preview.removed_evidence) await expect(dialog.getByText(new RegExp(`${row.source_id} is removed from the renewed request`))).toBeVisible();
          await expect(dialog.getByText(kept.length ? new RegExp(`renewed request keeps ${kept.length} results? for the same candidate`) : /renewed request keeps no evidence/)).toBeVisible();
          for (const id of kept) await expect(dialog.getByTitle(id, { exact: true })).toBeVisible();
        }, decision);
        await operator.click(dialog.getByRole('checkbox', { name: 'I inspected every change. Approval is required again.', exact: true }), `${name}-acknowledge-changes`);
        const renewed = responseFor(reviewer, `/release-requests/${submitted.id}/renewal/`);
        const decided = responseFor(reviewer, `/release-requests/${submitted.id}/decisions/`);
        await operator.approve(`${name}-renewed-approve`, ['release-packet'], () => dialog.getByRole('button', { name: 'Approve renewed revision', exact: true }).click(), decision);
        requireValue([200, 201].includes((await renewed).status()) && (await decided).ok(), 'Renewal or renewed approval rejected');
        await expect(dialog).toHaveCount(0);
        detail = await read(`release-requests/${submitted.id}/`, signal);
        requireValue(detail.status === 'approved' && detail.fingerprint === preview.fingerprint && detail.revision === preview.revision + 1, 'Renewal silently differs from the inspected changes');
        await expect(reviewer.getByRole('button', { name: 'Renew review', exact: true })).toHaveCount(0, { timeout: 60_000 });
        return { preview, decision };
      };
      const approve = async (reviewer, operator, name, decision) => {
        await expect(ack(reviewer)).toBeEnabled();
        await attestIfRequired(reviewer, operator, `${name}-acknowledge`);
        const response = responseFor(reviewer, `/release-requests/${submitted.id}/decisions/`);
        await operator.approve(`${name}-approve`, ['release-packet'], () => reviewer.getByRole('button', { name: 'Approve release', exact: true }).click(), decision);
        requireValue((await response).ok(), 'Reviewer decision rejected');
        await expect(reviewer.getByRole('button', { name: 'Release to production', exact: true })).toBeEnabled({ timeout: 60_000 });
      };
      // Each race must be refused for its own reason, never a leftover one.
      const EXPECTED = { 'policy-aba': [409, 'release_conflict'], 'target-config-change': [409, 'impact_changed'],
        'late-annotation': [409, 'annotation_conflict'], 'role-loss': [403, 'permission_denied'], 'evidence-expiry': [410, 'evidence_expired'] };
      const refused = async (fault, scenario, result, extra = {}) => {
        const events = await labelEvents();
        const row = { fault, scenario, attempt: result, expected: EXPECTED[fault], events: events.length, ...extra };
        refusals.push(row);
        requireValue(result.status === EXPECTED[fault][0] && result.code === EXPECTED[fault][1] && events.length === 0, `Stale approval executed or refused for another reason under ${fault}`);
        ledger.fault(fault, scenario);
        return row;
      };
      try {
        if (stage.id === 'configure-review-authority') {
          // Workspace owner grants the reviewer release authority and registers
          // the application target from its build manifest. Cold only.
          const admin = await page('admin');
          await navigate(admin, 'user-management', '/dashboard/settings/user-management');
          await op.fill(admin.getByPlaceholder('Search by name or email'), input.reviewer.name, 'find-reviewer');
          const row = admin.locator('.ag-row', { hasText: input.reviewer.name }).first();
          await expect(admin.locator('.ag-center-cols-container .ag-row')).toHaveCount(1);
          await op.click(row.locator('.ag-group-contracted:visible').first(), 'expand-reviewer-workspaces');
          await expect(admin.locator('.ag-details-row, .ag-full-width-row').first()).toBeVisible();
          await op.click(admin.getByRole('combobox').filter({ hasText: 'Workspace Viewer' }), 'open-workspace-role');
          const roleResponse = admin.waitForResponse((res) => /members\/role\/$/.test(new URL(res.url()).pathname) && res.request().method() === 'POST');
          await op.click(admin.getByRole('option', { name: 'Workspace Admin', exact: true }), 'grant-workspace-admin');
          requireValue((await roleResponse).ok(), 'Reviewer authority not granted');
          await openContext(admin, 'context-detail');
          await op.transition('consumers', () => op.click(admin.getByRole('tab', { name: 'Consumers', exact: true }), 'open-consumers'));
          await op.click(admin.getByRole('button', { name: 'Register application target', exact: true }), 'open-target-registration');
          const dialog = admin.getByRole('dialog');
          const chooser = admin.waitForEvent('filechooser');
          await op.click(dialog.getByRole('button', { name: 'Choose runtime manifest', exact: true }), 'choose-runtime-manifest');
          await (await chooser).setFiles(input.manifestPath);
          await expect(dialog.getByText('refund_agent.app:invoke', { exact: true })).toBeVisible();
          // The visible name defaults from the build entrypoint; nothing typed.
          await expect(dialog.getByLabel('Target name', { exact: true })).toHaveValue('refund_agent');
          const created = responseFor(admin, `/contexts/${input.contextId}/bindings/`);
          await op.click(dialog.getByRole('button', { name: 'Register target', exact: true }), 'register-target');
          const binding = (await (await created).json()).result;
          bindingId = binding.id;
          await expect(admin.getByText('Registered refund_agent · configuration r1', { exact: true })).toBeVisible();
          await expect(dialog).toHaveCount(0);
          const authority = await sql(`SELECT m.level FROM accounts_workspacemembership m WHERE m.user_id=$1 AND m.workspace_id=$2 AND m.is_active`, [input.reviewer.userId, input.reviewer.workspaceId]);
          const target = await sql('SELECT id::text, revision, configuration_digest FROM model_hub_hubskillcontextbinding WHERE id=$1', [bindingId]);
          requireValue(authority[0]?.level === 8 && target.length === 1 && target[0].configuration_digest === binding.configuration_digest, 'Configured authority/target not observed');
          await shot(admin, 'configure-review-authority');
          await stamp('configured-authority', { authority, target }, undefined, 'pg');
          return;
        }
        if (stage.id === 'publish-candidate') {
          const author = await page('author');
          await openContext(author, 'own-context');
          // The saved draft opens straight into its publication review.
          await op.transition('publication-review', () => op.click(author.getByRole('button', { name: 'Review saved draft', exact: true }), 'open-publication-review'));
          const draft = await read(`contexts/${input.contextId}/draft/`, signal, input.author);
          await op.review('candidate-against-origin', ['AGENTS.md'], [], () => expect(author.getByText('Never promise a refund before the order status is checked.', { exact: false }).first()).toBeVisible());
          await op.click(author.getByRole('checkbox', { name: 'I reviewed these changes against the current published version.', exact: true }), 'acknowledge-candidate');
          const warnings = author.getByRole('checkbox', { name: 'I acknowledge the static warnings for this exact candidate.', exact: true });
          if (await warnings.count()) await op.click(warnings, 'acknowledge-warnings');
          // One step: the same reviewed identity acknowledges the head,
          // publishes the immutable candidate and opens the review request
          // with resource and candidate carried forward. Production unchanged.
          const publication = responseFor(author, `/contexts/${input.contextId}/draft/publish/`);
          await op.transition('review-request-composer', () => op.click(author.getByRole('button', { name: 'Publish and request review', exact: true }), 'publish-and-request-review'));
          const result = await publication;
          requireValue(result.status() === 201, 'Candidate publication failed');
          published = (await result.json()).result.version;
          requireValue(published.digest === draft.digest, 'Published bytes differ from the reviewed draft');
          baselineObservation = await sql('SELECT v.number FROM model_hub_hubskilllabel l JOIN model_hub_hubskillversion v ON v.id=l.version_id WHERE l.skill_id=$1 AND l.name=$2', [input.contextId, 'production']);
          requireValue(baselineObservation[0]?.number === input.baselineVersion, 'Publication changed production');
          await shot(author, 'publish-candidate');
          await stamp('publication-observation', { version: published, production: baselineObservation }, undefined, 'pg');
          return;
        }
        if (stage.id === 'share-and-submit') {
          const author = await page('author');
          // Carried forward from publication: kind, resource and candidate.
          // The only registered target is preselected and visible.
          await expect(author.getByRole('heading', { name: 'Request release review', exact: true })).toBeVisible();
          const carriedCandidate = author.getByRole('group', { name: 'Carried release candidate', exact: true });
          await expect(carriedCandidate).toContainText(input.contextName);
          await expect(carriedCandidate).toContainText(new RegExp(` → v${published.number}(?!\\d)`));
          await expect(carriedCandidate).toContainText(/ · configuration r\d+$/);
          // One explicit decision shares and attaches every listed
          // exact-candidate result; the confirmation names each record. The
          // same decision continues to the exact packet (server-prepared, not
          // sent); the author still inspects and attests it below.
          // Preselected only when exactly one authorized reviewer exists.
          if ((await author.getByLabel('Assigned reviewer', { exact: true }).textContent())?.trim() !== input.reviewer.name) await choose(author, 'Assigned reviewer', input.reviewer.name, 'reviewer');
          const attach = author.getByRole('checkbox', { name: /^Attach Text comparison/ });
          await expect(attach).toHaveCount(input.evidenceIds.length);
          await expect(author.getByText('No matching test evidence. Not tested is not a pass.', { exact: true })).toHaveCount(0);
          await expect(author.getByRole('button', { name: 'Share and attach', exact: true })).toHaveCount(input.evidenceIds.length);
          // Capture without scrolling or adding an operator action. Private
          // results must occupy the same evidence table, never an empty state.
          await author.screenshot({ path: resolve(output, 'composer-private-evidence.png'), fullPage: true });
          await op.click(author.getByRole('button', { name: `Share and attach ${input.evidenceIds.length} results`, exact: true }), 'share-all-evidence');
          const confirmShare = author.getByRole('dialog');
          await reveal(confirmShare, 'Details');
          for (const id of input.evidenceIds) await expect(confirmShare.getByTitle(id, { exact: true })).toBeVisible();
          await expect(confirmShare.getByText('Private', { exact: true })).toHaveCount(input.evidenceIds.length);
          const prepared = responseFor(author, '/release-requests/preview/');
          await op.click(confirmShare.getByRole('button', { name: 'Share and attach', exact: true }), 'confirm-sharing-and-attach');
          const preview = (await (await prepared).json()).result;
          await expect(author.getByRole('dialog')).toHaveCount(0);
          requireValue(preview.packet.evidence.length === input.evidenceIds.length
            && preview.packet.evidence.every((ref) => input.evidenceIds.includes(ref.source_id)), 'Prepared packet differs from the shared selection');
          await op.review('author-packet', ['candidate', 'transition', 'target', ...preview.packet.evidence.map((_, i) => `evidence-${i}`)], [], () => expect(ack(author)).toBeEnabled(), decisionFor({ ...preview, revision: 1 }));
          await op.click(ack(author), 'acknowledge-author-packet');
          const sent = responseFor(author, '/release-requests/');
          await op.handoff('assigned-reviewer', () => op.click(author.getByRole('button', { name: 'Send review request', exact: true }), 'submit-exact-review-packet'));
          const response = await sent;
          requireValue(response.status() === 201, 'Author submission failed');
          submitted = (await response.json()).result;
          detail = await read(`release-requests/${submitted.id}/`, signal, input.author);
          requireValue(detail.author_id === input.author.userId && detail.packet.candidate_version_id === published.id
            && detail.packet.target?.binding_id === bindingId && detail.packet.evidence.length === input.evidenceIds.length
            && detail.packet.evidence.every((ref) => input.evidenceIds.includes(ref.source_id) && ref.visibility === 'workspace'), 'Submitted identity differs');
          await shot(author, 'share-and-submit');
          await stamp('author-submission', detail);
          // The owner and author are done: finalize their recordings now so
          // teardown stays inside the runner's bounded deadline.
          for (const role of ['admin', 'author']) if (pages[role]) await pages[role].context().close();
          return;
        }
        const reviewer = await page('reviewer');
        if (stage.id === 'reviewer-inbox-arrival') {
          await navigate(reviewer, 'reviewer-skills', '/dashboard/skills');
          await op.transition('reviewer-inbox', () => op.click(reviewer.getByRole('tab', { name: 'Reviews', exact: true }), 'open-reviews'));
          const inbox = await read('review-inbox/', signal);
          requireValue(inbox.results.some((row) => row.id === submitted.id && row.unread), 'Assigned unread request absent');
          await shot(reviewer, 'reviewer-inbox-arrival');
          await op.transition('assigned-request', () => op.click(reviewer.getByRole('button', { name: input.contextName, exact: true }), 'open-assigned-packet'));
          await expect(ack(reviewer)).toBeVisible();
          await stamp('reviewer-arrival', inbox);
          return;
        }
        if (stage.id === 'review-packet') {
          detail = await read(`release-requests/${submitted.id}/`, signal);
          const decision = decisionFor(detail);
          const wire = { resource_id: input.contextId, kind: 'agent_context', candidate_version_id: published.id, label: 'production', target: detail.packet.target,
            evidence: detail.packet.evidence.map((ref) => ({ source: ref.source, source_id: ref.source_id, annotation_revision: ref.annotation_revision })),
            external_pipeline: null, expected_fingerprint: detail.fingerprint };
          const response = await fetch(`${input.apiUrl}/model-hub/skills/release-requests/inspection/`, { method: 'POST', body: JSON.stringify(wire), headers: { ...headers(input.reviewer), 'Content-Type': 'application/json' }, signal });
          requireValue(response.ok, 'Independent inspection unavailable');
          const inspection = (await response.json()).result;
          await op.review('inspect-exact-diff', ['AGENTS.md'], [], async () => {
            await expect(reviewer.getByText('Never promise a refund before the order status is checked.', { exact: false }).first()).toBeVisible();
          }, decision);
          await op.review('inspect-target-configuration', ['target-binding', 'target-configuration'], [], async () => {
            await reveal(reviewer, 'Version details');
            await expect(reviewer.getByTitle(detail.packet.target.configuration_digest, { exact: true }).first()).toBeVisible();
            requireValue(inspection.target_configuration?.entrypoint === 'refund_agent.app:invoke', 'Target configuration differs');
          }, decision);
          // Correct decision rule for this synthetic fixture: approve only if
          // every attached result is complete, has no regression and no
          // unresolved human disagreement. Values come from the server.
          const outcomes = inspection.summaries.map((row) => ({ id: row.source_id, planned: row.planned, graded: row.graded, incomplete: row.incomplete, regressions: row.regressions, disagreements: row.disagreements }));
          await op.review('inspect-evidence-outcomes', outcomes.map((_, i) => `evidence-${i}`), [], async () => {
            for (const row of outcomes) {
              await expect(reviewer.getByTitle(row.id, { exact: true }).first()).toBeVisible();
              await expect(reviewer.getByText(`${row.graded}/${row.planned} attempts graded · ${row.incomplete} incomplete · ${row.regressions} regressions`, { exact: true }).first()).toBeVisible();
            }
          }, decision);
          const correct = outcomes.every((row) => row.graded === row.planned && row.planned > 0 && row.incomplete === 0 && row.regressions === 0 && row.disagreements === 0);
          requireValue(correct, 'Fixture evidence does not support approval; the correct decision would be request changes');
          await op.review('inspect-capabilities-and-impact', ['capabilities', 'impact'], [], async () => {
            await reveal(reviewer, 'Compared content');
            await expect(reviewer.getByTitle(inspection.capabilities.before_digest, { exact: true }).first()).toBeVisible();
            await expect(reviewer.getByTitle(inspection.capabilities.after_digest, { exact: true }).first()).toBeVisible();
          }, decision);
          await shot(reviewer, 'review-packet');
          await stamp('reviewed-packet', { request: detail, inspection, outcomes, correctDecision: 'approve', humanComprehensionMeasured: false }, decision);
          return;
        }
        if (stage.id === 'record-decision') {
          const decision = decisionFor(detail);
          await approve(reviewer, op, 'initial', decision);
          const decisions = await sql('SELECT d.actor_id::text, d.decision, p.fingerprint, p.revision FROM model_hub_hubskillreleasedecision d JOIN model_hub_hubskillreleasepacket p ON p.id=d.packet_id WHERE p.request_id=$1', [submitted.id]);
          requireValue(decisions.length === 1 && decisions[0].actor_id === input.reviewer.userId && decisions[0].fingerprint === detail.fingerprint, 'Independent decision attribution differs');
          await shot(reviewer, 'record-decision');
          await stamp('recorded-decision', decisions, decision, 'pg');
          return;
        }
        if (stage.id === 'stale-execution-control') {
          // Author cannot release even an approved packet (separate authority).
          const authorProbe = await environment(input.author, 'POST', `release-requests/${submitted.id}/execute/`, { revision: detail.revision, expected_fingerprint: detail.fingerprint });
          requireValue(authorProbe.status === 403 && (await labelEvents()).length === 0, 'Author released without authority');
          // 1. Safety policy A -> B -> A: the owner quarantines the candidate
          // and clears it again. The visible state returns to A, but the
          // security revision advanced. (The v1.1 label-protection API is
          // skill-only, so the context policy race uses the safety policy.)
          const safety = await environment(input.admin, 'GET', `version-safety/${published.id}/`);
          const quarantined = await environment(input.admin, 'PUT', `version-safety/${published.id}/`, { revision: safety.result.revision, quarantined: true, reason: 'Synthetic safety hold during review', replacement_version_id: null });
          const cleared = await environment(input.admin, 'PUT', `version-safety/${published.id}/`, { revision: quarantined.result?.revision, quarantined: false, reason: 'Synthetic safety hold cleared', replacement_version_id: null });
          requireValue(safety.status === 200 && quarantined.status === 200 && cleared.status === 200, `Policy race not applied ${safety.status} ${quarantined.status} ${quarantined.code} ${cleared.status} ${cleared.code}`);
          await refused('policy-aba', 'safety-quarantine-a-b-a', await attempt(reviewer, op, 'policy-aba'));
          await renew(reviewer, op, 'policy-aba');
          // 2. Application target configuration A -> B -> A (revision moves).
          const binding = await read(`contexts/bindings/${bindingId}/`, signal, input.admin).catch(async () => (await read(`contexts/${input.contextId}/bindings/`, signal, input.admin)).results.find((row) => row.id === bindingId));
          const body = (configuration) => ({ name: binding.name, kind: binding.kind, selector: binding.selector, update_mode: binding.update_mode, profile: binding.profile,
            profile_version: binding.profile_version, repository: binding.repository, expected_report_interval_seconds: binding.expected_report_interval_seconds, configuration });
          const toolsChanged = await environment(input.admin, 'PATCH', `contexts/bindings/${bindingId}/`, { revision: binding.revision, binding: body({ ...binding.configuration, tools_digest: `sha256:${'f'.repeat(64)}` }) });
          const toolsBack = await environment(input.admin, 'PATCH', `contexts/bindings/${bindingId}/`, { revision: toolsChanged.result.revision, binding: body(binding.configuration) });
          requireValue(toolsChanged.status === 200 && toolsBack.status === 200 && toolsBack.result.configuration_digest === binding.configuration_digest, 'Target race not applied');
          restored.binding = { digest: toolsBack.result.configuration_digest, revision: toolsBack.result.revision };
          await refused('target-config-change', 'target-configuration-a-b-a', await attempt(reviewer, op, 'target-config'));
          await renew(reviewer, op, 'target-config');
          // 3. A late human annotation on attached evidence.
          const annotated = detail.packet.evidence[0];
          const late = await environment(input.admin, 'POST', `evidence/skills_text/${annotated.source_id}/annotations/`, { annotation_revision: annotated.annotation_revision, check_id: null, attempt_id: null, verdict: 'agree', cited_text: '', comment: 'Late confirmation from the support lead.' });
          requireValue(late.status === 201 || late.status === 200, 'Annotation race not applied');
          await refused('late-annotation', 'late-agree-annotation', await attempt(reviewer, op, 'late-annotation'));
          await renew(reviewer, op, 'late-annotation');
          // 4. Transient role loss of the approver/releaser.
          const lost = await environment(input.admin, 'POST', 'organization/members/role/', { user_id: input.reviewer.userId, ws_level: 1, workspace_id: input.admin.workspaceId }, '/accounts/');
          requireValue(lost.status === 200, 'Role race not applied');
          const roleAttempt = await attempt(reviewer, op, 'role-loss');
          // Control: a still-authorized but unassigned releaser cannot address
          // the request at all (404), so it cannot carry the demoted approval.
          const ownerProbe = await environment(input.admin, 'POST', `release-requests/${submitted.id}/execute/`, { revision: detail.revision, expected_fingerprint: detail.fingerprint });
          await refused('role-loss', 'approver-demoted-to-viewer', roleAttempt, { ownerProbe: { status: ownerProbe.status, code: ownerProbe.code } });
          requireValue([403, 404].includes(ownerProbe.status) && (await labelEvents()).length === 0, 'Demoted approval executed by another releaser');
          const back8 = await environment(input.admin, 'POST', 'organization/members/role/', { user_id: input.reviewer.userId, ws_level: 8, workspace_id: input.admin.workspaceId }, '/accounts/');
          requireValue(back8.status === 200, 'Role not restored');
          restored.role = 8;
          // 5. Retention expiry of one attached result (test-image clock seam).
          const expiring = detail.packet.evidence[1];
          const expired = await environment(input.admin, 'POST', 'model-hub/e2e/j4/evidence-clock/', { source: 'skills_text', source_id: expiring.source_id }, '/');
          requireValue(expired.status === 200 && expired.result.payload_purged === true, 'Expiry not applied');
          await refused('evidence-expiry', 'retention-clock-advance-and-purge', await attempt(reviewer, op, 'evidence-expiry'), { expired: expired.result });
          await shot(reviewer, 'stale-execution-control');
          await stamp('stale-refusals', { authorProbe: { status: authorProbe.status, code: authorProbe.code }, refusals }, undefined, 'pg');
          return;
        }
        if (stage.id === 'renewed-review') {
          const renewed = await renew(reviewer, op, 'expiry');
          requireValue(renewed.preview.removed_evidence.length === 1 && detail.packet.evidence.length === 1, 'Expired evidence was not removed explicitly');
          // The renewed packet lost evidence: the reviewer re-reads what
          // remains (candidate, remaining outcome, target) on the page.
          const remaining = detail.packet.evidence[0];
          const wire = { resource_id: input.contextId, kind: 'agent_context', candidate_version_id: published.id, label: 'production', target: detail.packet.target,
            evidence: detail.packet.evidence.map((ref) => ({ source: ref.source, source_id: ref.source_id, annotation_revision: ref.annotation_revision })),
            external_pipeline: null, expected_fingerprint: detail.fingerprint };
          const inspected = await fetch(`${input.apiUrl}/model-hub/skills/release-requests/inspection/`, { method: 'POST', body: JSON.stringify(wire), headers: { ...headers(input.reviewer), 'Content-Type': 'application/json' }, signal });
          requireValue(inspected.ok, 'Independent renewed inspection unavailable');
          const outcome = (await inspected.json()).result.summaries.find((row) => row.source_id === remaining.source_id);
          requireValue(outcome && outcome.graded === outcome.planned && outcome.planned > 0 && outcome.incomplete === 0 && outcome.regressions === 0 && outcome.disagreements === 0,
            'Remaining evidence does not support approval of the renewed packet');
          await op.review('renewed-packet', ['candidate', 'remaining-evidence', 'target'], [], async () => {
            await expect(reviewer.getByText('Never promise a refund before the order status is checked.', { exact: false }).first()).toBeVisible();
            await expect(reviewer.getByTitle(remaining.source_id, { exact: true }).first()).toBeVisible();
            await expect(reviewer.getByText(`${outcome.graded}/${outcome.planned} attempts graded · ${outcome.incomplete} incomplete · ${outcome.regressions} regressions`, { exact: true }).first()).toBeVisible();
            await reveal(reviewer, 'Version details');
            await expect(reviewer.getByTitle(detail.packet.target.configuration_digest, { exact: true }).first()).toBeVisible();
            await expect(ack(reviewer)).toBeEnabled();
          }, renewed.decision);
          await shot(reviewer, 'renewed-review');
          await stamp('renewed-review', await read(`release-requests/${submitted.id}/`, signal), renewed.decision);
          return;
        }
        if (stage.id === 'authorized-release') {
          const decision = decisionFor(detail);
          await attestIfRequired(reviewer, op, 'acknowledge-final-release');
          await op.click(reviewer.getByRole('button', { name: 'Release to production', exact: true }), 'prepare-authorized-release');
          const response = responseFor(reviewer, `/release-requests/${submitted.id}/execute/`);
          await op.approve('authorize-exact-transition', ['release-packet'], () => reviewer.getByRole('button', { name: 'Move production', exact: true }).click(), decision);
          const result = await response;
          requireValue(result.ok(), 'Authorized release rejected');
          receipt = (await result.json()).result;
          await shot(reviewer, 'authorized-release');
          await stamp('authorized-release', receipt, decision);
          return;
        }
        if (stage.id === 'inspect-receipt') {
          await op.transition('durable-receipt', () => ledger.measure({ dimension: 'A', primitive: 'click', actor, step: stage.id, phase: stage.phase, target: 'reopen-durable-receipt' }, () => reviewer.reload()));
          await op.review('pre-and-post-move', ['pre-move-currentness', 'post-move-pointer'], [], async () => {
            await expect(reviewer.getByText('Release receipt', { exact: true })).toBeVisible();
            await expect(reviewer.getByText('Before the move', { exact: true })).toBeVisible();
            await expect(reviewer.getByText('After the move', { exact: true })).toBeVisible();
          });
          const durable = await read(`release-requests/${submitted.id}/receipt/`, signal);
          requireValue(digest(durable) === digest(receipt), 'Durable receipt differs');
          await shot(reviewer, 'inspect-receipt');
          await stamp('durable-receipt', durable);
          return;
        }
        throw new Error('Unimplemented registered stage');
      } catch (error) {
        for (const [index, context] of contexts.entries()) for (const [pageIndex, item] of context.pages().entries()) await item.screenshot({ path: resolve(output, `blocked-${stage.id}-${index}-${pageIndex}.png`) }).catch(() => {});
        await record(`blocked-${stage.id}`, { stage: stage.id, reason: String(error?.message || error).slice(0, 2000), refusals, completeJobQualified: false });
        throw error;
      } finally { signal.removeEventListener('abort', cancel); }
    },
    async observe(check, { signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      let source = 'pg'; let raw; let value;
      if (check.id === 'inbox-recipient-set') {
        raw = await sql('SELECT recipient_id::text FROM model_hub_hubskillreleaserecipient WHERE request_id=$1', [submitted.id]);
        value = raw.map((row) => row.recipient_id);
      } else if (check.id === 'unauthorized-unread-count') {
        source = 'api'; raw = await read('review-inbox/', signal, input.author); value = raw.unread_count;
      } else if (check.id === 'decision-actor-packet') {
        raw = await sql('SELECT d.actor_id::text, d.decision, p.fingerprint, p.revision FROM model_hub_hubskillreleasedecision d JOIN model_hub_hubskillreleasepacket p ON p.id=d.packet_id WHERE p.request_id=$1 ORDER BY p.revision', [submitted.id]);
        const final = raw.filter((row) => Number(row.revision) === detail.revision);
        value = final.length === 1 && final[0].actor_id === input.reviewer.userId && final[0].decision === 'approve' && final[0].fingerprint === detail.fingerprint
          && raw.every((row) => row.actor_id === input.reviewer.userId);
      } else if (check.id === 'stale-release-refused') {
        const faults = ['policy-aba', 'target-config-change', 'late-annotation', 'role-loss', 'evidence-expiry'];
        const executions = await sql('SELECT response FROM model_hub_hubskillcontextoperation WHERE operation=$1 AND result_resource_id=$2', ['execute_release', submitted.id]);
        raw = { refusals, executions: executions.length, faults };
        value = faults.every((fault) => refusals.some((row) => row.fault === fault && row.attempt.status === row.expected[0] && row.attempt.code === row.expected[1] && row.events === 0))
          && executions.length === 1 && executions[0].response.revision === detail.revision;
      } else if (check.id === 'exact-release-event') {
        raw = await sql('SELECT id::text, from_version, to_version, actor_id::text FROM model_hub_hubskilllabelevent WHERE skill_id=$1 AND to_version=$2', [input.contextId, published.number]);
        value = raw.length === 1 && raw[0].id === receipt.post_move_state.event_id && raw[0].actor_id === input.reviewer.userId && raw[0].from_version === input.baselineVersion;
      } else if (check.id === 'pre-move-currentness') {
        raw = await sql('SELECT response FROM model_hub_hubskillcontextoperation WHERE operation=$1 AND result_resource_id=$2', ['execute_release', submitted.id]);
        value = raw.length === 1 && raw[0].response.currentness_at_action?.packet_fingerprint === detail.fingerprint
          && raw[0].response.currentness_at_action.release_revision < raw[0].response.post_move_state.release_revision;
      } else if (check.id === 'production-unchanged-before-release') {
        raw = baselineObservation; value = raw?.length === 1 && raw[0].number === input.baselineVersion;
      } else throw new Error('Unobserved check');
      return { source, value, artifact_sha256: await record(`oracle-${check.id}`, raw) };
    },
    async teardown() {
      await Promise.all(contexts.map((context) => context.close().catch(() => {})));
      await browser?.close();
      // Verify the environment was returned to the configured state: the
      // reviewer's authority and the registered target configuration.
      let environmentRestored = true;
      try {
        if (restored.role !== null) {
          const level = await sql('SELECT level FROM accounts_workspacemembership WHERE user_id=$1 AND workspace_id=$2 AND is_active', [input.reviewer.userId, input.reviewer.workspaceId]);
          environmentRestored &&= level[0]?.level === restored.role;
        }
        if (restored.binding !== null) {
          const row = await sql('SELECT configuration_digest, revision FROM model_hub_hubskillcontextbinding WHERE id=$1 AND retired_at IS NULL', [bindingId]);
          environmentRestored &&= row[0]?.configuration_digest === restored.binding.digest;
        }
      } catch { environmentRestored = false; }
      if (connected) await db.end();
      await record('teardown', { environmentRestored, role: restored.role, binding: restored.binding });
      return { qualified: environmentRestored && (!browser || !browser.isConnected()) };
    },
  };
}
