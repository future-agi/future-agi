import { randomUUID, createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';

// Prerequisite provisioning only. Not a @flow and not a measured complete job.
// Private actor credentials stay on the owned VM; never fetch this output.
test('J3 driver initial condition: three v1 parents over a v7 shared policy', async ({ request, scopeActors }) => {
  test.setTimeout(180_000);
  expect(process.platform).toBe('linux');
  const directory = process.env.J3_DRIVER_FIXTURE_DIR;
  const provenancePath = process.env.J3_PROVENANCE_PATH;
  if (!directory || !provenancePath) throw new Error('Private cloud-local fixture and provenance paths are required');
  const owner = scopeActors.ownerA;
  const author = scopeActors.member;
  const prefix = `j3-benchmark-${Date.now().toString(36)}`;
  const api = async (actor: typeof owner, method: string, path: string, data?: unknown) => {
    const response = await request.fetch(`${E2E.apiUrl}/model-hub/skills/${path}`, { method, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() }, data });
    expect(response.ok()).toBe(true);
    return (await response.json()).result;
  };
  const leaves: { id: string; name: string }[] = [];
  for (let index = 0; index < 15; index++) {
    const name = `${prefix}-leaf-${index}`;
    for (let number = 1; number <= (index ? 1 : 7); number++) {
      const result = await api(owner, 'POST', 'publish/', { files: [{ path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Synthetic shared purchase policy.\n---\nApproved synthetic policy revision ${number}.\n` }], labels: ['production'], expected_release_revision: number - 1, acknowledge_warnings: true });
      if (number === 1) leaves.push({ id: result.skill.id, name });
    }
  }
  const parents = [];
  for (let index = 0; index < 3; index++) {
    const name = `${prefix}-parent-${index}`;
    if (process.env.J3_COHORT === 'cold') { parents.push({ id: null, name }); continue; }
    const draft = await api(author, 'POST', 'contexts/', { name, description: 'Synthetic shared-policy context', tags: [], files: [{ path: 'AGENTS.md', content: '# Synthetic policy\nConsult approved linked skills.\n' }] });
    const links = leaves.map((leaf, child) => ({ skill_id: leaf.id, selector: index === 2 && child === 0 ? { version: 7 } : { label: 'production' } }));
    const config = { revision: draft.revision, links, refresh_skill_ids: [] };
    const preview = await api(author, 'POST', `contexts/${draft.context_id}/dependencies/preview/`, config);
    const applied = await api(author, 'POST', `contexts/${draft.context_id}/dependencies/apply/`, { ...config, expected_fingerprint: preview.fingerprint });
    const review = await api(author, 'POST', `contexts/${draft.context_id}/draft/head-review/`, { revision: applied.revision });
    const ack = { revision: applied.revision, expected_digest: review.candidate_digest, expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint };
    const saved = await api(author, 'POST', `contexts/${draft.context_id}/draft/acknowledge-head/`, ack);
    await api(author, 'POST', `contexts/${draft.context_id}/draft/publish/`, { ...ack, revision: saved.revision, review_fingerprint: saved.review_fingerprint, base_version: saved.base_version, note: 'Synthetic fixture initial condition, not measured work', acknowledge_warnings: true });
    parents.push({ id: draft.context_id, name });
  }
  const sha = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
  const provenance = JSON.parse(await readFile(provenancePath, 'utf8'));
  provenance.fixture_hash = sha({ leaves, parents, initialPolicyVersion: 7 });
  const actor = (value: typeof author) => ({ ...value.tokens, userId: value.userId, organizationId: value.organizationId, workspaceId: value.workspaceId });
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const issued = await owner.api.post<{ result: { api_key: string; secret_key: string } }>('/accounts/key/generate_secret_key/', { key_name: `${prefix}-sdk` });
  const { api_key: apiKey, secret_key: secretKey } = issued.result;
  // The author's own user-bound key: private paid jobs and publication are not available to consumption credentials.
  const authoredResponse = await request.fetch(`${E2E.apiUrl}/accounts/key/generate_secret_key/`, { method: 'POST', headers: scopeActors.headers(author), data: { key_name: `${prefix}-author-sdk` } });
  expect(authoredResponse.ok()).toBe(true);
  const authored = (await authoredResponse.json()) as { result: { api_key: string; secret_key: string } };
  await writeFile(`${directory}/input.json`, JSON.stringify({ appUrl: E2E.appUrl, apiUrl: E2E.apiUrl, owner: actor(owner), author: actor(author), sdkActor: { apiKey, secretKey, organizationId: owner.organizationId, workspaceId: owner.workspaceId }, authorSdk: { apiKey: authored.result.api_key, secretKey: authored.result.secret_key, organizationId: author.organizationId, workspaceId: author.workspaceId }, leaves, parents, provenance, outputDir: `${directory}/artifacts` }), { mode: 0o600 });
  for (const cohort of [process.env.J3_COHORT || 'configured']) {
    await writeFile(`${directory}/${cohort}.json`, JSON.stringify({ schema: 1, job: 'J3', cohort, evidence_scope: 'synthetic', population: { contexts: 3, catalog_members: 15, intentional_pins: 1 }, fault_ids: ['upstream-advances-during-review', 'unrelated-heartbeat', 'source-aba'],
      customer_input_hash: provenance.fixture_hash, initial_conditions_hash: sha({ leaves, parents, version: 7, cohort }), safety_requirements_hash: sha(['exact-v8', 'pin-v7', 'separate-parent-review', 'independent-label-authorization', 'fresh-resumed-boundaries']), complete_job_boundary_hash: sha('J3 full ROUND3-PLAN target flow; no narrowed boundary'),
      expected_observations: { 'dependents-and-pins': parents.map((parent, index) => `${parent.name}:${index === 2 ? 'pin-v7' : 'follower'}`), 'accepted-v8-not-v9': [8, 8, 7], 'descriptor-projection': true, 'monotonic-draft-revisions': true, 'fresh-resumed-identities': { fresh: [8, 8, 7], resumed: [7, 7, 7] }, 'source-aba-rejected': true },
    }), { mode: 0o600 });
  }
});
