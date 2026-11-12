import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { CampaignExecutor } from '../../../bin/lib/context-campaign.mjs';
import { J5GitFixture } from '../../lib/skills-r3/j5-git-fixture.mjs';
import { repositoryFixture } from '../../fixtures/skills-r3/library.mjs';
import { digest } from '../../lib/skills-r3/canonical.mjs';

const hash = (text) => createHash('sha256').update(text).digest('hex');
const envelope = (result, status = 200) => new Response(JSON.stringify({ result }), { status });
function setup(index = 0, { mutateGit, mutateClaim, mutateTarget, dropClaimResponse = false } = {}) {
  const repositories = repositoryFixture(); const repo = repositories[index];
  const git = new J5GitFixture(repositories);
  const campaignId = randomUUID(); const bindingId = randomUUID();
  const after = repo.files['AGENTS.md'] + '\nReviewed synthetic managed policy.\n';
  const plan = { campaign_revision: 1, binding_revision: 1, repository_id: repo.id, base_commit: repo.base,
    previous_lock_digest: null, desired_version_id: randomUUID(), materialization_digest: `sha256:${hash(after)}`,
    changes: [{ path: 'AGENTS.md', action: 'replace', before_sha256: hash(repo.files['AGENTS.md']), after_sha256: hash(after), ownership: 'adopted', diff_preview: '+ synthetic managed policy' }],
    local_review_required: true, blockers: [] };
  plan.plan_digest = `sha256:${digest(Object.fromEntries(Object.entries(plan).filter(([key]) => key !== 'campaign_revision')))}`;
  const target = { binding_id: bindingId, decision: 'include', plan, plan_digest: plan.plan_digest,
    repository: { repository_id: repo.id, default_branch: 'main' }, attempt: null };
  const claims = new Map(); const receipts = []; let generation = 0; let claimDropped = false;
  const transport = async (url, init = {}) => {
    if (url.startsWith('https://api.github.com')) {
      const response = await git.fetch(url, init);
      return mutateGit ? mutateGit(url, init, response, git) : response;
    }
    const path = new URL(url).pathname;
    if (path.endsWith('/targets/')) return envelope({ results: [mutateTarget ? mutateTarget(structuredClone(target)) : target], total_pages: 1 });
    if (path.endsWith('/claim/')) {
      const key = init.headers['Idempotency-Key'];
      if (claims.has(key)) return envelope({ ...claims.get(key), replay: true, mode: 'reconcile_only' });
      generation++;
      const claim = { attempt_id: randomUUID(), claim_generation: generation, expires_at: new Date(Date.now() + 300_000).toISOString(),
        mode: generation === 1 ? 'execute' : 'reconcile_only', replay: false, approved_plan_digest: plan.plan_digest,
        branch: `futureagi/context/${campaignId}/${bindingId}`, marker: `futureagi-context:${campaignId}:${bindingId}:${plan.plan_digest}` };
      claims.set(key, claim);
      if (dropClaimResponse && !claimDropped) { claimDropped = true; throw new TypeError('lost claim'); }
      return envelope(mutateClaim ? mutateClaim(claim) : claim);
    }
    if (path.endsWith('/attempts/')) {
      const receipt = JSON.parse(init.body); const old = receipts.find((row) => row.attempt_id === receipt.attempt_id);
      if (old && JSON.stringify(old) !== JSON.stringify(receipt)) return envelope({}, 409);
      if (!old) receipts.push(receipt);
      target.attempt = { attempt_id: receipt.attempt_id, receipt };
      return envelope(receipt);
    }
    throw new Error('unexpected_hub_route');
  };
  const executor = new CampaignExecutor({ hubUrl: 'http://localhost:8100', hubHeaders: { Authorization: 'Bearer synthetic' }, githubToken: 'synthetic', fetch: transport });
  const input = { campaignId, bindingId, repository: `synthetic/${repo.name}`, idempotencyKey: randomUUID(),
    contents: { 'AGENTS.md': Buffer.from(after).toString('base64') } };
  return { executor, input, git, repo, after, receipts, plan, target };
}

