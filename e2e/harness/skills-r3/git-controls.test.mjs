import test from 'node:test';
import assert from 'node:assert/strict';
import { GitFixture } from '../../lib/skills-r3/git-fixture.mjs';
import { Barriers, OwnedResources, ScopedClock } from '../../lib/skills-r3/controls.mjs';
import { libraryFixture, repositoryFixture, uploadCases } from '../../fixtures/skills-r3/library.mjs';

const url = (repo, suffix = '') => `https://api.github.com/repos/synthetic/${repo.name}${suffix}`;

test('complete populations include unsupported library member, deliberate pins, no-lock local adoption and all forty targets', () => {
  const library = libraryFixture();
  assert.equal(library.skills.length, 15); assert.equal(library.contexts.length, 3);
  assert.equal(library.skills.filter((s) => s.unsupported.length).length, 1);
  const nativeLibrary = libraryFixture({ unsupportedMember: false });
  assert.ok(nativeLibrary.contexts.every((context) => context.links.length === 15));
  assert.equal(nativeLibrary.skills.filter((skill) => skill.unsupported.length).length, 0);
  const repos = repositoryFixture();
  assert.equal(repos.length, 40);
  assert.deepEqual(Object.fromEntries(['follower', 'pin', 'local_modification', 'unavailable', 'unknown_response'].map((state) => [state, repos.filter((r) => r.disposition === state).length])),
    { follower: 32, pin: 4, local_modification: 2, unavailable: 1, unknown_response: 1 });
  assert.ok(repos.every((repo) => repo.lock === null));
  assert.match(repos[0].files['AGENTS.md'], /Local policy/);
  assert.ok(Buffer.byteLength(uploadCases().longUtf8[0].content) > 100 * 1024);
  assert.ok(uploadCases().pagedManifest.length > 25);
});

test('unknown PR response requires lookup reconciliation and never fabricates merge/install/load', async () => {
  const fixture = new GitFixture();
  const repo = fixture.inventory().find((r) => r.disposition === 'unknown_response');
  const init = { method: 'POST', body: JSON.stringify({ base: 'main', head: 'r3-campaign-target', title: 'Synthetic reviewed patch', body: '<!-- fixture-marker -->' }) };
  await assert.rejects(fixture.fetch(url(repo, '/pulls'), init), /disconnected/);
  assert.equal(fixture.pulls(repo.id).length, 1);
  const found = await fixture.fetch(url(repo, '/pulls?head=synthetic:r3-campaign-target'));
  const pulls = await found.json();
  assert.equal(pulls.length, 1); assert.equal(pulls[0].merged, false);
  assert.equal((await fixture.fetch(url(repo, '/pulls'), init)).status, 422);
  assert.equal(fixture.pulls(repo.id).length, 1);
  assert.equal((await fixture.fetch(url(repo, '/pulls/1/merge'), { method: 'PUT' })).status, 501);
});

test('Git transport pins refs/ETags, exposes rate/redirect failures and forbids live fallback', async () => {
  const fixture = new GitFixture(); const repo = fixture.inventory()[0];
  const response = await fixture.fetch(url(repo, '/commits/main'));
  assert.equal((await response.json()).sha, repo.base);
  assert.equal((await fixture.fetch(url(repo, '/commits/main'), { headers: { 'if-none-match': `"${repo.base}"` } })).status, 304);
  assert.equal((await fixture.fetch(url(repo, `/contents/AGENTS.md?ref=${repo.base}`))).status, 200);
  fixture.advanceBase(repo.id, 'a'.repeat(40));
  assert.equal((await fixture.fetch(url(repo, `/contents/AGENTS.md?ref=${repo.base}`))).status, 409);
  fixture.faultNext({ status: 429, retryAfter: 60 });
  assert.equal((await fixture.fetch(url(repo))).headers.get('retry-after'), '60');
  fixture.faultNext({ status: 302, location: 'https://unapproved.invalid/' });
  assert.equal((await fixture.fetch(url(repo))).status, 302);
  await assert.rejects(fixture.fetch('https://unapproved.invalid/'), /forbids unregistered host/);
  assert.equal((await fixture.fetch(url(fixture.inventory().find((r) => r.disposition === 'unavailable')))).status, 404);
  await fixture.fetch(url(repo, '?token=synthetic-query-secret'));
  assert.ok(!JSON.stringify(fixture.ledger()).includes('synthetic-query-secret'));
});

test('deterministic barrier and clock isolate scope without sleeps or production mutations', async () => {
  const barriers = new Barriers(); let done = false;
  const waiting = barriers.wait('committed-before-dispatch').then(() => { done = true; });
  await barriers.reached('committed-before-dispatch'); assert.equal(done, false);
  barriers.release('committed-before-dispatch'); await waiting; assert.equal(done, true);
  const pending = barriers.wait('before-settlement');
  barriers.close(); await assert.rejects(pending, /teardown/);
  const clock = new ScopedClock('owned-worker', '2026-11-01T00:00:00Z');
  assert.equal(clock.advance('owned-worker', 86_400_000), '2026-11-02T00:00:00.000Z');
  assert.throws(() => clock.advance('shared-worker', 1));
  assert.throws(() => clock.advance('owned-worker', -1));
});

test('owned resource teardown fences foreign workers and preserves uncertain settlement', async () => {
  const resources = new OwnedResources('run-1'); const calls = [];
  const callbacks = { id: 'guest-1', cancel: async () => { calls.push('cancel'); },
    settle: async () => { calls.push('settle'); return false; },
    terminate: async () => { calls.push('terminate'); }, destroyed: async () => true };
  assert.throws(() => resources.register({ ...callbacks, ownerCapability: 'foreign' }), /ownership/);
  resources.register({ ...callbacks, ownerCapability: resources.ownerCapability });
  const result = await resources.teardown();
  assert.equal(result.qualified, false); assert.equal(result.receipts[0].settlement, 'unknown');
  assert.deepEqual(calls, ['cancel', 'settle', 'terminate']);
  await assert.rejects(resources.teardown(), /already/);
});
