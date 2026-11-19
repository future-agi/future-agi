import { randomUUID, createHash } from 'node:crypto';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';

// Endpoint regression only, NOT a measured UI job or native-runtime qualification.
// All initial content and pinned manifests are explicitly synthetic fixtures.
test('J4 public pinned target changes fence an approved packet, including ABA', async ({ scopeActors, request, scopeProbe }) => {
  test.setTimeout(240_000);
  const author = scopeActors.member;
  const reviewer = scopeActors.ownerA;
  const output = resolve(process.env.J4_BINDING_EVIDENCE || '/tmp/j4-evidence/continuation/binding-http');
  await mkdir(output, { recursive: true });
  const artifacts: unknown[] = [];
  const call = async (actor: typeof author, method: string, path: string, body?: unknown, status = 200, key = randomUUID()) => {
    const response = await request.fetch(`${E2E.apiUrl}/model-hub/skills/${path}`, {
      method, data: body, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': key },
    });
    const value = await response.json();
    expect(response.status(), JSON.stringify({ path, value })).toBe(status);
    return value.result;
  };
  const wire = JSON.parse(await readFile(resolve(import.meta.dirname, '../../../frontend/src/api/skills/__tests__/r3_l07/wire-fixtures.json'), 'utf8'));
  const name = `e2e-j4-target-${Date.now().toString(36)}`;
  const context = await call(author, 'POST', 'contexts/', {
    name, description: 'Synthetic configuration review fixture', tags: [],
    files: [{ path: 'AGENTS.md', content: 'Use the explicitly reviewed synthetic configuration.\n' }],
  }, 201);
  const id = context.context_id;
  const draft = await call(author, 'GET', `contexts/${id}/draft/`);
  const head = await call(author, 'POST', `contexts/${id}/draft/head-review/`, { revision: draft.revision });
  const ack = { revision: draft.revision, expected_digest: head.candidate_digest,
    expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint };
  const acknowledged = await call(author, 'POST', `contexts/${id}/draft/acknowledge-head/`, ack);
  const published = await call(author, 'POST', `contexts/${id}/draft/publish/`, {
    ...ack, revision: acknowledged.revision, review_fingerprint: acknowledged.review_fingerprint,
    base_version: acknowledged.base_version, note: 'Synthetic target fixture', acknowledge_warnings: true,
  }, 201);
  const body = { name: 'Synthetic application', kind: 'runtime', selector: { label: 'production' },
    update_mode: 'new_thread', profile: 'synthetic', profile_version: '1', repository: null,
    expected_report_interval_seconds: 60, configuration: { ...wire.NativeConfiguration,
      observable_boundaries: ['model_request', 'root_delivery', 'checkpoint', 'child_selection', 'tool_result'].sort() } };
  // The fixture's model and connection are inert identities, never invoked here.
  const binding = await call(author, 'POST', `contexts/${id}/bindings/`, body, 201);
  const canonical = (value: any): string => value && typeof value === 'object'
    ? Array.isArray(value) ? `[${value.map(canonical).join(',')}]` : `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
    : JSON.stringify(value);
  expect(binding.configuration_digest).toBe(`sha256:${createHash('sha256').update(canonical(body.configuration)).digest('hex')}`);
  const rows = await scopeProbe.pg<{ configuration: unknown; configuration_digest: string; revision: number }>(
    'SELECT configuration, configuration_digest, revision::integer AS revision FROM model_hub_hubskillcontextbinding WHERE id=$1', [binding.id]);
  expect(rows).toEqual([{ configuration: body.configuration, configuration_digest: binding.configuration_digest, revision: 1 }]);
  artifacts.push({ boundary: 'synthetic fixture; public HTTP endpoint regression, not runtime qualification', binding: rows });
  const target = { binding_id: binding.id, environment_id: null, revision: 1, configuration_digest: binding.configuration_digest };
  const previewBody = { resource_id: id, kind: 'agent_context', candidate_version_id: published.version.id,
    label: 'production', target, evidence: [], external_pipeline: null };
  const preview = await call(author, 'POST', 'release-requests/preview/', previewBody);
  const inspection = await call(reviewer, 'POST', 'release-requests/inspection/', { ...previewBody, expected_fingerprint: preview.fingerprint });
  expect(inspection.target_configuration).toEqual(body.configuration);
  expect(inspection.capabilities.after_digest).toBe(published.version.digest);
  expect(inspection.fingerprint).toBe(preview.fingerprint);
  artifacts.push({ inspection });
  const submitted = await call(author, 'POST', 'release-requests/', {
    packet: preview.packet, expected_fingerprint: preview.fingerprint, recipient_ids: [reviewer.userId],
  }, 201);
  await call(reviewer, 'POST', `release-requests/${submitted.id}/decisions/`, {
    revision: submitted.revision, expected_fingerprint: preview.fingerprint, decision: 'approve', comment: 'Synthetic exact target decision.',
  });
  const changedBody = { ...body, configuration: { ...body.configuration, tools_digest: `sha256:${'f'.repeat(64)}` } };
  const changed = await call(author, 'PATCH', `contexts/bindings/${binding.id}/`, { revision: 1, binding: changedBody });
  expect(changed.configuration_digest).not.toBe(binding.configuration_digest);
  const execute = { revision: submitted.revision, expected_fingerprint: preview.fingerprint };
  await call(reviewer, 'POST', `release-requests/${submitted.id}/execute/`, execute, 409);
  const restored = await call(author, 'PATCH', `contexts/bindings/${binding.id}/`, { revision: 2, binding: body });
  expect(restored.configuration_digest).toBe(binding.configuration_digest);
  expect(restored.revision).toBe(3);
  await call(reviewer, 'POST', `release-requests/${submitted.id}/execute/`, execute, 409);
  const events = await scopeProbe.pg('SELECT id FROM model_hub_hubskilllabelevent WHERE skill_id=$1', [id]);
  expect(events).toEqual([]);
  artifacts.push({ changed, restored, staleAttempts: 2, labelEvents: events });
  await writeFile(resolve(output, 'observations.json'), JSON.stringify(artifacts, null, 2));
});