test('creates one deterministic preimage-bound PR and retains all local files', async () => {
  const f = setup(); const result = await f.executor.execute(f.input);
  assert.equal(result.state, 'pr_open'); assert.equal(result.installed, false); assert.equal(result.delivered, false);
  assert.equal(f.git.pulls(f.repo.id).length, 1);
  assert.deepEqual(f.git.files(result.repository_commit), { ...f.repo.files, 'AGENTS.md': f.after });
  const wrong = structuredClone(f.git.files(result.repository_commit)); wrong['AGENTS.md'] = 'local policy lost';
  assert.throws(() => assert.deepEqual(wrong, { ...f.repo.files, 'AGENTS.md': f.after }));
});

test('unknown PR response reconciles the existing PR without a second write', async () => {
  const f = setup(39); const first = await f.executor.execute(f.input);
  assert.equal(first.state, 'unknown'); assert.equal(f.git.pulls(f.repo.id).length, 1);
  const writes = f.git.requests.filter((row) => row.method !== 'GET').length;
  const second = await f.executor.execute({ ...f.input, idempotencyKey: randomUUID() });
  assert.equal(second.state, 'pr_open'); assert.equal(second.pull_request_number, 1);
  assert.equal(f.git.requests.filter((row) => row.method !== 'GET').length, writes);
  assert.equal(f.git.pulls(f.repo.id).length, 1);
});

test('same-key retry recovers the immutable unknown receipt without conflicting or resending', async () => {
  const f = setup(39); const first = await f.executor.execute(f.input);
  assert.equal(first.state, 'unknown');
  const writes = f.git.requests.filter((row) => row.method !== 'GET').length;
  const replay = await f.executor.execute(f.input);
  assert.equal(replay.state, 'unknown'); assert.equal(replay.replay, true);
  assert.equal(f.receipts.length, 1);
  assert.equal(f.git.requests.filter((row) => row.method !== 'GET').length, writes);
});

test('lost claim response can only reconcile, never recreate the uncertain write', async () => {
  const f = setup(0, { dropClaimResponse: true });
  await assert.rejects(f.executor.execute(f.input), /lost claim/);
  const result = await f.executor.execute(f.input);
  assert.equal(result.state, 'unknown'); assert.equal(f.git.requests.filter((row) => row.method !== 'GET').length, 0);
});

test('base movement blocks publishing and reports uncertainty honestly', async () => {
  const f = setup(); const moved = f.git.advanceUnchangedBase(f.repo.id);
  assert.notEqual(moved, f.repo.base);
  assert.deepEqual(f.git.files(moved), f.git.files(f.repo.base));
  const commit = await (await f.git.fetch(`https://api.github.com/repos/${f.input.repository}/git/commits/${moved}`)).json();
  assert.deepEqual(commit.parents, [{ sha: f.repo.base }]);
  const result = await f.executor.execute(f.input);
  assert.equal(result.state, 'unknown'); assert.match(result.reason, /Base moved/);
  assert.equal(f.git.pulls(f.repo.id).length, 0);
  assert.equal(f.git.requests.filter((row) => row.method !== 'GET').length, 0);
});

for (const fault of ['wrong-bytes', 'extra-file', 'wrong-repository', 'missing-file', 'unavailable']) {
  test(`refuses ${fault} before Git writes`, async () => {
    const f = setup(fault === 'unavailable' ? 38 : 0);
    if (fault === 'wrong-bytes') f.input.contents['AGENTS.md'] = Buffer.from('discard local policy').toString('base64');
    if (fault === 'extra-file') f.input.contents['CLAUDE.md'] = Buffer.from('unreviewed').toString('base64');
    if (fault === 'wrong-repository') f.input.repository = 'synthetic/synthetic-repo-02';
    if (fault === 'missing-file') delete f.input.contents['AGENTS.md'];
    await assert.rejects(f.executor.execute(f.input));
    assert.equal(f.git.requests.filter((row) => row.method !== 'GET').length, 0);
  });
}

test('rejects expired claims and never writes Git objects', async () => {
  const f = setup(0, { mutateClaim: (claim) => ({ ...claim, expires_at: '2020-01-01T00:00:00Z' }) });
  assert.equal((await f.executor.execute(f.input)).state, 'unknown');
  assert.equal(f.git.requests.filter((row) => row.method !== 'GET').length, 0);
});

test('server plan substitution is rejected, even with supplied replacement bytes', async () => {
  const f = setup(0, { mutateTarget: (target) => { target.plan.changes[0].after_sha256 = 'a'.repeat(64); return target; } });
  await assert.rejects(f.executor.execute(f.input), /plan_digest_mismatch/);
  assert.equal(f.git.requests.length, 0);
});

