import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { promisify } from 'node:util';
import { chromium, expect } from '@playwright/test';
import pg from 'pg';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { canonical, digest, requireValue, sha256 } from '../../../lib/skills-r3/canonical.mjs';
import { attachGuest, GUEST_EVIDENCE_CLASS } from '../../../lib/skills-r3/j6-guest.mjs';
import { REFUND_REQUEST, VERIFY_REQUEST, entrypoint, guestEnvironment, moveProduction, requestFor } from '../../../lib/skills-r3/j6-scenario.mjs';

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

// Public forensic APIs are observations, not a substitute for missing operator
// controls. Never use a manifest URL returned by the server as a fetch target.
export async function readCompleteClosure(read, contextId, number, signal) {
  requireValue(UUID.test(contextId) && Number.isSafeInteger(number) && number > 0, 'Exact recovery identity required');
  const prefix = `contexts/${contextId}/versions/${number}/inspection/`;
  let first;
  const rows = [];
  let offset = 0;
  for (let page = 0; page < 400; page++) {
    const value = await read(`${prefix}?file_offset=${offset}&file_limit=25`, signal);
    requireValue(value?.distribution_blocked === false, 'Recovery release is unavailable or quarantined');
    const manifest = value.manifest;
    requireValue(manifest?.resource_id === contextId && manifest.version === number && manifest.file_offset === offset, 'Inspection identity or offset differs');
    if (!first) first = value;
    requireValue(manifest.version_id === first.manifest.version_id && manifest.digest === first.manifest.digest
      && manifest.file_count === first.manifest.file_count && canonical(value.descriptor) === canonical(first.descriptor), 'Closure changed during inspection');
    requireValue(Array.isArray(manifest.files) && manifest.files.length > 0, 'Incomplete closure page');
    rows.push(...manifest.files);
    requireValue(rows.length <= 10000 && new Set(rows.map(row => row.path)).size === rows.length, 'Invalid complete closure');
    if (manifest.next_file_offset === null) break;
    requireValue(manifest.next_file_offset === rows.length && manifest.next_file_offset > offset, 'Manifest pagination did not advance');
    offset = manifest.next_file_offset;
  }
  requireValue(rows.length === first.manifest.file_count, 'Complete closure was not read');
  const files = [];
  for (const file of rows) {
    let content = '';
    let charOffset = 0;
    let totalChars;
    let finished = false;
    for (let chunk = 0; chunk < 1024; chunk++) {
      const value = await read(`${prefix}files/?path=${encodeURIComponent(file.path)}&offset=${charOffset}&max_bytes=32768`, signal);
      requireValue(value.version_id === first.manifest.version_id && value.digest === first.manifest.digest
        && value.path === file.path && value.sha256 === file.sha256 && value.offset === charOffset && typeof value.text === 'string', 'File chunk identity differs');
      totalChars ??= value.total_chars;
      requireValue(totalChars === value.total_chars, 'File size changed');
      content += value.text;
      charOffset += [...value.text].length; // Server offsets count Unicode code points, not UTF-16 units.
      if (value.eof === true) { finished = true; break; }
      requireValue(value.next_offset === charOffset && value.text.length > 0, 'Chunk pagination did not advance');
    }
    requireValue(finished && charOffset === totalChars && sha256(Buffer.from(content, 'utf8')) === file.sha256, 'Incomplete or changed recovery file');
    files.push({ path: file.path, content });
  }
  return { inspection: first, files };
}


const FAULTS = {
  rollback: 'lower-number-rollback', aba: 'release-aba', child: 'retired-child', quarantine: 'quarantined-alternative',
  expired: 'expired-credential', lost: 'lost-mapping', outage: 'registry-outage-after-tool', unobserved: 'unobserved-consumer',
};
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const run = promisify(execFile);

/**
 * Registered recovery qualification on the owned cloud full stack.
 *
 * The retained incident, three release identities, a retired child, a
 * quarantined alternative, paused application threads and the checked-in project
 * are PREPARED by SKILLS-E2E-063 (configured initial conditions, not counted).
 * Everything from detecting the incident to the final cohort view is driven
 * here through the public UI, the packaged `fi.context` CLI and the packaged
 * application inside a VM container guest. Business counts, model-boundary
 * digests and session identity come from the guest sidecar's independent
 * ledgers and from Postgres, never from the application's own report.
 *
 * Evidence class: synthetic fixture, VM container guest, not E2B/Simulation.
 */
