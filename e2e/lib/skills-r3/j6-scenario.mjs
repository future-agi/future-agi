import { createHash, randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { libraryFixture } from '../../fixtures/skills-r3/library.mjs';
import { requireValue } from './canonical.mjs';

const exec = promisify(execFile);
export const API = '/model-hub/skills/';

// ---------------------------------------------------------------------------
// Preregistered J6 initial conditions (synthetic fixture). Everything here uses
// the same public APIs and packaged commands a customer has. It is NOT counted
// as operator work: the measured journey starts at the retained incident.
// ---------------------------------------------------------------------------
export const GOOD_ROOT = (n) => `Reviewed support policy revision ${n}. Select only relevant skills. Never repeat a completed refund; reconcile completed business operations first.\n`;
export const HOTFIX_ROOT = 'Reviewed support policy revision 4 (unreviewed hotfix candidate). Never repeat a completed refund.\n';
export const REGRESSED_ROOT = 'Reviewed support policy revision 3.\nREFUND-WINDOW-DAYS: 7\nNever repeat a completed refund; reconcile completed business operations first.\n';
export const RUNTIME_DIGEST = `sha256:${createHash('sha256').update('j6-synthetic-runtime-v1').digest('hex')}`;
export const REFUND_REQUEST = 'Refund 25 units for an order purchased 30 days ago.';
export const VERIFY_REQUEST = 'Verify the independent business receipt for this order; take no new action.';

export function actorOf(scopeActor) {
  return { userId: scopeActor.userId, email: scopeActor.email, access: scopeActor.tokens.access, refresh: scopeActor.tokens.refresh,
    organizationId: scopeActor.organizationId, workspaceId: scopeActor.workspaceId };
}

export function publicApi(apiUrl) {
  const send = async (actor, method, path, data, extraHeaders = {}) => {
    const response = await fetch(`${apiUrl}${API}${path}`, { method, redirect: 'error', signal: AbortSignal.timeout(60_000),
      headers: { Authorization: `Bearer ${actor.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId,
        'Content-Type': 'application/json', 'Idempotency-Key': randomUUID(), ...extraHeaders },
      body: data === undefined ? undefined : JSON.stringify(data) });
    const text = await response.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = null; }
    return { status: response.status, body };
  };
  const api = async (actor, method, path, data) => {
    const { status, body } = await send(actor, method, path, data);
    requireValue(status >= 200 && status < 300, `${method} ${path} returned ${status}`);
    return body?.result;
  };
  return { send, api };
}

export async function publishContextVersion({ api, author, contextId, draft, base }) {
  const root = `contexts/${contextId}/`;
  const head = await api(author, 'POST', `${root}draft/head-review/`, { revision: draft.revision });
  const fields = { revision: head.draft_revision, expected_digest: head.candidate_digest, expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint };
  const ack = await api(author, 'POST', `${root}draft/acknowledge-head/`, fields);
  return (await api(author, 'POST', `${root}draft/publish/`, { ...fields, revision: ack.revision, review_fingerprint: ack.review_fingerprint, base_version: base, note: '', acknowledge_warnings: true })).version;
}

/** A complete reviewed pointer move through two people, as the public release workflow requires. */
export async function moveProduction({ api, author, reviewer, contextId, version }) {
  const preview = await api(author, 'POST', 'release-requests/preview/', { resource_id: contextId, kind: 'agent_context', candidate_version_id: version.id, label: 'production', target: null, evidence: [], external_pipeline: null });
  const submitted = await api(author, 'POST', 'release-requests/', { packet: preview.packet, expected_fingerprint: preview.fingerprint, recipient_ids: [reviewer.userId], revision: 0 });
  await api(reviewer, 'POST', `release-requests/${submitted.id}/decisions/`, { revision: submitted.revision, expected_fingerprint: preview.fingerprint, decision: 'approve', comment: 'Synthetic J6 fixture release', });
  return api(reviewer, 'POST', `release-requests/${submitted.id}/execute/`, { revision: submitted.revision, expected_fingerprint: preview.fingerprint });
}

export const credential = (api, actor, body) => api(actor, 'POST', 'contexts/credentials/', body);
const hours = (value) => new Date(Date.now() + value * 3_600_000).toISOString();

export function guestEnvironment({ registry = 'http://skills-fakes:8081', consume, report, bindingId }) {
  return { CTXHUB_REGISTRY_URL: registry, CTXHUB_PROVIDER_URL: 'http://skills-fakes:8082/', CTXHUB_BUSINESS_URL: 'http://skills-fakes:8083/',
    CTXHUB_CONTEXT_CREDENTIAL: consume, ...(report ? { CTXHUB_REPORT_CREDENTIAL: report } : {}), CTXHUB_BINDING_ID: bindingId, CTXHUB_RUNTIME_DIGEST: RUNTIME_DIGEST };
}

export const entrypoint = (request, state, output) => ['python', '-m', 'ctxhub_support.entrypoint', request, '--state', state, '--output', output];
export const requestFor = (thread, orderId, text = REFUND_REQUEST) => ({ schema: 1, thread, case: { case_id: `case-${orderId.slice(-12)}`, order_id: orderId, request: text } });

/**
 * Creates the retained incident on the owned stack, entirely through public
 * interfaces, and returns a private seed for the registered driver.
 */
export async function prepareJ6Scenario({ apiUrl, appUrl, author, reviewer, probe, guest, python, projectDir, prefix, expireSeconds = 300, log = () => {} }) {
  const { api, send } = publicApi(apiUrl);
  const leaves = [];
  const catalog = libraryFixture({ unsupportedMember: false });
  for (const [index, member] of catalog.skills.entries()) {
    const name = `${prefix}-${index}`;
    leaves.push(await api(author, 'POST', 'publish/', { files: member.files.map((file) => ({ ...file, content: file.content.replaceAll(member.name, name) })), labels: [], expected_release_revision: 0, acknowledge_warnings: true }));
  }
  const created = await api(author, 'POST', 'contexts/', { name: prefix, description: 'Synthetic J6 incident recovery context', tags: [], files: [{ path: 'AGENTS.md', content: GOOD_ROOT(1) }] });
  const contextId = created.context_id;
  const root = `contexts/${contextId}/`;
  const links = { revision: created.revision, links: leaves.map((leaf) => ({ skill_id: leaf.skill.id, selector: { version: 1 } })), refresh_skill_ids: [] };
  const dependencyPreview = await api(author, 'POST', `${root}dependencies/preview/`, links);
  let draft = await api(author, 'POST', `${root}dependencies/apply/`, { ...links, expected_fingerprint: dependencyPreview.fingerprint });
  const versions = [await publishContextVersion({ api, author, contextId, draft, base: null })];
  const bindings = {};
  for (const [key, label] of [['A', 'application'], ['E', 'expired-credential'], ['L', 'lost-mapping'], ['U', 'unobserved']]) {
    bindings[key] = await api(author, 'POST', `${root}bindings/`, { name: `${prefix}-${label}`, kind: 'runtime', selector: key === 'E' || key === 'L' ? { version: 1 } : { label: 'production' }, update_mode: 'new_thread', profile: 'ctxhub-support', profile_version: '0.1.0', repository: null, expected_report_interval_seconds: 3600 });
  }
  await moveProduction({ api, author, reviewer, contextId, version: versions[0] });
  log('v1 in production');

  // Checked-in project: v1 is the last reviewed commit. Local work stays untracked.
  const project = resolve(projectDir);
  await mkdir(resolve(project, 'docs'), { recursive: true });
  const projectCredential = await credential(api, author, { purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: contextId, labels: ['production'], version_ids: [] }], binding_ids: [], expires_at: hours(8) });
  const cli = async (args, env = {}) => {
    try {
      return await exec(python, ['-m', 'fi.context', ...args], { cwd: project, encoding: 'utf8', env: { PATH: process.env.PATH, HOME: process.env.HOME, FI_CONTEXT_ORIGIN: apiUrl, FI_CONTEXT_ORGANIZATION_ID: author.organizationId, FI_CONTEXT_WORKSPACE_ID: author.workspaceId, FI_CONTEXT_CREDENTIAL: projectCredential.secret, ...env }, maxBuffer: 8 * 1024 * 1024 });
    } catch (error) { throw new Error(`fi.context ${args[0]} failed: ${String(error.stdout).slice(0, 600)}`); }
  };
  const git = (...args) => exec('git', ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', ...args], { cwd: project, encoding: 'utf8' });
  await exec('git', ['init', '-q', '-b', 'main', project]);
  await writeFile(resolve(project, 'README.md'), 'Synthetic support application checkout.\n');
  await git('add', '-A'); await git('commit', '-q', '-m', 'initial checkout');
  await cli(['plan', '--name', prefix, '--label', 'production', '--client', 'codex', '--allow-preview', '--project', '.', '--out', '/tmp/' + prefix + '-install-plan.json']);
  await cli(['apply', '--plan', '/tmp/' + prefix + '-install-plan.json', '--quiescent']);
  await git('add', '-A'); await git('commit', '-q', '-m', 'reviewed context v1');
  const goodCommit = (await git('rev-parse', 'HEAD')).stdout.trim();

  for (const number of [2, 3, 4]) {
    draft = await api(author, 'POST', `${root}draft/start/`, { revision: 0, from_version: number - 1 });
    draft = await api(author, 'PUT', `${root}draft/`, { revision: draft.revision, base_version: number - 1, description: 'Synthetic J6 incident recovery context', note: '', files: [{ path: 'AGENTS.md', content: number === 3 ? REGRESSED_ROOT : number === 4 ? HOTFIX_ROOT : GOOD_ROOT(2) }] });
    versions.push(await publishContextVersion({ api, author, contextId, draft, base: number - 1 }));
  }
  await moveProduction({ api, author, reviewer, contextId, version: versions[2] });
  log('regressed v3 in production');
  await cli(['update', '--allow-preview', '--project', '.', '--plan', '/tmp/' + prefix + '-update-plan.json']);
  await cli(['apply', '--plan', '/tmp/' + prefix + '-update-plan.json', '--quiescent']);
  await writeFile(resolve(project, 'docs', 'local-notes.md'), 'Local on-call notes that must survive restore.\n');
  await git('add', '-A'); await git('commit', '-q', '-m', 'update context to v3 (regression)');
  await writeFile(resolve(project, 'docs', 'local-notes.md'), 'Local on-call notes that must survive restore.\nUncommitted note written during the incident.\n');

  // Orders and application threads under the original identities.
  const orders = {}; const threads = {};
  for (const key of ['o0', 'o1', 'o2', 'o3']) { orders[key] = `synthetic-${key}-${randomUUID()}`; await guest.seedOrder(orders[key]); }
  for (const key of ['t0', 't1', 't2', 't3']) threads[key] = randomUUID();
  const reports = {};
  for (const key of ['A', 'E', 'L']) reports[key] = await credential(api, author, { purpose: 'report', grants: [], binding_ids: [bindings[key].id], expires_at: hours(8) });
  const consume = (pins, expires, labels = []) => credential(api, author, { purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: contextId, labels, version_ids: pins.map((pin) => pin.id) }], binding_ids: [], expires_at: expires });
  const guestRun = async (key, binding, creds, thread, order, { reported = true } = {}) => {
    const envFile = await guest.envFile(`${key}-${Date.now()}`, guestEnvironment({ consume: creds.consume.secret, report: reported ? reports[binding].secret : undefined, bindingId: bindings[binding].id }));
    await guest.put(`/state/requests/${key}.json`, JSON.stringify(requestFor(thread, order)) + '\n');
    return guest.execute(envFile, entrypoint(`/state/requests/${key}.json`, `/state/${key}.sqlite`, `/state/out/${key}-prep.json`));
  };
  // T0: the incident. The regressed release declines a refund its own policy allows.
  const cV3 = { consume: await consume([], hours(8), ['production']) };
  const t0 = await guestRun('t0', 'A', cV3, threads.t0, orders.o0);
  const t0Reason = await guest.read('/state/out/t0-prep.json').then((text) => JSON.stringify({ status: JSON.parse(text).status, reason: JSON.parse(text).reason }), () => 'no output');
  requireValue(t0.code === 0, `The incident thread must complete (declined): ${t0.code} ${t0Reason}`);
  const t0Out = JSON.parse(await guest.read('/state/out/t0-prep.json'));
  requireValue(/outside the 7-day refund window/.test(t0Out.output), 'The regression must decline the refund');
  const t0Session = (await probe.pg('SELECT id::text, incarnation_id::text AS instance_id, version_id::text, digest FROM model_hub_hubskillcontextsession WHERE binding_id=$1', [bindings.A.id]))[0];
  const traceRef = { project_id: randomUUID(), trace_id: `j6-${threads.t0}`, span_id: 'refund-decision', start_time: new Date().toISOString() };
  const failure = await send(author, 'POST', `contexts/bindings/${bindings.A.id}/receipts/`, { event_id: randomUUID(), session_id: t0Session.id, instance_id: t0Session.instance_id, sequence: Date.now() * 1000 + 5000, stage: 'failed',
    version_id: t0Session.version_id, digest: t0Session.digest, materialization_digest: null, observed_at: new Date().toISOString(),
    details: { path: null, sha256: null, ranges: [], runtime_version: 'python/3.11', adapter_version: 'ctxhub-support/0.1.0', diagnostic_codes: ['refund_window_regression'], trace_ref: traceRef } },
  { Authorization: `Context ${reports.A.secret}` });
  requireValue(failure.status >= 200 && failure.status < 300, 'The retained incident receipt was rejected');
  log('incident retained');
  // T2: refund executed, registry lost, then the original credential expires.
  const cE = { consume: await consume([versions[0]], new Date(Date.now() + expireSeconds * 1000).toISOString()) };
  await guest.faults({ available: true, arm_after_business: true });
  const t2 = await guestRun('t2', 'E', cE, threads.t2, orders.o2);
  requireValue(t2.code === 2, `T2 must pause after its persisted refund: ${t2.code} ${String(t2.stderr).slice(-700)}`);
  await guest.faults({ available: true, arm_after_business: false });
  // T3: same, then the application state volume holding its session mapping is lost.
  const cL = { consume: await consume([versions[0]], hours(8)) };
  await guest.faults({ available: true, arm_after_business: true });
  // The lost thread never reported delivery: the product can only ever see an open, unobserved session.
  const t3 = await guestRun('t3', 'L', cL, threads.t3, orders.o3, { reported: false });
  requireValue(t3.code === 2, `T3 must pause after its persisted refund: ${t3.code} ${String(t3.stderr).slice(-700)}`);
  await guest.faults({ available: true, arm_after_business: false });
  await guest.remove('/state/t3.sqlite');
  await guest.remove('/state/t3.sqlite-wal');
  await guest.remove('/state/t3.sqlite-shm');
  log('paused threads prepared');

  // The application's own thread registry: where each durable thread lives, its binding and the NAMES of the environment
  // variables holding its credentials. Application configuration that predates the incident; it holds no secret and no
  // recovery decision (which thread is rechecked is named by the operator when the plan is made).
  const registryRow = (key, binding, consumeEnv, extra = {}) => ({ key, thread: threads[key], state: `/state/${key}.sqlite`, binding_id: bindings[binding].id, report_env: `${binding}_REPORT`, consume_env: consumeEnv, ...extra });
  await guest.put('/state/requests/recovery-registry.json', JSON.stringify({ schema: 1, threads: [
    registryRow('t1', 'A', 'APP_CONSUME'),
    registryRow('t2', 'E', 'E_CONSUME', { replacement_env: 'APP_CONSUME' }),
    registryRow('t3', 'L', 'L_CONSUME', { replacement_env: 'APP_CONSUME' }),
    registryRow('t0', 'A', 'APP_CONSUME', { replacement_env: 'APP_CONSUME' }),
  ] }) + '\n');

  // Retire the refund child and quarantine the alternative after all identities exist.
  const refundState = await api(author, 'GET', `${leaves[0].skill.id}/`);
  await api(author, 'DELETE', `${leaves[0].skill.id}/`, { expected_release_revision: refundState.release_revision });
  const safety = await api(author, 'GET', `version-safety/${versions[1].id}/`);
  await api(author, 'PUT', `version-safety/${versions[1].id}/`, { revision: safety.revision, quarantined: true, reason: 'Synthetic unsafe recovery alternative', replacement_version_id: versions[0].id });

  return { contextId, contextName: prefix, versions, leaves: leaves.map((leaf) => ({ skillId: leaf.skill.id, versionId: leaf.version.id })), bindings: Object.fromEntries(Object.entries(bindings).map(([key, value]) => [key, { id: value.id, name: value.name }])),
    orders, threads, goodCommit, project, projectCredential: { id: projectCredential.id },
    credentials: { v3: { id: cV3.consume.id }, E: { id: cE.consume.id, secret: cE.consume.secret, expiresAt: cE.consume.expires_at }, L: { id: cL.consume.id, secret: cL.consume.secret },
      report: Object.fromEntries(Object.entries(reports).map(([key, value]) => [key, { id: value.id, secret: value.secret }])) },
    incident: { sessionId: t0Session.id, instanceId: t0Session.instance_id, versionId: t0Session.version_id, traceRef, codes: ['refund_window_regression'] },
    catalogHash: catalog.fixture_sha256, appUrl, apiUrl };
}

export async function readText(path) { return readFile(path, 'utf8'); }
