import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { promisify } from 'node:util';
import { chromium, expect as baseExpect } from '@playwright/test';
import pg from 'pg';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { canonical, digest, requireValue, sha256 } from '../../../lib/skills-r3/canonical.mjs';
import { attachGuest, GUEST_EVIDENCE_CLASS } from '../../../lib/skills-r3/j6-guest.mjs';
import { GOOD_ROOT, REFUND_REQUEST, VERIFY_REQUEST, entrypoint, guestEnvironment, moveProduction, requestFor } from '../../../lib/skills-r3/j6-scenario.mjs';

// The full stack can be slow under load; a person would wait, so assertions poll for 30 seconds.
const expect = baseExpect.configure({ timeout: 30_000 });
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
const sequenceOf = (ledger, target) => ledger.events().findLast((event) => event.type === 'start' && event.target === target).sequence;
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
    const response = await call(author, 'GET', path, undefined, signal);
    requireValue(response.status >= 200 && response.status < 300, 'Independent public recovery observation unavailable');
    return response.result;
  };
  const [v1, v2, v3, v4] = input.versions;
  const members = await fetch(`${api}/accounts/organization/members/?page=1&limit=100`, { headers: header(author), redirect: 'error' }).then((r) => r.json());
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
    const ctx = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
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
  // Evidence images never show a one-time credential: readonly fields are masked in the capture only.
  const shot = async (page, name) => {
    await page.addStyleTag({ content: 'input[readonly]{-webkit-text-security:disc !important;}' }).catch(() => {});
    return page.screenshot({ path: resolve(output, 'shots', `${name}.png`) });
  };
  const opsFor = (stage, ledger, signal, actor) => new MeasuredOperator({ ledger, actor, step: stage.id, phase: stage.phase, signal });
  const guestEnv = (consume, report, binding) => guest.envFile(`${binding}-${randomUUID().slice(0, 8)}`, guestEnvironment({ consume, report, bindingId: input.bindings[binding].id }));
  // A measured operator command whose non-zero exit is data (a paused thread), not a crash.
  const command = (stage, ledger, actor, target, argv, accepted = [0], options = {}) => ledger.measure({ dimension: 'A', primitive: 'command', actor, step: stage.id, phase: stage.phase, target }, async () => {
    const started = performance.now();
    let result;
    try { const { stdout, stderr } = await run(argv[0], argv.slice(1), { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024, ...options }); result = { code: 0, stdout, stderr }; }
    catch (error) { result = { code: error.code, stdout: error.stdout ?? '', stderr: error.stderr ?? '' }; }
    state.timings[target] = Math.round(performance.now() - started);
    // Retain the redacted, bounded text of a host command: its review or typed error, never a credential.
    await writeFile(resolve(output, `command-${target}.txt`), `exit ${result.code}\n${String(result.stdout).slice(0, 4000)}\n${String(result.stderr).slice(-1500)}`.replace(/[A-Za-z0-9_-]{40,}/g, '<redacted>'), { mode: 0o600 }).catch(() => {});
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
  const labelEvents = () => pgRows('SELECT id::text, from_version, to_version, release_revision FROM model_hub_hubskilllabelevent WHERE skill_id=$1 ORDER BY release_revision, created_at', [input.contextId]);
  const db = new pg.Client({ connectionString: process.env.E2E_PG_URL ?? 'postgresql://futureagi:futureagi@localhost:25432/futureagi' });
  let pgReady = false;
  async function pgRows(text, values = []) {
    if (!pgReady) { await db.connect(); pgReady = true; }
    return (await db.query(text, values)).rows;
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
  // Independent re-derivation from the sidecar business/model ledgers and the public closure (never memoized).
  const policySha = (closure, skillId) => {
    const descriptor = JSON.parse(closure.files.find((file) => file.path === '.futureagi/context.json').content);
    const link = descriptor.links.find((item) => item.skill_id === skillId);
    const policy = closure.files.find((file) => file.path === `${link.mount}references/policy.md`);
    return sha256(Buffer.from(policy.content, 'utf8'));
  };
  const originalCase = async (signal) => {
    const business = await guest.business();
    const o0 = business.operations.filter((row) => row.order_id === input.orders.o0);
    const model = (await guest.ledger()).model;
    const decided = model.filter((row) => row.decision.rule === 'refund_within_window').at(-1);
    const expected = policySha(await readCompleteClosure(read, input.contextId, 1, signal), input.leaves[0].skillId);
    const child = decided?.delivered_files.find((file) => file.skill_id === input.leaves[0].skillId && file.path === 'references/policy.md');
    return { status: o0.length === 1 ? o0[0].status : `operations:${o0.length}`, amount: o0[0]?.amount, rule: decided?.decision.rule, delivered_root_sha256: decided?.system_sha256,
      retired_child_bytes_delivered: Boolean(child) && child.sha256 === expected, model_requests: model.length };
  };
  const faultSeen = (ledger, key, artifactHash, observed) => {
    ledger.fault(FAULTS[key], `${FAULTS[key]}-1`);
    state.faults[key] = { fault: FAULTS[key], scenario: `${FAULTS[key]}-1`, evidence_sha256: artifactHash, observed };
  };
  const decisionFor = (packet, preconditions) => ({ packet_sha256: digest(packet), preconditions_sha256: digest(preconditions) });

  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, context) {
      try { await perform(stage, context); } catch (error) {
        // Retain a redacted diagnostic next to the evidence: never a token, cookie or body.
        for (const [name, page] of Object.entries(pages)) await shot(page, `failure-${stage.id}-${name}`).catch(() => {});
        const options = await pages.author?.getByRole('option').allTextContents().catch(() => []);
        const message = `${String(error?.message ?? error)}\nvisible options: ${JSON.stringify(options)}`.replace(/[A-Za-z0-9_-]{32,}/g, '<redacted>').slice(0, 1600);
        await writeFile(resolve(output, `stage-failure-${stage.id}.txt`), `${message}\n`, { flag: 'a', mode: 0o600 }).catch(() => {});
        throw error;
      }
    },
    async observe(check, { signal }) {
      return observe(check, { signal });
    },
    async teardown() { return teardown(); },
  };

  async function perform(stage, { ledger, signal }) {
      if (signal.aborted) throw new Error('Journey cancelled');
      const operator = opsFor(stage, ledger, signal, 'incident-operator');
      const admin = opsFor(stage, ledger, signal, 'release-reviewer');
      const authorPage = await open('author', author);
      const reviewerPage = await open('reviewer', reviewer);
      const contextUrl = `/dashboard/skills/contexts/${input.contextId}`;

      if (stage.id === 'detect-incident') {
        // The incident link names the declared context, the failing release and the recovery view: one navigation, one screen.
        await operator.transition('recovery-cohort', () => ledger.measure({ dimension: 'A', primitive: 'click', actor: 'incident-operator', step: stage.id, phase: stage.phase, target: 'open-declared-context' },
          () => authorPage.goto(`${contextUrl}?version=3&tab=recovery`)));
        const cohort = await consumers(signal);
        requireValue(cohort.A.latest_failure?.diagnostic_codes.includes('refund_window_regression'), 'The retained incident receipt is not visible');
        requireValue(cohort.U.states.length === 1 && cohort.U.states[0] === 'unknown', 'The never-observed consumer must start unknown');
        const table = authorPage.getByRole('table', { name: 'Recovery cohort' });
        await expect(table).toBeVisible();
        await expect(table.getByText('Refund window regression', { exact: false })).toBeVisible();
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
        const retired = await call(author, 'GET', `${input.leaves[0].skillId}/`, undefined, signal);
        requireValue(retired.status === 404, 'The refund child source must be retired');
        await expect(authorPage.getByText('Source retired:', { exact: false }).first()).toBeVisible();
        // The view preselects the newest eligible release older than the failing one and says why; choosing another is still one click.
        await expect(authorPage.getByRole('heading', {name:`Roll back ${input.contextName} to v1`, exact:true})).toBeVisible();
        await expect(authorPage.getByText('v1 is the newest eligible release before v3.', {exact:false})).toBeVisible();
        await expect(authorPage.getByRole('button', { name: 'Choose v1', exact: true })).toBeEnabled();
        const closure = await readCompleteClosure(read, input.contextId, 1, signal);
        const production = await readCompleteClosure(read, input.contextId, 3, signal);
        const before = new Map(production.files.map((file) => [file.path, sha256(Buffer.from(file.content, 'utf8'))]));
        const changed = closure.files.filter((file) => before.get(file.path) !== sha256(Buffer.from(file.content, 'utf8'))).map((file) => file.path);
        requireValue(changed.includes('AGENTS.md'), 'The recovery release must differ from the regressed one');
        const files = authorPage.getByRole('table', { name: 'Recovery closure files' });
        await expect(files).toBeVisible();
        const items = closure.files.map((_, index) => `closure-file-${index}`);
        const exceptions = closure.files.flatMap((file, index) => (changed.includes(file.path) ? [`closure-file-${index}`] : []));
        // The closure table shows its first rows and keeps the rest behind one disclosure; open it so every file is inspected.
        const moreFiles = authorPage.getByRole('button', { name: /^Show \d+ unchanged files?$/ });
        if (await moreFiles.count()) await operator.click(moreFiles, 'show-all-closure-files');
        const retiredHash = await record('retired-child-closure', { retired_status: retired.status, closure_paths: closure.files.map((file) => file.path), changed });
        const decision = decisionFor({ candidate: closure.files.map((file) => [file.path, sha256(Buffer.from(file.content, 'utf8'))]), production: [...before] },
          { candidate_version_id: v1.id, production_version_id: v3.id, release_revision: (await read(`${context}release-state/`, signal)).release_revision });
        await operator.review('complete-recovery-closure', items, exceptions, async () => {
          for (const file of closure.files) {
            const cell = files.getByRole('cell', { name: file.path, exact: true });
            await cell.scrollIntoViewIfNeeded();
            await expect(cell).toBeInViewport();
            const row = files.getByRole('row').filter({ has: authorPage.getByRole('cell', { name: file.path, exact: true }) });
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
        await operator.click(authorPage.getByRole('checkbox', { name: /^I've checked the (?:changes|\d+ changed files?)(?: and the linked skill versions)?$/ }), 'acknowledge-complete-review');
        await shot(authorPage, '02-closure-review');
        await record('eligible-closure', closure);
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: retiredHash, actionSequences: [], decision });
        faultSeen(ledger, 'child', retiredHash, { retired_status: retired.status });
        return;
      }

      if (stage.id === 'cas-rollback') {
        const submit = async (cycle) => {
          // Acknowledging the review prepares the packet; the reviewer chosen once stays selected for a restarted review.
          const reviewerBox = authorPage.getByRole('combobox', { name: 'Reviewer' });
          await expect(authorPage.getByRole('button', {name:'Request rollback to v1', exact:true})).toBeEnabled();
          // The person who last moved production is the suggested reviewer; no list to open for a restarted or a first review alike.
          await expect(reviewerBox).toContainText(reviewerName);
          await operator.click(authorPage.getByRole('button', { name: 'Request rollback to v1', exact: true }), `submit-rollback-${cycle}`);
          await expect(authorPage.getByText(`Rollback to v1 requested from ${reviewerName}. Production has not moved.`, {exact:true})).toBeVisible();
        };
        const latestRequest = async () => {
          const inboxResponse = await call(reviewer, 'GET', 'review-inbox/?page=1&page_size=50', undefined, signal);
          requireValue(inboxResponse.status === 200, 'The reviewer inbox is unavailable');
          const inbox = inboxResponse.result;
          return inbox.results.filter((row) => row.subject_id === input.contextId && row.candidate_version_id === v1.id && row.status === 'pending')
            .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0];
        };
        await submit(1);
        const stale = await latestRequest();
        requireValue(stale, 'The submitted rollback request is not in the reviewer inbox');
        const eventsBefore = await labelEvents();
        // The independent reviewer approves the packet before production moves (an approval is a person's act, injected here
        // as a fault precondition exactly as the hops are): only the current-state check can now stop the stale approval.
        const staleApproval = await call(reviewer, 'POST', `release-requests/${stale.id}/decisions/`, { revision: stale.revision, expected_fingerprint: stale.fingerprint, decision: 'approve', comment: 'Synthetic J6 reviewer approval before the ABA hop' }, signal);
        requireValue(staleApproval.status >= 200 && staleApproval.status < 300, 'The reviewer approval of the soon-stale packet failed');
        // ABA: another releaser moves production away and back (other candidates) before execution.
        for (const target of [v4, v3]) await moveProduction({ api: apiOk(signal), author, reviewer, contextId: input.contextId, version: target });
        const afterAba = await labelEvents();
        requireValue(afterAba.length === eventsBefore.length + 2
          && JSON.stringify(afterAba.slice(eventsBefore.length).map((row) => [row.from_version, row.to_version])) === JSON.stringify([[3, 4], [4, 3]]), 'The ABA hop must record exactly v3 to v4 and back');
        // An independent execute of the approved, stale packet is refused by the current-state check and writes no label event.
        const rejected = await call(reviewer, 'POST', `release-requests/${stale.id}/execute/`, { revision: stale.revision, expected_fingerprint: stale.fingerprint }, signal);
        const afterReject = await labelEvents();
        requireValue(rejected.status === 409 && afterReject.length === afterAba.length, 'The stale ABA packet must be rejected without a label event');
        requireValue(/review_stale|release_conflict/.test(JSON.stringify(rejected.body)), 'The approved stale packet must be refused by the current-state check, not by its status');
        state.stalePacket = { id: stale.id, revision: stale.revision, fingerprint: stale.fingerprint };
        state.stale = { status: rejected.status, label_events_added: afterReject.length - afterAba.length, hop_events: afterAba.slice(eventsBefore.length).map((row) => [row.from_version, row.to_version]), approved_before_hop: true, request_id: stale.id };
        // Operator starts a fresh review against the moved release revision.
        // The recovery view follows the moved release on its own; the review restarts against the new revision.
        // The view shows the release revision the review was made against; wait for it to match the moved release
        // (a change after the review restarts it), so no later poll can restart the new review.
        const movedRevision = (await read(`${context}release-state/`, signal)).release_revision;
        await expect(authorPage.locator(`[data-release-revision="${movedRevision}"]`, { hasText: 'Reviewed against the release that made' })).toBeVisible({ timeout: 60_000 });
        const reack = authorPage.getByRole('checkbox', { name: /^I've checked the (?:changes|\d+ changed files?)(?: and the linked skill versions)?$/ });
        await expect(reack).toBeEnabled({ timeout: 30_000 });
        await expect(reack).not.toBeChecked();
        await operator.click(reack, 'acknowledge-complete-review-again');
        await submit(2);
        const fresh = await latestRequest();
        requireValue(fresh && fresh.id !== stale.id, 'The fresh rollback request is not in the reviewer inbox');
        // The fresh packet closed the stale approved request, so no reviewer opens and declines it.
        const superseded = await read(`release-requests/${stale.id}/`, signal);
        requireValue(superseded.status === 'superseded', 'The stale request must be superseded by the fresh packet');
        const afterSupersede = await labelEvents();
        requireValue(afterSupersede.length === afterAba.length, 'Superseding a stale packet must not move a label');
        state.stale.request_status = superseded.status;
        const abaHash = await record('aba-rejection', state.stale);
        faultSeen(ledger, 'aba', abaHash, state.stale);
        const detail = await read(`release-requests/${fresh.id}/`, signal);
        const decision = decisionFor({ packet: detail.fingerprint }, { candidate: v1.id, from_version: 3, release_revision: (await read(`${context}release-state/`, signal)).release_revision });
        await operator.handoff('release-reviewer', async () => {});
        // The author copies the exact review link from the submitted request; the reviewer opens it.
        await operator.click(authorPage.getByRole('button', { name: 'Copy request link', exact: true }), 'copy-review-link');
        await expect(authorPage.getByText('Copied the request link.', { exact: true })).toBeVisible();
        const reviewLink = await authorPage.evaluate(() => navigator.clipboard.readText());
        requireValue(reviewLink === await authorPage.getByRole('link', { name: 'Open request' }).getAttribute('href') && reviewLink.includes(`request=${fresh.id}`), 'The copied link must open the fresh request');
        await authorPage.getByRole('link', { name: 'Open request' }).scrollIntoViewIfNeeded();
        // No credential is on this screen (the replacement is issued later), so the link is captured unmasked.
        await authorPage.screenshot({ path: resolve(output, 'shots', '03-review-link.png') });
        await navigate(admin, ledger, stage, reviewerPage, reviewLink, 'open-review-link');
        await expect(reviewerPage).toHaveURL(new RegExp(`request=${fresh.id}`));
        const ack = reviewerPage.getByRole('checkbox', { name: /^I've checked the (?:changes|\d+ changed files?), the evidence and what this release will affect\.$/, exact: true });
        await expect(ack).toBeEnabled();
        await admin.click(ack, 'acknowledge-fresh-packet');
        await admin.approve('approve-fresh-packet', ['packet'], () => reviewerPage.getByRole('button', { name: 'Approve release', exact: true }).click(), decision);
        // The acknowledgement is keyed to the exact packet revision and fingerprint and carries across
        // pending -> approved; only a cleared acknowledgement (a new revision) needs another click.
        if (!(await ack.isChecked())) await admin.click(ack, 'acknowledge-before-release');
        await expect(ack).toBeChecked();
        await admin.click(reviewerPage.getByRole('button', { name: 'Release to production', exact: true }), 'release-approved-packet');
        await admin.click(reviewerPage.getByRole('button', { name: 'Move production', exact: true }), 'move-production');
        await expect(reviewerPage.getByText('Release receipt', { exact: true })).toBeVisible();
        await expect(reviewerPage.getByText('Move production to v1?', { exact: false })).toBeHidden();
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
        // The operator is an organization administrator: issue the replacement credential here and hand
        // its one-time secret to the application's secret store (manual information transfer).
        await expect(authorPage.getByText('Production moved from v3 to v1', { exact: false })).toBeVisible({ timeout: 30_000 });
        await operator.click(authorPage.getByRole('button', { name: 'Issue replacement credential', exact: true }), 'issue-replacement-credential');
        const secretBox = authorPage.getByRole('textbox', { name: 'New credential (shown once)' });
        await expect(secretBox).toBeVisible();
        state.replacement = await secretBox.inputValue();
        requireValue(typeof state.replacement === 'string' && state.replacement.length >= 32, 'The replacement credential was not shown');
        await operator.handoff('application-secret-store', async () => {});
        // The secret is configured in two places: the application's environment and the CLI's.
        await operator.configurationEdit('configure-application-credential', async () => { state.appCredential = state.replacement; });
        await operator.configurationEdit('configure-cli-credential', async () => { state.cliCredential = state.replacement; });
        await shot(authorPage, '05-replacement-credential-issued');
        // Exact checked-in lock: restore the last reviewed commit's lock, preserve local work.
        const before = {
          local: sha256(await readFile(resolve(input.project, 'docs', 'local-notes.md'), 'utf8')),
          lock: sha256(await readFile(resolve(input.project, '.futureagi-context.lock.json'), 'utf8')),
        };
        // One command reads the lock of the last reviewed commit, plans, applies and verifies it.
        const reviewedLock = (await run('git', ['-C', input.project, 'show', `${input.goodCommit}:.futureagi-context.lock.json`], { encoding: 'utf8' })).stdout;
        const restored = await hostCli(stage, ledger, 'restore-locked', ['restore', '--allow-preview', '--lock-from-git', input.goodCommit, '--project', '.', '--apply', '--quiescent'], state.cliCredential);
        const restoreResult = JSON.parse(restored.stdout);
        requireValue(restoreResult.state === 'restored' && restoreResult.verification?.verified === true, 'The restore did not verify');
        const lock = JSON.parse(await readFile(resolve(input.project, '.futureagi-context.lock.json'), 'utf8'));
        const files = closureFiles(await readCompleteClosure(read, input.contextId, 1, signal));
        const notes = await readFile(resolve(input.project, 'docs', 'local-notes.md'), 'utf8');
        const agents = await readFile(resolve(input.project, 'AGENTS.md'), 'utf8');
        requireValue(sha256(notes) === before.local, 'Local work was changed by the restore');
        requireValue(lock.version_id === v1.id && lock.version === 1 && lock.digest === v1.digest, 'The lock does not retain the recovery release');
        requireValue(agents.includes(GOOD_ROOT(1)) && !agents.includes('REFUND-WINDOW-DAYS'), 'The restored root differs from the recovery release');
        // Every file the lock owns exists on disk with exactly the locked bytes.
        let copied = 0;
        for (const owned of lock.materialization.files.filter((row) => row.transformation === 'copy')) {
          const source = files.find((file) => file.path === owned.source_path);
          requireValue(source && sha256(Buffer.from(source.content, 'utf8')) === owned.source_sha256, `Locked source differs from the recovery release: ${owned.source_path}`);
          requireValue(sha256(await readFile(resolve(input.project, owned.destination))) === owned.sha256 && owned.sha256 === owned.source_sha256, `Managed file differs from its lock: ${owned.destination}`);
          copied += 1;
        }
        requireValue(copied > 0, 'The restore owns no copied files');
        const retainedManifest = JSON.parse(reviewedLock);
        requireValue(retainedManifest.version_id === lock.version_id && retainedManifest.digest === lock.digest, 'The restored lock differs from the retained one');
        const hash = await record('locked-restore', { lock_version: lock.version, lock_version_id: lock.version_id, lock_sha256_before: before.lock, local_notes_preserved: true, closure_files: files.length, managed_copies: copied,
          timings_ms: { restore: state.timings['restore-locked'] } });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'filesystem', artifactHash: hash, actionSequences: [] });
        return;
      }

      if (stage.id === 'interrupt-after-tool') {
        const order = input.orders.o1;
        const businessStart = await guest.business();
        await guest.faults({ available: true, arm_after_business: true });
        const reportA = input.credentials.report.A.secret;
        const envFile = await guestEnv(state.appCredential, reportA, 'A');
        await guest.put('/state/requests/t1.json', JSON.stringify(requestFor(input.threads.t1, order)) + '\n');
        const paused = await guestCommand(stage, ledger, 'incident-operator', 'run-application-thread', envFile, entrypoint('/state/requests/t1.json', '/state/t1.sqlite', '/state/out/t1-1.json'), [2]);
        const out = await guestJson('/state/out/t1-1.json');
        requireValue(out.status === 'paused', 'The thread must pause when the registry is lost after the tool result');
        const business = await guest.business();
        const op = business.operations.filter((row) => row.order_id === order);
        requireValue(op.length === 1 && op[0].status === 'applied' && business.request_count === businessStart.request_count + 1, 'The refund must be applied exactly once before the outage');
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
        const recoverEnv = await guest.envFile(`recover-${randomUUID().slice(0, 8)}`, { ...guestEnvironment({ consume: state.appCredential, report: reports.A.secret, bindingId: input.bindings.A.id }),
          APP_CONSUME: state.appCredential, A_REPORT: reports.A.secret, E_REPORT: reports.E.secret, L_REPORT: reports.L.secret, E_CONSUME: input.credentials.E.secret, L_CONSUME: input.credentials.L.secret });
        state.recoverEnv = recoverEnv;
        // The expired credential is denied by the public access check before anything is planned (a wait, not operator work).
        const eSession = (await sessionRows(input.bindings.E.id))[0];
        await expect.poll(async () => (await call({ ...author }, 'POST', `contexts/bindings/${input.bindings.E.id}/sessions/${eSession.id}/access-check/`, {}, signal, { Authorization: `Context ${input.credentials.E.secret}` })).status, { timeout: 330_000, intervals: [2000] }).toBeGreaterThanOrEqual(401);
        const eDenied = await call(author, 'POST', `contexts/bindings/${input.bindings.E.id}/sessions/${eSession.id}/access-check/`, {}, signal, { Authorization: `Context ${input.credentials.E.secret}` });
        requireValue([401, 403].includes(eDenied.status), 'The expired credential must be denied by the public access check');
        // ONE command inspects every registered thread (no session opened, no model call, no tool repeated) and writes what each needs.
        // The operator types the reconstruction request once, the order of the thread whose mapping is lost, and the original case to recheck.
        const planned = await guestCommand(stage, ledger, 'incident-operator', 'plan-thread-recovery', recoverEnv, ['python', '-m', 'ctxhub_support.recover', 'plan', '--registry', '/state/requests/recovery-registry.json',
          '--request', VERIFY_REQUEST, '--order', `t3=${input.orders.o3}`, '--recheck', `t0=${input.orders.o0}`, '--recheck-request', REFUND_REQUEST, '--output', '/state/out/recovery-plan.json']);
        // What the operator reviews is the command's own printed plan (each thread, what it needs and why), not a file the driver reads on their behalf.
        const shown = JSON.parse(planned.stdout);
        const plan = await guestJson('/state/out/recovery-plan.json');
        requireValue(shown.plan_sha256 === plan.plan_sha256 && shown.executed === false, 'The printed plan must be the written plan and executed nothing');
        const entry = Object.fromEntries(shown.threads.map((item) => [item.key, item]));
        const t1Plan = entry.t1;
        const businessBefore = await guest.business();
        requireValue([input.orders.o2, input.orders.o3].every((order) => businessBefore.operations.filter((item) => item.order_id === order && item.status === 'applied').length === 1), 'The executed refunds must be visible before reconstruction');
        // The operator's review of the plan: what each thread needs and why. A plan that classifies any thread differently is not applied.
        const reviewPlan = async () => {
          // The refund call is still pending in the transcript, but its business receipt is already durable.
          requireValue(t1Plan.action === 'resume' && t1Plan.reason === 'original_identity_available' && t1Plan.pending_tool_calls.length === 1
            && t1Plan.pending_tool_calls.every((id) => t1Plan.durable_receipt_calls.includes(id)), 'The paused thread must be resumable under its original identity with its refund receipt durable');
          requireValue(entry.t2.action === 'close_and_reconstruct' && entry.t2.reason === 'authority_unavailable' && entry.t2.durable_receipt_calls.length >= 1
            && entry.t2.order_id === input.orders.o2, 'The expired-credential thread must not be resumable');
          requireValue(entry.t3.action === 'reconstruct' && entry.t3.known === false && entry.t3.reason === 'thread_state_missing' && entry.t3.order_id === input.orders.o3, 'The lost-mapping thread must not be resumable');
          requireValue(entry.t0.action === 'close_and_recheck' && entry.t0.phase === 'recheck' && entry.t0.order_id === input.orders.o0, 'The superseded incident thread must be rechecked as a clean thread');
        };
        // The decision covers the whole plan: resume T1 under its identity; reconstruct E and L as explicit clean threads; close and recheck the incident thread later.
        // Every registered thread is exposed; the three that are not a plain resume are the exceptions the operator must see.
        const decision = decisionFor({ plan: plan.plan_sha256, resume: [t.t1], reconstruct: [t.t2, t.t3], recheck: [t.t0], requests: [VERIFY_REQUEST, REFUND_REQUEST], copied_messages: 0 },
          { t1: [t1Plan.checkpoint, t1Plan.session_id], t2: entry.t2.reason, t3: entry.t3.reason, t0: entry.t0.reason, replay_refunds: false });
        await ledger.measure({ dimension: 'A', primitive: 'recovery_decision', actor: 'incident-operator', step: stage.id, phase: stage.phase, target: 'resume-one-reconstruct-two',
          items: shown.threads.map((item) => item.key), exceptionItems: shown.threads.filter((item) => item.action !== 'resume').map((item) => item.key), decision }, reviewPlan);
        const expiredHash = await record('expired-credential', { inspect: { reason: entry.t2.reason, receipts: entry.t2.durable_receipt_calls.length, checkpoint: entry.t2.checkpoint }, access_check_status: eDenied.status });
        faultSeen(ledger, 'expired', expiredHash, { reason: entry.t2.reason, status: eDenied.status });
        const lostHash = await record('lost-mapping', { inspect: { known: entry.t3.known, reason: entry.t3.reason }, business_receipts_for_order: 1 });
        faultSeen(ledger, 'lost', lostHash, { reason: entry.t3.reason });
        // A digest other than the reviewed one applies nothing (a control injected like the ABA hops, not operator work).
        const refused = await guest.execute(recoverEnv, ['python', '-m', 'ctxhub_support.recover', 'apply', '--registry', '/state/requests/recovery-registry.json',
          '--plan', '/state/out/recovery-plan.json', '--reviewed-plan-sha256', `sha256:${'0'.repeat(64)}`, '--phase', 'threads', '--output', '/state/out/recovery-refused.json']);
        const afterRefused = await guest.business();
        requireValue(refused.code !== 0 && /plan_not_reviewed/.test(refused.stderr) && afterRefused.request_count === businessBefore.request_count && !(await guest.exists('/state/out/recovery-refused.json')), 'An unreviewed plan digest must apply nothing');
        await record('apply-refusal', { refused_exit_nonzero: refused.code !== 0, error: 'plan_not_reviewed', business_requests_before: businessBefore.request_count, business_requests_after: afterRefused.request_count });
        // Apply exactly the reviewed plan: the command refuses any other digest or a thread that moved since the plan.
        await guestCommand(stage, ledger, 'incident-operator', 'apply-thread-recovery', recoverEnv, ['python', '-m', 'ctxhub_support.recover', 'apply', '--registry', '/state/requests/recovery-registry.json',
          '--plan', '/state/out/recovery-plan.json', '--reviewed-plan-sha256', plan.plan_sha256, '--phase', 'threads', '--output', '/state/out/recovery-threads.json'], [0]);
        const applied = await guestJson('/state/out/recovery-threads.json');
        const done = Object.fromEntries(applied.threads.map((item) => [item.key, item]));
        requireValue(applied.plan_sha256 === plan.plan_sha256 && done.t1.status === 'completed' && done.t1.session_id === state.threads.t1.session_id && done.t1.version_id === v1.id, 'The resumed thread must keep its session and release identity');
        state.threads.t1.completed_checkpoint = done.t1.checkpoint;
        requireValue(done.t2.closed?.closed === true && done.t2.closed.reason === 'authority_unavailable', 'The expired thread must be closed so it is not counted as recovered');
        const clean = { e: { entry: done.t2, abandoned: t.t2 }, l: { entry: done.t3, abandoned: t.t3 } };
        for (const value of Object.values(clean)) requireValue(value.entry.status === 'completed' && value.entry.version_id === v1.id && value.entry.clean_thread !== value.abandoned, 'The clean thread must run the recovery release as a new thread');
        const businessAfter = await guest.business();
        requireValue(businessAfter.request_count === 3 && businessAfter.operations.length === 3 && applied.reconciliation.request_count === 3, 'Clean threads must not replay a business action');
        state.recoverApplied = applied;
        state.reconstruction = Object.entries(clean).map(([key, value]) => ({ thread: key, elapsed_ms: value.entry.elapsed_ms, copied_messages: 0, additional_business_requests: businessAfter.request_count - businessBefore.request_count }));
        state.reconstructionSharedMs = state.timings['apply-thread-recovery'];
        state.threads.clean = Object.fromEntries(Object.entries(clean).map(([key, value]) => [key, { session_id: value.entry.session_id, version_id: value.entry.version_id, thread: value.entry.clean_thread }]));
        const hash = await record('resume-and-clean-threads', { t1: state.threads.t1, clean: state.threads.clean, reconstruction: state.reconstruction, plan_sha256: plan.plan_sha256 });
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'guest', artifactHash: hash, actionSequences: [], decision });
        return;
      }

      if (stage.id === 'reconcile-business-action') {
        // The apply output already pairs every order's durable receipts with the business backend's own ledger; the operator reconciles that
        // table (one reconciliation) and the driver re-derives it from the sidecar's independent ledger.
        const ledgerRead = await guest.business();
        const rows = await operator.reconcile('durable-receipts-against-business-ledger', async () => {
          const orders = [input.orders.o1, input.orders.o2, input.orders.o3];
          const shown = new Map(state.recoverApplied.reconciliation.orders.map((item) => [item.order_id, item.operations]));
          return orders.map((order) => ({ order, operations: ledgerRead.operations.filter((item) => item.order_id === order).length, shown: shown.get(order) ?? null }));
        });
        requireValue(rows.every((item) => item.operations === 1 && item.shown === 1) && ledgerRead.request_count === 3 && !ledgerRead.operations.some((item) => item.order_id === input.orders.o0), 'The business ledger must show one operation per executed order and none for the declined case');
        const hash = await record('business-reconciliation', { orders: rows, request_count: ledgerRead.request_count });
        // The reconciliation table is the apply command's output: the same completed command supports this stage without being counted twice.
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'guest', artifactHash: hash, actionSequences: [sequenceOf(ledger, 'apply-thread-recovery')] });
        return;
      }

      if (stage.id === 'verify-original-case') {
        // The reviewed plan also covers the original case: close the superseded incident thread and rerun it once as a clean thread under the recovery release.
        const plan = await guestJson('/state/out/recovery-plan.json');
        await guestCommand(stage, ledger, 'incident-operator', 'apply-original-case-recheck', state.recoverEnv, ['python', '-m', 'ctxhub_support.recover', 'apply', '--registry', '/state/requests/recovery-registry.json',
          '--plan', '/state/out/recovery-plan.json', '--reviewed-plan-sha256', plan.plan_sha256, '--phase', 'recheck', '--output', '/state/out/recovery-recheck.json'], [0]);
        const rechecked = await guestJson('/state/out/recovery-recheck.json');
        const out = rechecked.threads.find((item) => item.key === 't0');
        const business = await guest.business();
        const o0 = business.operations.filter((row) => row.order_id === input.orders.o0);
        requireValue(rechecked.plan_sha256 === plan.plan_sha256 && out?.closed?.reason === 'incident_superseded', 'The abandoned incident thread must be closed, never counted as recovered');
        requireValue(out.status === 'completed' && out.version_id === v1.id && o0.length === 1 && o0[0].status === 'applied' && o0[0].amount === 25, 'The original case must be refunded exactly once under the recovery release');
        state.original = await originalCase(signal);
        requireValue(state.original.retired_child_bytes_delivered === true, 'The retired child policy bytes were not delivered at the model boundary');
        const hash = await record('original-case', state.original);
        ledger.stage({ step: stage.id, phase: stage.phase, source: 'guest', artifactHash: hash, actionSequences: [] });
        return;
      }

      if (stage.id === 'inspect-declared-cohort') {
        // The recovery view has been idle for minutes and the browser throttles an idle tab's timers; the operator refreshes the facts once.
        await operator.click(authorPage.getByRole('button', { name: 'Check again', exact: true }), 'refresh-final-cohort');
        const cohort = await consumers(signal);
        const table = authorPage.getByRole('table', { name: 'Recovery cohort' });
        await expect(table).toBeVisible();
        const verdicts = {};
        for (const [key, binding] of Object.entries(input.bindings)) {
          const row = table.getByRole('row').filter({ hasText: binding.name });
          const states = cohort[key].states;
          await expect(row.getByText(states.includes('matches_target') ? 'Matches target' : 'No report from this app yet', { exact: true })).toBeVisible();
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
        await table.scrollIntoViewIfNeeded();
        await authorPage.evaluate(() => window.scrollBy(0, -80));
        await shot(authorPage, '06-final-cohort');
        // Dark-mode evidence of the same view (capture only; no operator action, no product state change).
        await authorPage.evaluate(() => {
          const settings = JSON.parse(localStorage.getItem('settings') || '{}');
          localStorage.setItem('settings', JSON.stringify({ ...settings, themeMode: 'dark' }));
        });
        await authorPage.reload();
        await authorPage.getByRole('tab', { name: 'Rollback', exact: true }).click();
        const darkTable = authorPage.getByRole('table', { name: 'Recovery cohort' });
        await expect(darkTable).toBeVisible();
        await darkTable.scrollIntoViewIfNeeded();
        await authorPage.evaluate(() => window.scrollBy(0, -80));
        await shot(authorPage, '07-final-cohort-dark');
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
  }

  async function observe(check, { signal }) {
      if (signal.aborted) throw new Error('Journey cancelled');
      if (check.id === 'eligible-complete-closure') {
        const actual = await readCompleteClosure(read, input.contextId, 1, signal);
        return { source: 'api', value: actual.files, artifact_sha256: await record('closure-oracle', actual) };
      }
      if (check.id === 'rollback-event-and-aba') {
        const events = await labelEvents();
        const rollbacks = events.filter((row) => row.from_version === 3 && row.to_version === 1);
        // Re-probe now: the stale packet is still refused and the quarantined alternative still cannot be previewed.
        const staleAgain = await call(reviewer, 'POST', `release-requests/${state.stalePacket.id}/execute/`, { revision: state.stalePacket.revision, expected_fingerprint: state.stalePacket.fingerprint }, signal);
        const after = await labelEvents();
        const refusedAgain = await call(author, 'POST', 'release-requests/preview/', { resource_id: input.contextId, kind: 'agent_context', candidate_version_id: v2.id, label: 'production', target: null, evidence: [], external_pipeline: null }, signal);
        const value = { rollback: { from_version: 3, to_version: 1, events: rollbacks.length },
          aba_rejected: { status: staleAgain.status, label_events_added: after.length - events.length },
          quarantined_preview_refused: { code: JSON.stringify(refusedAgain.body).includes('context_quarantined') ? 'context_quarantined' : null } };
        return { source: 'pg', value, artifact_sha256: await record('rollback-oracle', { events, stale_status: staleAgain.status, refused_status: refusedAgain.status, value }) };
      }
      if (check.id === 'original-checkpoint-identity') {
        const first = await guestJson('/state/out/t1-1.json');
        const final = state.recoverApplied.threads.find((item) => item.key === 't1').output;
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
        const actual = await originalCase(signal);
        return { source: 'guest', value: { status: actual.status, amount: actual.amount, rule: actual.rule, delivered_root_sha256: actual.delivered_root_sha256, retired_child_bytes_delivered: actual.retired_child_bytes_delivered },
          artifact_sha256: await record('original-case-oracle', actual) };
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
        const quarantined = await call(author, 'GET', `${context}versions/2/inspection/?file_offset=0&file_limit=1`, undefined, signal);
        const value = { expired_credential_denied: [401, 403].includes(denied.status), quarantined_inspection_blocked: quarantined.status >= 400 || quarantined.result?.distribution_blocked === true };
        void blocked;
        return { source: 'api', value, artifact_sha256: await record('denial-oracle', { denied: denied.status, quarantined: quarantined.status, blocked: quarantined.result?.distribution_blocked ?? null, value }) };
      }
      throw new Error(`Independent complete-job observation unavailable: ${check.id}`);
  }

  async function teardown() {
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
            max: authz.length ? Math.round(Math.max(...authz.map((row) => row.elapsed_ns)) / 1e6) : null, host_lock_restore_ms: { restore: state.timings['restore-locked'] } },
          cold_thread_reconstruction: { threads: state.reconstruction.length, per_thread: state.reconstruction, shared_apply_command_ms: state.reconstructionSharedMs, operator_fields_typed: { reconstruction_request: 1, lost_mapping_order: 1, original_case_order: 1, original_case_request: 1 }, previously_typed_fields: 9 } }, null, 2) + '\n', { mode: 0o600, flag: 'wx' }).catch(() => {});
      } catch { /* evidence writing never masks teardown */ }
      await Promise.all(browsers.map(async ({ ctx, browser }) => { await ctx.close().catch(() => {}); await browser.close().catch(() => {}); }));
      if (pgReady) await db.end().catch(() => {});
      const result = await guest.stop().catch(() => ({ qualified: false }));
      return { qualified: result.qualified === true && browsers.every(({ browser }) => !browser.isConnected()) };
  }
}
const closureFiles = (closure) => closure.files;