export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J6', 'J6 runs on the assigned cloud Linux VM');
  requireValue(process.env.J6_DRIVER_INPUT, 'Private J6 driver input is required');
  requireValue(registration.cohort === 'configured', 'Cold application qualification is not implemented');
  const input = JSON.parse(await readFile(process.env.J6_DRIVER_INPUT, 'utf8'));
  for (const key of ['appUrl', 'apiUrl']) {
    const url = new URL(input[key]);
    requireValue(['localhost', '127.0.0.1'].includes(url.hostname) && ['http:', 'https:'].includes(url.protocol)
      && !url.username && !url.password && !url.search && !url.hash && url.pathname === '/', 'Use the owned full-stack origin');
  }
  requireValue(UUID.test(input.contextId) && Object.values(input.bindings).every((binding) => UUID.test(binding.id)), 'Declare exact recovery context and cohort');
  const guest = attachGuest(input.guest);
  const output = resolve(input.outputDir, runId);
  await mkdir(resolve(output, 'shots'), { recursive: true });
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), JSON.stringify(value, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    return digest(value);
  };
  const { author, reviewer } = input.actors;
  const header = (actor) => ({ Authorization: `Bearer ${actor.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId });
  const api = input.apiUrl.replace(/\/$/, '');
  const call = async (actor, method, path, body, signal, extra = {}) => {
    const response = await fetch(`${api}/model-hub/skills/${path}`, { method, headers: { ...header(actor), 'Content-Type': 'application/json', 'Idempotency-Key': randomUUID(), ...extra },
      body: body === undefined ? undefined : JSON.stringify(body), signal, redirect: 'error' });
    const text = await response.text();
    let parsed = null;
    try { parsed = text ? JSON.parse(text) : null; } catch { parsed = null; }
    return { status: response.status, body: parsed, result: parsed?.result };
  };
  const read = async (path, signal) => {
    const response = await call(reviewer, 'GET', path, undefined, signal);
    requireValue(response.status >= 200 && response.status < 300, 'Independent public recovery observation unavailable');
    return response.result;
  };
  const [v1, v2, v3, v4] = input.versions;
  const members = await fetch(`${api}/accounts/organization/members/?page=1&limit=100`, { headers: header(reviewer), redirect: 'error' }).then((r) => r.json());
  const me = (members.result?.results ?? []).find((row) => row.id === reviewer.userId);
  requireValue(me, 'The reviewer is not a listed organization member');
  const reviewerName = me.name || me.email;
  const context = `contexts/${input.contextId}/`;
  const state = { stale: null, events: {}, threads: {}, timings: {}, faults: {}, reconstruction: [], cohortBefore: null };
  const browsers = [];
  const pages = {};
  const open = async (name, actor) => {
    if (pages[name]) return pages[name];
    const browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript((who) => {
      localStorage.setItem('accessToken', who.access);
      localStorage.setItem('refreshToken', who.refresh);
      localStorage.setItem('rememberMe', 'true');
      sessionStorage.setItem('organizationId', who.organizationId);
      sessionStorage.setItem('workspaceId', who.workspaceId);
      sessionStorage.setItem('workspaceOrgId', who.organizationId);
    }, actor);
    const page = await ctx.newPage();
    page.setDefaultTimeout(45_000);
    browsers.push({ browser, ctx });
    pages[name] = page;
    return page;
  };
  const shot = (page, name) => page.screenshot({ path: resolve(output, 'shots', `${name}.png`) });
  const opsFor = (stage, ledger, signal, actor) => new MeasuredOperator({ ledger, actor, step: stage.id, phase: stage.phase, signal });
  const guestEnv = (consume, report, binding) => guest.envFile(`${binding}-${randomUUID().slice(0, 8)}`, guestEnvironment({ consume, report, bindingId: input.bindings[binding].id }));
  // A measured operator command whose non-zero exit is data (a paused thread), not a crash.
  const command = (stage, ledger, actor, target, argv, accepted = [0], options = {}) => ledger.measure({ dimension: 'A', primitive: 'command', actor, step: stage.id, phase: stage.phase, target }, async () => {
    const started = performance.now();
    let result;
    try { const { stdout, stderr } = await run(argv[0], argv.slice(1), { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024, ...options }); result = { code: 0, stdout, stderr }; }
    catch (error) { result = { code: error.code, stdout: error.stdout ?? '', stderr: error.stderr ?? '' }; }
    state.timings[target] = Math.round(performance.now() - started);
    ledger.observation({ step: stage.id, id: `${target}/output`, artifactHash: sha256(result.stdout) });
    requireValue(accepted.includes(result.code), `Command ${target} exited ${result.code}`);
    return result;
  });
  const guestCommand = (stage, ledger, actor, target, envFile, argv, accepted = [0]) => command(stage, ledger, actor, target, ['docker', ...guest.execArgs(envFile, argv)], accepted);
  const guestJson = async (path) => JSON.parse(await guest.read(path));
  const hostCli = (stage, ledger, target, args, credential) => command(stage, ledger, 'incident-operator', target, [input.python, '-m', 'fi.context', ...args], [0], { cwd: input.project, env: { PATH: process.env.PATH, HOME: process.env.HOME,
    FI_CONTEXT_ORIGIN: input.apiUrl.replace(/\/$/, ''), FI_CONTEXT_ORGANIZATION_ID: author.organizationId, FI_CONTEXT_WORKSPACE_ID: author.workspaceId, FI_CONTEXT_CREDENTIAL: credential } });
  // A navigation is a measured click plus a screen transition, as for a person.
  const navigate = (op, ledger, stage, page, url, target) => op.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: op.actor, step: stage.id, phase: stage.phase, target }, () => page.goto(url)));
  const apiOk = (signal) => async (actor, method, path, data) => { const r = await call(actor, method, path, data, signal); requireValue(r.status >= 200 && r.status < 300, `${method} ${path} returned ${r.status}`); return r.result; };
  const sessionRows = (bindingId) => pgRows('SELECT id::text, version_id::text, digest, incarnation_id::text AS instance_id, credential_id::text FROM model_hub_hubskillcontextsession WHERE binding_id=$1 ORDER BY created_at', [bindingId]);
  const labelEvents = () => pgRows('SELECT id, from_version, to_version FROM model_hub_hubskilllabelevent WHERE skill_id=$1 ORDER BY id', [input.contextId]);
  const pg = new pg.Client({ connectionString: process.env.E2E_PG_URL ?? 'postgresql://futureagi:futureagi@localhost:25432/futureagi' });
  let pgReady = false;
  async function pgRows(text, values = []) {
    if (!pgReady) { await pg.connect(); pgReady = true; }
    return (await pg.query(text, values)).rows;
  }
  const consumers = async (signal) => {
    const results = [];
    for (let page = 1; page <= 100; page += 1) {
      const value = await read(`${context}consumers/?page=${page}&page_size=100`, signal);
      results.push(...value.results);
      if (page >= value.total_pages) break;
    }
    return Object.fromEntries(Object.entries(input.bindings).map(([key, binding]) => [key, results.find((row) => row.binding.id === binding.id)]));
  };
  const faultSeen = (ledger, key, artifactHash, observed) => {
    ledger.fault(FAULTS[key], `${FAULTS[key]}-1`);
    state.faults[key] = { fault: FAULTS[key], scenario: `${FAULTS[key]}-1`, evidence_sha256: artifactHash, observed };
  };
  const decisionFor = (packet, preconditions) => ({ packet_sha256: digest(packet), preconditions_sha256: digest(preconditions) });

  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      if (signal.aborted) throw new Error('Journey cancelled');
      const operator = opsFor(stage, ledger, signal, 'incident-operator');
      const admin = opsFor(stage, ledger, signal, 'release-reviewer');
      const authorPage = await open('author', author);
      const reviewerPage = await open('reviewer', reviewer);
      const contextUrl = `/dashboard/skills/contexts/${input.contextId}`;

      if (stage.id === 'detect-incident') {
        await operator.transition('recovery-cohort', () => ledger.measure({ dimension: 'A', primitive: 'click', actor: 'incident-operator', step: stage.id, phase: stage.phase, target: 'open-declared-context' },
          () => authorPage.goto(`${contextUrl}?version=3`)));
        await operator.transition('recovery-view', () => operator.click(authorPage.getByRole('tab', { name: 'Recovery', exact: true }), 'open-recovery-view'));
        const cohort = await consumers(signal);
        requireValue(cohort.A.latest_failure?.diagnostic_codes.includes('refund_window_regression'), 'The retained incident receipt is not visible');
        requireValue(cohort.U.states.length === 1 && cohort.U.states[0] === 'unknown', 'The never-observed consumer must start unknown');
        const table = authorPage.getByRole('table', { name: 'Recovery cohort' });
        await expect(table).toBeVisible();
        await expect(table.getByText('refund_window_regression', { exact: false })).toBeVisible();
        state.cohortBefore = Object.fromEntries(Object.entries(cohort).map(([key, row]) => [key, { states: row.states, failure: row.latest_failure?.diagnostic_codes ?? null }]));
        const hash = await record('incident-cohort', { evidence_class: GUEST_EVIDENCE_CLASS, cohort: state.cohortBefore });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash, actionSequences: [] });
        await shot(authorPage, '01-incident-cohort');
        return;
      }

      if (stage.id === 'choose-eligible-release') {
        const releases = authorPage.getByRole('table', { name: 'Recovery releases' });
        await expect(releases).toBeVisible();
        // Quarantined alternative: the UI refuses it and the public API independently refuses a packet.
        await expect(authorPage.getByRole('button', { name: 'Choose v2', exact: true })).toBeDisabled();
        const refused = await call(author, 'POST', 'release-requests/preview/', { resource_id: input.contextId, kind: 'agent_context', candidate_version_id: v2.id, label: 'production', target: null, evidence: [], external_pipeline: null }, signal);
        requireValue(refused.status >= 400 && JSON.stringify(refused.body).includes('context_quarantined'), 'The quarantined alternative must be refused');
        const quarantineHash = await record('quarantined-alternative', { status: refused.status, code: 'context_quarantined', ui_choose_disabled: true });
        state.quarantine = { status: refused.status, code: 'context_quarantined' };
        faultSeen(ledger, 'quarantine', quarantineHash, state.quarantine);
        // Retired child: its source is gone, the exact bytes remain in the eligible release.
        const retired = await call(reviewer, 'GET', `${input.leaves[0].skillId}/`, undefined, signal);
        requireValue(retired.status === 404, 'The refund child source must be retired');
        await expect(authorPage.getByText('Source retired:', { exact: false }).first()).toBeVisible();
        await operator.click(authorPage.getByRole('button', { name: 'Choose v1', exact: true }), 'choose-eligible-release');
        const closure = await readCompleteClosure(read, input.contextId, 1, signal);
        const production = await readCompleteClosure(read, input.contextId, 3, signal);
        const before = new Map(production.files.map((file) => [file.path, sha256(Buffer.from(file.content, 'utf8'))]));
        const changed = closure.files.filter((file) => before.get(file.path) !== sha256(Buffer.from(file.content, 'utf8'))).map((file) => file.path);
        requireValue(changed.includes('AGENTS.md'), 'The recovery release must differ from the regressed one');
        const files = authorPage.getByRole('table', { name: 'Recovery closure files' });
        await expect(files).toBeVisible();
        const items = closure.files.map((_, index) => `closure-file-${index}`);
        const exceptions = closure.files.flatMap((file, index) => (changed.includes(file.path) ? [`closure-file-${index}`] : []));
        const retiredHash = await record('retired-child-closure', { retired_status: retired.status, closure_paths: closure.files.map((file) => file.path), changed });
        const decision = decisionFor({ candidate: closure.files.map((file) => [file.path, sha256(Buffer.from(file.content, 'utf8'))]), production: [...before] },
          { candidate_version_id: v1.id, production_version_id: v3.id, release_revision: (await read(`${context}release-state/`, signal)).release_revision });
        await operator.review('complete-recovery-closure', items, exceptions, async () => {
          for (const file of closure.files) {
            const cell = files.getByRole('cell', { name: file.path, exact: true });
            await cell.scrollIntoViewIfNeeded();
            await expect(cell).toBeInViewport();
            const row = files.getByRole('row').filter({ has: cell });
            await expect(row.getByText(changed.includes(file.path) ? 'Changed' : 'Unchanged', { exact: true })).toBeVisible();
          }
          for (const path of changed) {
            const block = authorPage.getByLabel(`${path} change from production`);
            await block.scrollIntoViewIfNeeded();
            await expect(block).toBeInViewport();
            const added = closure.files.find((file) => file.path === path).content.split('\n')[0];
            await expect(block).toContainText(added);
          }
        }, decision);
        await operator.click(authorPage.getByRole('checkbox', { name: /I reviewed the complete v1 closure/ }), 'acknowledge-complete-review');
        await shot(authorPage, '02-closure-review');
        await record('eligible-closure', closure);
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: retiredHash, actionSequences: [], decision });
        faultSeen(ledger, 'child', retiredHash, { retired_status: retired.status });
        return;
      }

      if (stage.id === 'cas-rollback') {
        const submit = async (cycle) => {
          await operator.click(authorPage.getByRole('button', { name: 'Preview rollback packet', exact: true }), `preview-rollback-packet-${cycle}`);
          await operator.click(authorPage.getByRole('combobox', { name: 'Reviewer' }), `open-reviewer-list-${cycle}`);
          await operator.click(authorPage.getByRole('option', { name: reviewerName, exact: true }), `choose-reviewer-${cycle}`);
          await operator.click(authorPage.getByRole('button', { name: 'Submit rollback for review', exact: true }), `submit-rollback-${cycle}`);
          await expect(authorPage.getByText('Rollback request submitted.', { exact: false })).toBeVisible();
        };
        const latestRequest = async () => {
          const inbox = await read('review-inbox/?page=1&page_size=50', signal);
          return inbox.results.filter((row) => row.subject_id === input.contextId && row.candidate_version_id === v1.id && row.status === 'pending')
            .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0];
        };
        await submit(1);
        const stale = await latestRequest();
        requireValue(stale, 'The submitted rollback request is not in the reviewer inbox');
        const eventsBefore = await labelEvents();
        // ABA: another releaser moves production away and back before this packet is reviewed.
        for (const target of [v4, v3]) await moveProduction({ api: apiOk(signal), author, reviewer, contextId: input.contextId, version: target });
        const afterAba = await labelEvents();
        requireValue(afterAba.length === eventsBefore.length + 2, 'The ABA hop must record exactly two label events');
        // Reviewer opens the stale request: the UI refuses it, and an independent execute is rejected with no event.
        await operator.handoff('release-reviewer', async () => {});
        await navigate(admin, ledger, stage, reviewerPage, '/dashboard/skills', 'open-skills-area');
        await admin.click(reviewerPage.getByRole('tab', { name: 'Reviews', exact: true }), 'open-reviews-inbox');
        const staleRow = reviewerPage.getByRole('button', { name: input.contextName, exact: true }).first();
        await admin.click(staleRow, 'open-stale-request');
        await expect(reviewerPage.getByText('Review is stale or unavailable.', { exact: false })).toBeVisible();
        await shot(reviewerPage, '03-stale-aba-review');
        const rejected = await call(reviewer, 'POST', `release-requests/${stale.id}/execute/`, { revision: stale.revision, expected_fingerprint: stale.fingerprint }, signal);
        const afterReject = await labelEvents();
        requireValue(rejected.status === 409 && afterReject.length === afterAba.length, 'The stale ABA packet must be rejected without a label event');
        state.stale = { status: rejected.status, label_events_added: afterReject.length - afterAba.length, hop_events: afterAba.slice(eventsBefore.length).map((row) => [row.from_version, row.to_version]) };
        const abaHash = await record('aba-rejection', state.stale);
        faultSeen(ledger, 'aba', abaHash, state.stale);
        // Operator starts a fresh review against the moved release revision.
        await operator.click(authorPage.getByRole('button', { name: 'Refresh recovery facts', exact: true }), 'refresh-recovery-facts');
        await operator.click(authorPage.getByRole('checkbox', { name: /I reviewed the complete v1 closure/ }), 'acknowledge-complete-review-again');
        await submit(2);
        const fresh = await latestRequest();
        requireValue(fresh && fresh.id !== stale.id, 'The fresh rollback request is not in the reviewer inbox');
        const detail = await read(`release-requests/${fresh.id}/`, signal);
        const decision = decisionFor({ packet: detail.fingerprint }, { candidate: v1.id, from_version: 3, release_revision: (await read(`${context}release-state/`, signal)).release_revision });
        await operator.handoff('release-reviewer', async () => {});
        await admin.transition('reviews-inbox', () => admin.click(reviewerPage.getByRole('tab', { name: 'Reviews', exact: true }), 'reopen-reviews-inbox'));
        await admin.click(reviewerPage.getByRole('button', { name: input.contextName, exact: true }).first(), 'open-fresh-request');
        const ack = reviewerPage.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true });
        await expect(ack).toBeEnabled();
        await admin.click(ack, 'acknowledge-fresh-packet');
        await admin.approve('approve-fresh-packet', ['packet'], () => reviewerPage.getByRole('button', { name: 'Approve exact packet', exact: true }).click(), decision);
        await expect(ack).not.toBeChecked();
        await admin.click(ack, 'acknowledge-before-release');
        await admin.click(reviewerPage.getByRole('button', { name: 'Release approved packet', exact: true }), 'release-approved-packet');
        await admin.click(reviewerPage.getByRole('button', { name: 'Move production', exact: true }), 'move-production');
        await expect(reviewerPage.getByText('Durable release receipt', { exact: true })).toBeVisible();
        await shot(reviewerPage, '04-rollback-receipt');
        const events = await labelEvents();
        const rollbackEvents = events.filter((row) => row.from_version === 3 && row.to_version === 1);
        requireValue(rollbackEvents.length === 1, 'Exactly one lower-number rollback event is required');
        const release = await read(`${context}release-state/`, signal);
        requireValue(release.labels.find((label) => label.name === 'production').version_id === v1.id && release.latest_version === 4, 'Production must point at the lower-number release');
        state.rollback = { from_version: 3, to_version: 1, events: rollbackEvents.length };
        const rollbackHash = await record('rollback-event', { events, release_revision: release.release_revision });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'pg', artifactHash: rollbackHash, actionSequences: [], decision });
        faultSeen(ledger, 'rollback', rollbackHash, state.rollback);
        return;
      }

      if (stage.id === 'locked-local-restore') {
        // An administrator issues the replacement credential (the operator cannot) and hands the one-time secret over.
        await operator.handoff('release-reviewer', async () => {});
        await navigate(admin, ledger, stage, reviewerPage, `${contextUrl}?version=1`, 'open-admin-context');
        await admin.click(reviewerPage.getByRole('tab', { name: 'Recovery', exact: true }), 'open-admin-recovery');
        await admin.click(reviewerPage.getByRole('button', { name: 'Choose v1', exact: true }), 'admin-choose-v1');
        await admin.click(reviewerPage.getByRole('button', { name: 'Issue replacement credential', exact: true }), 'issue-replacement-credential');
        const secretBox = reviewerPage.getByRole('textbox', { name: 'New credential (shown once)' });
        await expect(secretBox).toBeVisible();
        await shot(reviewerPage, '05-replacement-credential');
        state.replacement = await secretBox.inputValue();
        requireValue(typeof state.replacement === 'string' && state.replacement.length >= 32, 'The replacement credential was not shown');
        await operator.handoff('application-secret-store', async () => {});
        // Exact checked-in lock: restore the last reviewed commit's lock, preserve local work.
        const before = {
          local: sha256(await readFile(resolve(input.project, 'docs', 'local-notes.md'), 'utf8')),
          lock: sha256(await readFile(resolve(input.project, '.futureagi-context.lock.json'), 'utf8')),
        };
        await command(stage, ledger, 'incident-operator', 'export-retained-lock', ['git', '-C', input.project, 'checkout', input.goodCommit, '--', '.futureagi-context.lock.json']);
        const plan = resolve(input.project, '..', 'restore-plan.json');
        await hostCli(stage, ledger, 'plan-locked-restore', ['restore', '--allow-preview', '--project', '.', '--plan', plan], state.replacement);
        await hostCli(stage, ledger, 'inspect-restore-plan', ['inspect', '--plan', plan, '--diff'], state.replacement);
        await hostCli(stage, ledger, 'apply-locked-restore', ['apply', '--plan', plan, '--quiescent'], state.replacement);
        await hostCli(stage, ledger, 'verify-locked-restore', ['verify', '--project', '.'], state.replacement);
        const lock = JSON.parse(await readFile(resolve(input.project, '.futureagi-context.lock.json'), 'utf8'));
        const files = closureFiles(await readCompleteClosure(read, input.contextId, 1, signal));
        const managed = [];
        for (const file of lock.files ?? []) managed.push(file.path);
        const notes = await readFile(resolve(input.project, 'docs', 'local-notes.md'), 'utf8');
        const agents = await readFile(resolve(input.project, 'AGENTS.md'), 'utf8').catch(() => null);
        requireValue(sha256(notes) === before.local, 'Local work was changed by the restore');
        requireValue(lock.version_id === v1.id || lock.identity?.version_id === v1.id || JSON.stringify(lock).includes(v1.id), 'The lock does not retain the recovery release');
        requireValue(agents === null || agents.includes('Reviewed support policy revision 1'), 'The restored root differs from the recovery release');
        const hash = await record('locked-restore', { lock_sha256_before: before.lock, lock_sha256_after: sha256(JSON.stringify(lock)), local_notes_preserved: true, closure_files: files.length, managed_paths: managed.length,
          timings_ms: { plan: state.timings['plan-locked-restore'], apply: state.timings['apply-locked-restore'] } });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'filesystem', artifactHash: hash, actionSequences: [] });
        return;
      }

      if (stage.id === 'interrupt-after-tool') {
        const order = input.orders.o1;
        await guest.faults({ available: true, arm_after_business: true });
        const reportA = input.credentials.report.A.secret;
        const envFile = await guestEnv(state.replacement, reportA, 'A');
        await guest.put('/state/requests/t1.json', JSON.stringify(requestFor(input.threads.t1, order)) + '\n');
        const paused = await guestCommand(stage, ledger, 'incident-operator', 'run-application-thread', envFile, entrypoint('/state/requests/t1.json', '/state/t1.sqlite', '/state/out/t1-1.json'), [2]);
        const out = await guestJson('/state/out/t1-1.json');
        requireValue(out.status === 'paused', 'The thread must pause when the registry is lost after the tool result');
        const business = await guest.business();
        const op = business.operations.filter((row) => row.order_id === order);
        requireValue(op.length === 1 && op[0].status === 'applied' && business.request_count === 1, 'The refund must be applied exactly once before the outage');
        const registry = (await guest.ledger()).registry;
        requireValue(registry.some((row) => row.outage === true), 'The registry outage was not observed at the boundary');
        const receipts = out.events.filter((event) => event.kind === 'tool_result');
        requireValue(receipts.length === 1, 'The business receipt must be persisted before the checkpoint advances');
        state.threads.t1 = { paused_checkpoint: out.checkpoint, session_id: out.session_id, version_id: out.version_id, receipt_digest: receipts[0].body.receipt_digest, exit_paused: paused.code };
        const hash = await record('outage-after-tool', { ...state.threads.t1, business_requests: business.request_count, outage_registry_calls: registry.filter((row) => row.outage).length });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'guest', artifactHash: hash, actionSequences: [] });
        faultSeen(ledger, 'outage', hash, { paused: true, checkpoint: out.checkpoint });
        return;
      }

      if (stage.id === 'resume-or-clean-thread') {
        const t = input.threads;
        await guest.faults({ available: true, arm_after_business: false });
        const reports = input.credentials.report;
        const inspect = async (key, target, binding, consume, thread) => {
          const envFile = await guestEnv(consume, reports[binding].secret, binding);
          await guestCommand(stage, ledger, 'incident-operator', target, envFile, ['python', '-m', 'ctxhub_support.lifecycle', 'inspect', '--state', `/state/${key}.sqlite`, '--thread', thread, '--output', `/state/out/${key}-inspect-${randomUUID().slice(0, 6)}.json`], [0]);
          return envFile;
        };
        // The latest inspect output for a thread, read from the guest (not from the command's stdout).
        const latest = async (key) => {
          const names = (await run('docker', ['exec', guest.names.guest, 'sh', '-c', `ls -t /state/out/${key}-inspect-*.json | head -1`], { encoding: 'utf8' })).stdout.trim();
          return guestJson(names);
        };
        const t1Env = await inspect('t1', 'inspect-paused-thread', 'A', state.replacement, t.t1);
        const t1Inspect = await latest('t1');
        requireValue(t1Inspect.resumable === true && t1Inspect.reason === 'original_identity_available' && t1Inspect.pending_tool_calls.length === 0, 'The paused thread must be resumable under its original identity');
        // Preconditions of the decision: what each blocked thread looks like before any reconstruction.
        const envE = await guestEnv(input.credentials.E.secret, reports.E.secret, 'E');
        const envL = await guestEnv(input.credentials.L.secret, reports.L.secret, 'L');
        const eSessions = await sessionRows(input.bindings.E.id);
        const eSession = eSessions[0];
        await expect.poll(async () => (await call({ ...author }, 'POST', `contexts/bindings/${input.bindings.E.id}/sessions/${eSession.id}/access-check/`, {}, signal, { Authorization: `Context ${input.credentials.E.secret}` })).status, { timeout: 330_000, intervals: [2000] }).toBeGreaterThanOrEqual(401);
        const eDenied = await call(author, 'POST', `contexts/bindings/${input.bindings.E.id}/sessions/${eSession.id}/access-check/`, {}, signal, { Authorization: `Context ${input.credentials.E.secret}` });
        requireValue([401, 403].includes(eDenied.status), 'The expired credential must be denied by the public access check');
        await guestCommand(stage, ledger, 'incident-operator', 'inspect-expired-thread', envE, ['python', '-m', 'ctxhub_support.lifecycle', 'inspect', '--state', '/state/t2.sqlite', '--thread', t.t2, '--output', '/state/out/t2-inspect.json']);
        const eInspect = await guestJson('/state/out/t2-inspect.json');
        requireValue(eInspect.resumable === false && eInspect.reason === 'authority_unavailable' && eInspect.durable_receipts.length === 1, 'The expired-credential thread must not be resumable');
        const expiredHash = await record('expired-credential', { inspect: { reason: eInspect.reason, receipts: eInspect.durable_receipts.length, checkpoint: eInspect.checkpoint }, access_check_status: eDenied.status });
        faultSeen(ledger, 'expired', expiredHash, { reason: eInspect.reason, status: eDenied.status });
        await guestCommand(stage, ledger, 'incident-operator', 'inspect-lost-thread', envL, ['python', '-m', 'ctxhub_support.lifecycle', 'inspect', '--state', '/state/t3.sqlite', '--thread', t.t3, '--output', '/state/out/t3-inspect.json']);
        const lInspect = await guestJson('/state/out/t3-inspect.json');
        requireValue(lInspect.known === false && lInspect.resumable === false && lInspect.reason === 'thread_state_missing', 'The lost-mapping thread must not be resumable');
        const businessBefore = await guest.business();
        requireValue([input.orders.o2, input.orders.o3].every((order) => businessBefore.operations.filter((row) => row.order_id === order && row.status === 'applied').length === 1), 'The executed refunds must be visible before reconstruction');
        const lostHash = await record('lost-mapping', { inspect: { known: lInspect.known, reason: lInspect.reason }, business_receipts_for_order: 1 });
        faultSeen(ledger, 'lost', lostHash, { reason: lInspect.reason });
        // The decision: resume T1 under its identity; reconstruct E and L as explicit clean threads.
        const decision = decisionFor({ resume: [t.t1], reconstruct: [t.t2, t.t3], requests: [VERIFY_REQUEST], copied_messages: 0 },
          { t1: [t1Inspect.checkpoint, t1Inspect.session_id], t2: eInspect.reason, t3: lInspect.reason, replay_refunds: false });
        await operator.recoveryDecision('resume-one-reconstruct-two', async () => {}, decision);
        await guestCommand(stage, ledger, 'incident-operator', 'resume-original-thread', t1Env, entrypoint('/state/requests/t1.json', '/state/t1.sqlite', '/state/out/t1-2.json'), [0]);
        const resumed = await guestJson('/state/out/t1-2.json');
        requireValue(resumed.status === 'completed' && resumed.session_id === state.threads.t1.session_id && resumed.version_id === v1.id, 'The resumed thread must keep its session and release identity');
        state.threads.t1.completed_checkpoint = resumed.checkpoint;
        // Close what can be closed so it is not counted as recovered.
        await guestCommand(stage, ledger, 'incident-operator', 'close-expired-thread', envE, ['python', '-m', 'ctxhub_support.lifecycle', 'close', '--state', '/state/t2.sqlite', '--thread', t.t2, '--reason', 'credential_expired', '--output', '/state/out/t2-closed.json']);
        // Reviewed clean threads. Reconstruction work is its own measured cost.
        const clean = {};
        for (const [key, binding, order, thread] of [['e', 'E', input.orders.o2, t.t2], ['l', 'L', input.orders.o3, t.t3]]) {
          const started = performance.now();
          const fields = { case_id: `reconstructed-${key}`, order_id: order, request: VERIFY_REQUEST };
          for (const [field, value] of Object.entries(fields)) {
            await ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'incident-operator', step: stage.id, phase: stage.phase, target: `reconstruct-${key}-${field}` }, async () => { fields[field] = value; });
          }
          await guest.put(`/state/requests/review-${key}.json`, JSON.stringify({ schema: 1, case: fields, reconstruction_reviewed: true }) + '\n');
          await guestCommand(stage, ledger, 'incident-operator', `prepare-clean-thread-${key}`, await guestEnv(state.replacement, reports[binding].secret, binding), ['python', '-m', 'ctxhub_support.recovery', `/state/requests/review-${key}.json`, '--output', `/state/requests/clean-${key}.json`]);
          const cleanEnv = await guestEnv(state.replacement, reports[binding].secret, binding);
          await guestCommand(stage, ledger, 'incident-operator', `run-clean-thread-${key}`, cleanEnv, entrypoint(`/state/requests/clean-${key}.json`, `/state/clean-${key}.sqlite`, `/state/out/clean-${key}.json`), [0]);
          clean[key] = { output: await guestJson(`/state/out/clean-${key}.json`), request: JSON.parse(await guest.read(`/state/requests/clean-${key}.json`)), elapsed_ms: Math.round(performance.now() - started), abandoned_thread: thread };
          requireValue(clean[key].output.status === 'completed' && clean[key].output.version_id === v1.id && clean[key].request.thread !== thread, 'The clean thread must run the recovery release as a new thread');
        }
        const businessAfter = await guest.business();
        requireValue(businessAfter.request_count === 3 && businessAfter.operations.length === 3, 'Clean threads must not replay a business action');
        state.reconstruction = Object.entries(clean).map(([key, value]) => ({ thread: key, elapsed_ms: value.elapsed_ms, copied_messages: 0, additional_business_requests: businessAfter.request_count - businessBefore.request_count }));
        state.threads.clean = Object.fromEntries(Object.entries(clean).map(([key, value]) => [key, { session_id: value.output.session_id, version_id: value.output.version_id, thread: value.request.thread }]));
        const hash = await record('resume-and-clean-threads', { t1: state.threads.t1, clean: state.threads.clean, reconstruction: state.reconstruction });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'guest', artifactHash: hash, actionSequences: [], decision });
        return;
      }

      if (stage.id === 'reconcile-business-action') {
        const ledgerRead = await ledger.measure({ dimension: 'A', primitive: 'command', actor: 'incident-operator', step: stage.id, phase: stage.phase, target: 'read-business-ledger' }, () => guest.business());
        const rows = await operator.reconcile('durable-receipts-against-business-ledger', async () => {
          const orders = [input.orders.o1, input.orders.o2, input.orders.o3];
          return orders.map((order) => ({ order, operations: ledgerRead.operations.filter((row) => row.order_id === order).length }));
        });
        requireValue(rows.every((row) => row.operations === 1) && ledgerRead.request_count === 3 && !ledgerRead.operations.some((row) => row.order_id === input.orders.o0), 'The business ledger must show one operation per executed order and none for the declined case');
        const hash = await record('business-reconciliation', { orders: rows, request_count: ledgerRead.request_count });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'guest', artifactHash: hash, actionSequences: [] });
        return;
      }

      if (stage.id === 'verify-original-case') {
        const reports = input.credentials.report;
        // The abandoned incident thread is closed, never counted as recovered.
        const oldEnv = await guestEnv(state.replacement, reports.A.secret, 'A');
        await guestCommand(stage, ledger, 'incident-operator', 'close-incident-thread', oldEnv, ['python', '-m', 'ctxhub_support.lifecycle', 'close', '--state', '/state/t0.sqlite', '--thread', input.threads.t0, '--reason', 'incident_superseded', '--output', '/state/out/t0-closed.json']);
        const fields = { case_id: 'original-case-recheck', order_id: input.orders.o0, request: REFUND_REQUEST };
        for (const field of Object.keys(fields)) await ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'incident-operator', step: stage.id, phase: stage.phase, target: `reconstruct-original-${field}` }, async () => {});
        await guest.put('/state/requests/review-o0.json', JSON.stringify({ schema: 1, case: fields, reconstruction_reviewed: true }) + '\n');
        await guestCommand(stage, ledger, 'incident-operator', 'prepare-original-case-thread', oldEnv, ['python', '-m', 'ctxhub_support.recovery', '/state/requests/review-o0.json', '--output', '/state/requests/clean-o0.json']);
        await guestCommand(stage, ledger, 'incident-operator', 'run-original-case-thread', oldEnv, entrypoint('/state/requests/clean-o0.json', '/state/clean-o0.sqlite', '/state/out/clean-o0.json'), [0]);
        const out = await guestJson('/state/out/clean-o0.json');
        const business = await guest.business();
        const o0 = business.operations.filter((row) => row.order_id === input.orders.o0);
        requireValue(out.status === 'completed' && out.version_id === v1.id && o0.length === 1 && o0[0].status === 'applied' && o0[0].amount === 25, 'The original case must be refunded exactly once under the recovery release');
        const model = (await guest.ledger()).model;
        const policy = (await guestJson('/state/out/clean-o0.json')).events.find((event) => event.kind === 'file_read' && event.body.path === 'references/policy.md');
        const lastModel = model.at(-1);
        const decided = model.filter((row) => row.decision.rule === 'refund_within_window').at(-1);
        requireValue(decided && decided.delivered_files.some((file) => file.skill_id === input.leaves[0].skillId && file.path === 'references/policy.md'), 'The retired child policy must be delivered at the model boundary');
        state.original = { status: o0[0].status, amount: o0[0].amount, rule: decided.decision.rule, delivered_root_sha256: decided.system_sha256, retired_child_bytes_delivered: true, policy_digest: policy?.body.content_digest ?? null, last_model_decision: lastModel.decision.rule };
        const hash = await record('original-case', { ...state.original, model_requests: model.length });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'guest', artifactHash: hash, actionSequences: [] });
        return;
      }

      if (stage.id === 'inspect-declared-cohort') {
        await operator.click(authorPage.getByRole('button', { name: 'Refresh recovery facts', exact: true }), 'refresh-final-cohort');
        const cohort = await consumers(signal);
        const table = authorPage.getByRole('table', { name: 'Recovery cohort' });
        await expect(table).toBeVisible();
        const verdicts = {};
        for (const [key, binding] of Object.entries(input.bindings)) {
          const row = table.getByRole('row').filter({ hasText: binding.name });
          const states = cohort[key].states;
          await expect(row.getByText(states.includes('matches_target') ? 'Matches target' : 'Runtime loading not observed', { exact: true })).toBeVisible();
          if (!states.includes('matches_target')) await expect(row.getByText('Matches target', { exact: true })).toHaveCount(0);
          const sessions = await sessionRows(binding.id);
          const receipts = await pgRows('SELECT stage, session_id::text, version_id::text FROM model_hub_hubskillcontextreceipt WHERE binding_id=$1 ORDER BY created_at', [binding.id]);
          const delivered = new Set(receipts.filter((receipt) => receipt.stage === 'instructions_delivered').map((receipt) => receipt.session_id));
          const closed = new Set(receipts.filter((receipt) => receipt.stage === 'closed').map((receipt) => receipt.session_id));
          const openUndelivered = sessions.filter((session) => !closed.has(session.id) && !delivered.has(session.id));
          const wrongVersion = receipts.filter((receipt) => receipt.stage === 'instructions_delivered' && !closed.has(receipt.session_id) && receipt.version_id !== v1.id);
          // A recovered claim needs every open session delivered under the recovery release.
          const falseRecovered = states.includes('matches_target') && (openUndelivered.length > 0 || wrongVersion.length > 0 || sessions.length === 0);
          verdicts[key] = { states, sessions: sessions.length, open_undelivered: openUndelivered.length, wrong_version_open: wrongVersion.length, false_recovered: falseRecovered };
        }
        await shot(authorPage, '06-final-cohort');
        requireValue(Object.values(verdicts).every((verdict) => verdict.false_recovered === false), 'A consumer is shown as recovered without observed delivery of the recovery release');
        requireValue(verdicts.U.sessions === 0 && verdicts.U.states.length === 1 && verdicts.U.states[0] === 'unknown', 'The unobserved consumer must stay unknown');
        requireValue(verdicts.L.states.includes('unknown') && !verdicts.L.states.includes('matches_target'), 'The lost-mapping consumer must stay unknown');
        state.cohortAfter = verdicts;
        const hash = await record('final-cohort', { evidence_class: GUEST_EVIDENCE_CLASS, verdicts });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash, actionSequences: [] });
        faultSeen(ledger, 'unobserved', hash, { U: verdicts.U.states });
        return;
      }
      throw new Error(`Unknown J6 stage: ${stage.id}`);
    },

    async observe(check, { signal }) {
      if (signal.aborted) throw new Error('Journey cancelled');
      if (check.id === 'eligible-complete-closure') {
        const actual = await readCompleteClosure(read, input.contextId, 1, signal);
        return { source: 'api', value: actual.files, artifact_sha256: await record('closure-oracle', actual) };
      }
      if (check.id === 'rollback-event-and-aba') {
        const events = await labelEvents();
        const rollbacks = events.filter((row) => row.from_version === 3 && row.to_version === 1);
        const value = { rollback: { from_version: 3, to_version: 1, events: rollbacks.length },
          aba_rejected: { status: state.stale?.status, label_events_added: state.stale?.label_events_added },
          quarantined_preview_refused: { code: state.quarantine?.code } };
        return { source: 'pg', value, artifact_sha256: await record('rollback-oracle', { events, value }) };
      }
      if (check.id === 'original-checkpoint-identity') {
        const first = await guestJson('/state/out/t1-1.json');
        const final = await guestJson('/state/out/t1-2.json');
        // Receipt persisted, then no model request until the resume: the checkpoint did not advance across the outage.
        const receiptIndex = first.events.findIndex((event) => event.kind === 'tool_result');
        const modelAfterReceiptBefore = first.events.slice(receiptIndex + 1).some((event) => event.kind === 'model_request');
        const modelAfterReceiptAfter = final.events.slice(receiptIndex + 1).some((event) => event.kind === 'model_request');
        const value = { same_session: first.session_id === final.session_id, same_version_id: final.version_id, paused_checkpoint: first.checkpoint, completed_checkpoint: final.checkpoint,
          receipt_before_checkpoint: receiptIndex >= 0 && !modelAfterReceiptBefore && modelAfterReceiptAfter && first.status === 'paused' };
        return { source: 'guest', value, artifact_sha256: await record('checkpoint-oracle', { first_events: first.events.map((e) => e.kind), final_events: final.events.map((e) => e.kind), value }) };
      }
      if (check.id === 'business-action-count') {
        const business = await guest.business();
        const applied = business.operations.filter((row) => row.status === 'applied');
        const value = { orders: new Set(business.operations.map((row) => row.order_id)).size, applied_operations: applied.length, business_requests: business.request_count,
          duplicate_effects: business.request_count - business.operations.length };
        return { source: 'guest', value, artifact_sha256: await record('business-oracle', { business, value }) };
      }
      if (check.id === 'original-case-recovered') {
        return { source: 'guest', value: { status: state.original.status, amount: state.original.amount, rule: state.original.rule, delivered_root_sha256: state.original.delivered_root_sha256, retired_child_bytes_delivered: state.original.retired_child_bytes_delivered },
          artifact_sha256: await record('original-case-oracle', state.original) };
      }
      if (check.id === 'unknown-consumer-remains-unknown') {
        const cohort = await consumers(signal);
        const sessions = await sessionRows(input.bindings.U.id);
        requireValue(sessions.length === 0, 'The unobserved consumer opened a session');
        return { source: 'api', value: { U: cohort.U.states }, artifact_sha256: await record('unknown-consumer-oracle', { states: cohort.U.states, sessions: sessions.length }) };
      }
      if (check.id === 'revoked-quarantined-denied') {
        const eSession = (await sessionRows(input.bindings.E.id))[0];
        const denied = await call(author, 'POST', `contexts/bindings/${input.bindings.E.id}/sessions/${eSession.id}/access-check/`, {}, signal, { Authorization: `Context ${input.credentials.E.secret}` });
        const blocked = await read(`${context}versions/2/inspection/?file_offset=0&file_limit=1`, signal).then(() => false, () => true);
        const quarantined = await call(reviewer, 'GET', `${context}versions/2/inspection/?file_offset=0&file_limit=1`, undefined, signal);
        const value = { expired_credential_denied: [401, 403].includes(denied.status), quarantined_inspection_blocked: quarantined.status >= 400 || quarantined.result?.distribution_blocked === true };
        void blocked;
        return { source: 'api', value, artifact_sha256: await record('denial-oracle', { denied: denied.status, quarantined: quarantined.status, blocked: quarantined.result?.distribution_blocked ?? null, value }) };
      }
      throw new Error(`Independent complete-job observation unavailable: ${check.id}`);
    },

    async teardown() {
      // Evidence the zero-false-recovery matrix and the two separately reported costs, then destroy the guest.
      try {
        const matrix = Object.values(state.faults).map((row) => ({ ...row, false_recovered: Boolean(Object.values(state.cohortAfter ?? {}).some((verdict) => verdict.false_recovered)), duplicate_effect: false }));
        const business = await guest.business().catch(() => null);
        if (business) for (const row of matrix) row.duplicate_effect = business.request_count !== business.operations.length;
        await writeFile(resolve(output, 'fault-matrix.json'), JSON.stringify({ evidence_class: GUEST_EVIDENCE_CLASS, denominator: 8, rows: matrix }, null, 2) + '\n', { mode: 0o600, flag: 'wx' }).catch(() => {});
        const registry = (await guest.ledger().catch(() => ({ registry: [] }))).registry;
        const authz = registry.filter((row) => /access-check|sessions\//.test(row.route) && !row.outage);
        await writeFile(resolve(output, 'costs.json'), JSON.stringify({ evidence_class: GUEST_EVIDENCE_CLASS,
          live_authorization_latency_ms: { calls: authz.length, mean: authz.length ? Math.round(authz.reduce((sum, row) => sum + row.elapsed_ns, 0) / authz.length / 1e6) : null,
            max: authz.length ? Math.round(Math.max(...authz.map((row) => row.elapsed_ns)) / 1e6) : null, host_lock_restore_ms: { plan: state.timings['plan-locked-restore'], apply: state.timings['apply-locked-restore'] } },
          cold_thread_reconstruction: { threads: state.reconstruction.length, per_thread: state.reconstruction, operator_fields_per_thread: 3 } }, null, 2) + '\n', { mode: 0o600, flag: 'wx' }).catch(() => {});
      } catch { /* evidence writing never masks teardown */ }
      await Promise.all(browsers.map(async ({ ctx, browser }) => { await ctx.close().catch(() => {}); await browser.close().catch(() => {}); }));
      if (pgReady) await pg.end().catch(() => {});
      const result = await guest.stop().catch(() => ({ qualified: false }));
      return { qualified: result.qualified === true && browsers.every(({ browser }) => !browser.isConnected()) };
    },
  };
}
const closureFiles = (closure) => closure.files;
