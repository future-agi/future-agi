import { test, expect } from '../../lib/scope-actors';
import { knowledgeHeaders, uploadKnowledgeDocument } from '../../lib/kb-upload';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';

// Supplemental API boundary matrix for KB-E2E-001; not a second user journey.
test('knowledge-base download authorization and input boundaries', async ({ scopeActors: scopes, scopeProbe, request }, testInfo) => {
  test.setTimeout(300_000);
  const name = `e2e-kb-matrix-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const contents = `Owned bytes ${name}\n`;
  const seeded = await uploadKnowledgeDocument(request, scopes.ownerA, name, contents);
  const path = `${E2E.apiUrl}/model-hub/kb/${seeded.kbId}/download-file/`;
  const params = { file_id: seeded.fileId };
  const receipts: unknown[] = [];
  const snapshot = () => scopeProbe.pg('SELECT id, name, status, deleted, updated_at FROM model_hub_files WHERE id = $1', [seeded.fileId]);
  try {
    await expect.poll(async () => {
      const response = await request.get(path, { headers: knowledgeHeaders(scopes.ownerA), params });
      return response.status() === 200 ? response.text() : String(response.status());
    }, POLL.ASYNC_JOB).toBe(contents);
    for (const actor of [scopes.member, scopes.viewer]) {
      const response = await request.get(path, { headers: knowledgeHeaders(actor), params });
      receipts.push({ actor: actor === scopes.member ? 'member' : 'viewer-read', status: response.status(), body: await response.text() });
      expect(response.status()).toBe(200);
      expect(await response.text()).toBe(contents);
    }
    // Let the upload's asynchronous indexing finish before measuring read-only deltas.
    await expect.poll(async () => (await snapshot())[0].status, POLL.ASYNC_JOB).not.toBe('Processing');
    // Source-defined refusal matrix: no storage or file-row change on denied reads.
    for (const [label, headers, query, expected] of [
      ['foreign-org', knowledgeHeaders(scopes.ownerB), params, 403],
      ['sibling-workspace', knowledgeHeaders(scopes.withWorkspace(scopes.ownerA, scopes.emptyWorkspace.id)), params, 403],
      ['anonymous', {}, params, 401],
      ['object-selector', knowledgeHeaders(scopes.ownerA), { file_id: '../other' }, 400],
      ['encoded-selector', knowledgeHeaders(scopes.ownerA), { file_id: '..%2Fother' }, 400],
      ['unexpected-filename-parameter', knowledgeHeaders(scopes.ownerA), { ...params, file_name: 'other.txt' }, 400],
      ['missing-file', knowledgeHeaders(scopes.ownerA), {}, 400],
      ['unknown-file', knowledgeHeaders(scopes.ownerA), { file_id: '00000000-0000-0000-0000-000000000001' }, 404],
    ] as const) {
      const before = await snapshot();
      const response = await request.get(path, { headers, params: query });
      const body = await response.text();
      receipts.push({ label, status: response.status(), body });
      expect(response.status(), label).toBe(expected);
      expect(body).not.toContain(contents);
      expect(response.headers()['location']).toBeUndefined();
      expect(await snapshot(), label).toEqual(before);
    }

    // A previously signed-in reader loses workspace access without replacing their JWT.
    const removedWorkspace = await scopes.send(scopes.ownerA, 'DELETE',
      `/accounts/workspace/${scopes.ownerA.workspaceId}/members/remove/`, { user_id: scopes.member.userId });
    expect(removedWorkspace.status).toBe(200);
    const beforeRemoved = await snapshot();
    const revokedWorkspace = await request.get(path, { headers: knowledgeHeaders(scopes.member), params });
    receipts.push({ label: 'removed-workspace-member', status: revokedWorkspace.status(), body: await revokedWorkspace.text() });
    expect(revokedWorkspace.status()).toBe(403);
    expect(await snapshot()).toEqual(beforeRemoved);

    const removedOrg = await scopes.send(scopes.ownerA, 'DELETE', '/accounts/organization/members/remove/', { user_id: scopes.viewer.userId });
    expect(removedOrg.status).toBe(200);
    const beforeOrg = await snapshot();
    const revokedOrg = await request.get(path, { headers: knowledgeHeaders(scopes.viewer), params });
    receipts.push({ label: 'removed-org-member', status: revokedOrg.status(), body: await revokedOrg.text() });
    // MemberRemoveView calls clear_user_redis_cache, which deactivates all
    // AuthToken rows. Its existing-session contract is 401, before view authz.
    expect(revokedOrg.status()).toBe(401);
    expect(await revokedOrg.json()).toMatchObject({
      status: false, type: 'authentication_error', code: 'authentication_failed',
    });
    expect(await revokedOrg.text()).not.toContain(contents);
    expect(await snapshot()).toEqual(beforeOrg);

    const removedFile = await scopes.send(scopes.ownerA, 'DELETE', '/model-hub/knowledge-base/files/',
      { kb_id: seeded.kbId, file_ids: [seeded.fileId] });
    expect(removedFile.status).toBe(200);
    const unavailable = await request.get(path, { headers: knowledgeHeaders(scopes.ownerA), params });
    receipts.push({ label: 'deleted-file', status: unavailable.status(), body: await unavailable.text() });
    expect(unavailable.status()).toBe(404);
    expect(await unavailable.text()).not.toContain(contents);
  } finally {
    await testInfo.attach('download-boundary-matrix', { contentType: 'application/json', body: JSON.stringify({ seeded, receipts }) });
  }
});
