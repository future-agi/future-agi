// Demo prerequisites through public product APIs only (signup, invite, publish, link, tests).
// Usage: node demo/setup.mjs <out.json> [tag]   (tag keeps rehearsal orgs distinct)
import { randomUUID } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { SKILLS, ESCALATION_V2, AGENTS_MD, RETURNS_AGENTS_MD, REQUESTS } from './content.mjs';

const API = process.env.E2E_API_URL ?? 'http://localhost:8100';
const out = process.argv[2];
const domain = process.argv[3] ?? 'acme.example';
const log = (...a) => console.log('[setup]', ...a);

async function call(actor, method, path, data, extra = {}) {
  const headers = { 'Content-Type': 'application/json', 'Idempotency-Key': randomUUID(), ...extra };
  if (actor) Object.assign(headers, { Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId });
  const res = await fetch(API + path, { method, headers, body: data === undefined ? undefined : JSON.stringify(data) });
  const text = await res.text();
  let body; try { body = JSON.parse(text); } catch { body = { raw: text.slice(0, 400) }; }
  return { status: res.status, body };
}
async function must(actor, method, path, data, extra) {
  const r = await call(actor, method, path, data, extra);
  if (r.status >= 400) throw new Error(`${method} ${path} -> ${r.status} ${JSON.stringify(r.body).slice(0, 800)}`);
  return r.body;
}
const S = (p) => `/model-hub/skills/${p}`;
const res = (b) => b.result ?? b;

async function signup(email, fullName, password) {
  await must(null, 'POST', '/accounts/e2e/clean-signup/', { email, full_name: fullName, password });
  const tokens = await must(null, 'POST', '/accounts/token/', { email, password, remember_me: true });
  const anon = { tokens, organizationId: '', workspaceId: '' };
  let info = await must({ ...anon, organizationId: undefined }, 'GET', '/accounts/user-info/').catch(() => null);
  if (!info) info = await (await fetch(API + '/accounts/user-info/', { headers: { Authorization: `Bearer ${tokens.access}` } })).json();
  const actor = { email, password, tokens, userId: info.id, organizationId: info.organization.id, workspaceId: info.default_workspace_id };
  const keys = await must(actor, 'GET', '/accounts/keys/');
  actor.apiKey = keys.data.api_key; actor.secretKey = keys.data.secret_key;
  return actor;
}

async function invite(owner, email, level) {
  const body = await must(owner, 'POST', '/accounts/organization/invite/', { emails: [email], org_level: level,
    workspace_access: [{ workspace_id: owner.workspaceId, level }] });
  const link = body.result.invites.find((i) => i.email === email).invite_link;
  const m = new URL(link.startsWith('None/') ? link.slice(4) : link, 'http://localhost').pathname.match(/accept\/([^/]+)\/([^/]+)\/?$/);
  const path = `/accounts/accept-invitation/${m[1]}/${m[2]}/`;
  const password = `Demo-${randomUUID().slice(0, 12)}!`;
  const tokens = await must(null, 'POST', path, { new_password: password, repeat_password: password });
  const actor = { email, password, tokens: { access: tokens.access, refresh: tokens.refresh }, organizationId: owner.organizationId, workspaceId: owner.workspaceId };
  const info = await must(actor, 'GET', '/accounts/user-info/');
  actor.userId = info.id;
  return actor;
}

async function publishContext(actor, id, note) {
  const draft = res(await must(actor, 'GET', S(`contexts/${id}/draft/`)));
  const review = res(await must(actor, 'POST', S(`contexts/${id}/draft/head-review/`), { revision: draft.revision }));
  const ack = { revision: draft.revision, expected_digest: review.candidate_digest, expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint };
  const saved = res(await must(actor, 'POST', S(`contexts/${id}/draft/acknowledge-head/`), ack));
  return res(await must(actor, 'POST', S(`contexts/${id}/draft/publish/`), { ...ack, revision: saved.revision, review_fingerprint: saved.review_fingerprint,
    base_version: saved.base_version, note, acknowledge_warnings: true }));
}

async function createContext(actor, name, description, agents, links) {
  const created = res(await must(actor, 'POST', S('contexts/'), { name, description, tags: [], files: [{ path: 'AGENTS.md', content: agents }] }));
  const id = created.context_id;
  const input = { revision: created.revision, links, refresh_skill_ids: [] };
  const preview = res(await must(actor, 'POST', S(`contexts/${id}/dependencies/preview/`), input));
  await must(actor, 'POST', S(`contexts/${id}/dependencies/apply/`), { ...input, expected_fingerprint: preview.fingerprint });
  const published = await publishContext(actor, id, 'First reviewed release');
  return { id, version: published.version };
}

// Release a context label through the reviewed request path (author requests, owner approves).
async function releaseContext(author, reviewer, contextId, versionId) {
  const preview = res(await must(author, 'POST', S('release-requests/preview/'), { resource_id: contextId, kind: 'agent_context', candidate_version_id: versionId, label: 'production', evidence: [] }));
  const submitted = res(await must(author, 'POST', S('release-requests/'), { packet: preview.packet, expected_fingerprint: preview.fingerprint, recipient_ids: [reviewer.userId] }));
  return { preview, submitted };
}

const stateFixture = (s) => ({ id: randomUUID(), name: 'order-state', text: JSON.stringify({ 'support-order': s }) });
const expectation = (skillId, action) => ({ id: randomUUID(), name: 'fi.application.expectations', text: JSON.stringify({ schema: 1, skill_id: skillId, action }) });
const testCase = (title, prompt, state, skillId, action, contains) => ({ id: randomUUID(), title, source_group_id: `support:${action}`, prompt,
  fixtures: [stateFixture(state), expectation(skillId, action)], expected_trigger: null, reviewed: true,
  checks: [{ id: randomUUID(), kind: 'contains', critical: true, config: { text: contains, case_sensitive: false } }] });