test('one-sided unexpected Git tree mutation cannot become a successful receipt', async () => {
  let trees = 0;
  const f = setup(0, { mutateGit: async (url, init, response) => {
    if (url.includes('/git/trees/') && init.method === 'GET') {
      const value = await response.json(); trees++;
      if (trees > 1) value.tree.push({ path: 'LOCAL.md', type: 'blob', mode: '100644', sha: 'f'.repeat(40) });
      return new Response(JSON.stringify(value));
    }
    return response;
  } });
  const result = await f.executor.execute(f.input);
  assert.equal(result.state, 'unknown'); assert.equal(f.git.pulls(f.repo.id).length, 0);
});

test('reads individual PR merge status and tolerates advanced base without inferring installation', async () => {
  const f = setup(0, { mutateGit: async (url, init, response) => {
    if (url.includes('/pulls?')) {
      const rows = await response.json();
      return new Response(JSON.stringify(rows.map(({ merged: _merged, ...row }) => row)));
    }
    if (/\/pulls\/\d+$/.test(url)) {
      const row = await response.json();
      // The PR base branch is live; its SHA is not the reviewed parent.
      return new Response(JSON.stringify({ ...row, base: { ...row.base, sha: 'c'.repeat(40) } }));
    }
    return response;
  } });
  const opened = await f.executor.execute(f.input);
  const merged = await f.git.fetch(`https://api.github.com/repos/${f.input.repository}/pulls/${opened.pull_request_number}/merge`, {
    method: 'PUT', body: JSON.stringify({ sha: opened.repository_commit, merge_method: 'merge' }),
  });
  assert.equal(merged.status, 200);
  const writes = f.git.requests.filter((row) => row.method !== 'GET').length;
  const observed = await f.executor.execute({ ...f.input, idempotencyKey: randomUUID() });
  assert.equal(observed.state, 'merged'); assert.equal(observed.installed, false); assert.equal(observed.delivered, false);
  assert.equal(f.git.requests.filter((row) => row.method !== 'GET').length, writes);
});

for (const mutation of ['closed-unmerged', 'wrong-detail-identity', 'missing-merge-status']) {
  test(`does not accept ${mutation} from individual PR observation`, async () => {
    const f = setup(0, { mutateGit: async (url, init, response) => {
      if (!/\/pulls\/\d+$/.test(url)) return response;
      const row = await response.json();
      if (mutation === 'closed-unmerged') Object.assign(row, { state: 'closed', merged: false });
      if (mutation === 'wrong-detail-identity') row.head.ref = 'another-branch';
      if (mutation === 'missing-merge-status') delete row.merged;
      return new Response(JSON.stringify(row));
    } });
    assert.equal((await f.executor.execute(f.input)).state, 'pr_open');
    const writes = f.git.requests.filter((row) => row.method !== 'GET').length;
    assert.equal((await f.executor.execute({ ...f.input, idempotencyKey: randomUUID() })).state, 'unknown');
    assert.equal(f.git.requests.filter((row) => row.method !== 'GET').length, writes);
  });
}

test('forty-target synthetic fixture preserves pins/exceptions and creates no duplicate PRs', async () => {
  const outcomes = [];
  for (let index = 0; index < 40; index++) {
    const f = setup(index);
    if (index >= 32 && index <= 38) {
      outcomes.push({ id: f.repo.id, state: f.repo.disposition });
      assert.deepEqual(f.git.base.inventory()[index].files, f.repo.files);
      continue;
    }
    const first = await f.executor.execute(f.input);
    const result = first.state === 'unknown' ? await f.executor.execute({ ...f.input, idempotencyKey: randomUUID() }) : first;
    assert.equal(result.state, 'pr_open'); assert.equal(f.git.pulls(f.repo.id).length, 1);
    assert.deepEqual(f.git.files(result.repository_commit), { ...f.repo.files, 'AGENTS.md': f.after });
    outcomes.push({ id: f.repo.id, state: result.state });
  }
  assert.equal(outcomes.length, 40); assert.equal(new Set(outcomes.map((row) => row.id)).size, 40);
  assert.equal(outcomes.filter((row) => row.state === 'pin').length, 4);
  assert.equal(outcomes.filter((row) => row.state === 'local_modification').length, 2);
  assert.equal(outcomes.filter((row) => row.state === 'unavailable').length, 1);
  assert.equal(outcomes.filter((row) => row.state === 'pr_open').length, 33);
});
