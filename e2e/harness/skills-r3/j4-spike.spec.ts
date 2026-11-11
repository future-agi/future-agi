import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { SkillsFixtures, answerRule } from '../../lib/skills-fixtures';

// Temporary design spike (not a flow): draft-vs-published digest, role change,
// annotation append and refusal codes on the standalone stack.
test('J4 spike', async ({ scopeActors, request, scopeProbe }) => {
  test.setTimeout(600_000);
  const out: Record<string, unknown> = {};
  const admin = scopeActors.ownerA;
  const author = scopeActors.member;
  const reviewer = scopeActors.viewer;
  const call = async (actor: typeof author, method: string, path: string, body?: unknown, base = '/model-hub/skills/') => {
    const response = await request.fetch(`${E2E.apiUrl}${base}${path}`, { method, data: body, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() } });
    const text = await response.text();
    let value: any; try { value = JSON.parse(text); } catch { value = text; }
    out[`${method} ${path} ${Object.keys(out).length}`] = { status: response.status(), value };
    return { status: response.status(), value, result: value?.result };
  };
  try {
    const role = await call(admin, 'POST', 'organization/members/role/', { user_id: reviewer.userId, ws_level: 8, workspace_id: admin.workspaceId }, '/accounts/');
    out.roleStatus = role.status;
    const name = `e2e-j4-spike-${Date.now().toString(36)}`;
    const ctx = await call(author, 'POST', 'contexts/', { name, description: 'Spike context', tags: [], files: [{ path: 'AGENTS.md', content: 'Baseline policy.\n' }] });
    const id = ctx.result.context_id;
    const publish = async (actor: typeof author) => {
      const draft = (await call(actor, 'GET', `contexts/${id}/draft/`)).result;
      const head = (await call(actor, 'POST', `contexts/${id}/draft/head-review/`, { revision: draft.revision })).result;
      const ack = { revision: draft.revision, expected_digest: head.candidate_digest, expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint };
      const acknowledged = (await call(actor, 'POST', `contexts/${id}/draft/acknowledge-head/`, ack)).result;
      return (await call(actor, 'POST', `contexts/${id}/draft/publish/`, { ...ack, revision: acknowledged.revision, review_fingerprint: acknowledged.review_fingerprint, base_version: acknowledged.base_version, note: 'spike', acknowledge_warnings: true })).result;
    };
    const v1 = await publish(author);
    // No public context label route exists: production moves through a release request.
    const pv = (await call(author, 'POST', 'release-requests/preview/', { resource_id: id, kind: 'agent_context', candidate_version_id: v1.version.id, label: 'production', target: null, evidence: [], external_pipeline: null })).result;
    const sub = (await call(author, 'POST', 'release-requests/', { packet: pv.packet, expected_fingerprint: pv.fingerprint, recipient_ids: [admin.userId] })).result;
    await call(admin, 'POST', `release-requests/${sub.id}/decisions/`, { revision: sub.revision, expected_fingerprint: pv.fingerprint, decision: 'approve', comment: 'baseline' });
    const ex = await call(admin, 'POST', `release-requests/${sub.id}/execute/`, { revision: sub.revision, expected_fingerprint: pv.fingerprint });
    out.baselineStatus = ex.status;
    const started = await call(author, 'POST', `contexts/${id}/draft/start/`, { revision: 0, from_version: 1 });
    const draft = started.result ?? (await call(author, 'GET', `contexts/${id}/draft/`)).result;
    const saved = await call(author, 'PUT', `contexts/${id}/draft/`, { revision: draft.revision, base_version: 1, files: [{ path: 'AGENTS.md', content: 'Candidate policy requiring independent approval.\n' }], description: 'Spike context', note: '' });
    const fake = new SkillsFixtures();
    const prompt = `${name}: may the agent refund?`;
    await fake.register([answerRule(prompt, 0, { eligible: false })]);
    await fake.connect(author.api);
    const current = (await call(author, 'GET', `contexts/${id}/draft/`)).result;
    out.draftDigest = current.digest;
    const config = { candidate: { kind: 'draft', revision: current.revision, digest: current.digest },
      cases: { kind: 'ad_hoc', cases: [{ id: randomUUID(), title: 'refund boundary', source_group_id: randomUUID(), prompt, fixtures: [], expected_trigger: null, reviewed: true,
        checks: [{ id: randomUUID(), kind: 'json_equals', critical: true, config: { pointer: '/eligible', value: false } }] }] },
      baselines: [{ kind: 'production' }], competitors: [], harness: 'contexts-text-v1.2', model: { model_id: 'gpt-4o-mini', temperature: 0, max_output_tokens: 128 },
      judge: null, profile: 'quick', repetitions: 1, limits: { max_total_tokens: 150000, max_estimated_cost_usd: '1' }, purpose: 'regression', parent_run_id: null };
    const preview = await call(author, 'POST', `contexts/${id}/test-runs/preview/`, config);
    const run = await call(author, 'POST', `contexts/${id}/test-runs/`, { configuration: config, expected_fingerprint: preview.result?.fingerprint, acknowledge_provider_processing: true,
      inventory_review: { fingerprint: preview.result?.fingerprint, reviewed_all_files: true, no_known_answer_keys: true, classifications: [], false_positive_secret_findings: [] } });
    const runId = run.result?.id;
    await expect.poll(async () => (await call(author, 'GET', `contexts/${id}/test-runs/${runId}/`)).result?.status, POLL.ASYNC_JOB).toMatch(/completed|failed|incomplete/);
    out.runRows = await scopeProbe.pg('SELECT candidate_digest, visibility, payload_expires_at FROM model_hub_hubskilltestrun WHERE job_id=$1', [runId]);
    const v2 = await publish(author);
    out.v2 = v2;
    const options = await call(author, 'GET', `release-requests/options/?resource_id=${id}&kind=agent_context&candidate_version_id=${v2?.version?.id}`);
    out.optionEvidence = options.result?.evidence?.length;
    await call(author, 'POST', `contexts/${id}/test-runs/${runId}/share/`, { acknowledge_snapshot_sharing: true });
    const results = await call(author, 'GET', `contexts/${id}/test-runs/${runId}/results/`);
    const ann = await call(admin, 'POST', `evidence/skills_text/${runId}/annotations/`, { source: 'skills_text', source_id: runId, annotation_revision: 0, check_id: null, attempt_id: null, verdict: 'agree', cited_text: '', comment: 'late confirmation' });
    out.annotationStatus = ann.status;
    out.resultsShape = JSON.stringify(results.result).slice(0, 1500);
    await fake.dispose();
  } finally {
    await writeFile('/tmp/j4-spike.json', JSON.stringify(out, null, 2));
  }
});