async function main() {
  const run = Date.now().toString(36);
  const owner = await signup(`priya.shah@${domain}`, 'Priya Shah', `Demo-${randomUUID().slice(0, 12)}!`);
  log('owner', owner.userId, owner.organizationId, owner.workspaceId);
  // The story's workspace is named after the company, not "Default Workspace".
  await must(owner, 'PUT', `/accounts/workspaces/${owner.workspaceId}/`, { display_name: 'Acme Support', name: 'Acme Support' });
  const author = await invite(owner, `maya.chen@${domain}`, 8);
  await must(author, 'POST', '/accounts/update-user-full-name/', { name: 'Maya Chen' });
  log('author', author.userId);
  const probes = {};

  const skills = {};
  for (const s of SKILLS) {
    const pub = res(await must(author, 'POST', S('publish/'), { files: s.files, labels: ['production'], expected_release_revision: 0, acknowledge_warnings: true, note: 'First reviewed version' }));
    skills[s.name] = { id: pub.skill.id, v1: pub.version };
  }
  log('skills', Object.keys(skills).join(','));
  const esc = SKILLS.find((s) => s.name === 'escalation-tone');
  const esc2 = res(await must(author, 'POST', S('publish/'), { files: [{ path: 'SKILL.md', content: ESCALATION_V2 }, esc.files[1]], labels: ['production'], expected_release_revision: 1, acknowledge_warnings: true, note: 'Offer a callback window' }));
  skills['escalation-tone'].v2 = esc2.version;

  const links = Object.entries(skills).map(([name, s]) => ({ skill_id: s.id, selector: name === 'escalation-tone' ? { version: 1 } : { label: 'production' } }));
  let ctxName = 'support-agent';
  const support = await createContext(author, ctxName, 'Acme Support agent for refunds, disputes and deliveries', AGENTS_MD, links);
  log('context', support.id, support.version?.number);
  const returns = await createContext(author, 'returns-desk', 'In-store returns desk agent', RETURNS_AGENTS_MD,
    [{ skill_id: skills['refund-policy'].id, selector: { version: 1 } }, { skill_id: skills['order-lookup'].id, selector: { label: 'production' } }]);
  log('returns context', returns.id);

  // Give context v1 the production label through the reviewed request path.
  for (const ctx of [support, returns]) {
    const inspect = res(await must(author, 'GET', S(`contexts/${ctx.id}/versions/1/inspection/`)));
    const { submitted, preview } = await releaseContext(author, owner, ctx.id, inspect.manifest.version_id);
    const decided = res(await must(owner, 'POST', S(`release-requests/${submitted.id}/decisions/`), { decision: 'approve', expected_fingerprint: preview.fingerprint, revision: submitted.revision, comment: 'Initial release' }).catch((e) => ({ error: String(e) })));
    probes[`decision-${ctx.id}`] = decided;
    if (!decided.error) {
      const req = res(await must(owner, 'GET', S(`release-requests/${submitted.id}/`)));
      const exec = await call(owner, 'POST', S(`release-requests/${submitted.id}/execute/`), { revision: req.revision, expected_fingerprint: preview.fingerprint });
      probes[`execute-${ctx.id}`] = { status: exec.status, body: JSON.stringify(exec.body).slice(0, 600) };
    }
    const detail = res(await must(author, 'GET', S(`contexts/${ctx.id}/`)));
    probes[`labels-${ctx.id}`] = detail.context?.labels;
  }

  // Reviewed application cases for the support context.
  const cases = [
    // The answer check names the refund amount, the same check as the whole-context test: v1's answer misses it.
    testCase('Damaged mug refund', REQUESTS.refund, { age_days: 10, amount: 25, disputed: false }, skills['refund-policy'].id, 'issue_refund', '$25'),
    testCase('Unrecognised card charge', REQUESTS.chargeback, { age_days: 10, amount: 25, disputed: true }, skills['chargeback-handling'].id, 'open_chargeback', 'chargeback'),
    testCase('Late package', REQUESTS.shipping, { age_days: 5, amount: 25, disputed: false }, skills['shipping-delays'].id, 'track_shipment', 'package'),
  ];
  const suite = res(await must(author, 'PUT', S(`contexts/${support.id}/tests/`), { revision: 0, cases }));
  log('suite revision', suite.revision);

  // Workspace allowance for paid test runs (synthetic provider; nothing is billed).
  const spend = res(await must(owner, 'GET', S('spend-policy/')));
  await must(owner, 'PUT', S('spend-policy/'), { revision: spend.revision, enabled: true, period_limit_usd: '20.00' });

  // Synthetic BYOK provider credential that routes whole-context tests to the deterministic fixture.
  const providerNamespace = `e2e-skills-${randomUUID()}`; // fixture-service credential format; never displayed
  await must(owner, 'POST', '/model-hub/api-keys/', { provider: 'openai', key: providerNamespace });

  // Probe: can the author move a skill label directly?
  const probeLabel = await call(author, 'GET', S(`${skills['refund-policy'].id}/`));
  probes.skillDetail = { status: probeLabel.status, release_revision: probeLabel.body?.result?.release_revision, permissions: probeLabel.body?.result?.permissions };

  writeFileSync(out, JSON.stringify({ run, providerNamespace, owner, author, skills, support, returns, cases: suite.cases?.map((c) => ({ id: c.id, title: c.title })), probes }, null, 2), { mode: 0o600 });
  log('probes', JSON.stringify(probes, null, 1).slice(0, 3000));
  log('done', out);
}
main().catch((e) => { console.error('[setup] FAILED', e.stack || e); process.exit(1); });
