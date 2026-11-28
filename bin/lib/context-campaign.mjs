import { createHash, randomUUID } from 'node:crypto';

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const commitPattern = /^[0-9a-f]{40}$/;
const digestPattern = /^sha256:[0-9a-f]{64}$/;
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const requireValue = (value, code) => { if (!value) throw new Error(code); };
const canonical = (value) => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, item[key]])) : item);

/**
 * Public, human-bound campaign executor. It never grants ownership, authorizes a
 * plan, merges a PR, or treats a Git result as installed/delivered instructions.
 * The caller supplies bytes exported from their reviewed local install plan;
 * the server's frozen plan, claim and deterministic identities remain authority.
 * Injected transports are useful for tests; the default Git host is fixed.
 */
export class CampaignExecutor {
  constructor({ hubUrl, hubHeaders, githubToken, fetch: transport = globalThis.fetch }) {
    const url = new URL(hubUrl);
    requireValue(url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)), 'secure_hub_required');
    requireValue(!url.username && !url.password && !url.search && !url.hash, 'invalid_hub_url');
    requireValue(githubToken && hubHeaders && typeof hubHeaders === 'object', 'credentials_required');
    this.hub = url.href.replace(/\/$/, '');
    this.hubHeaders = hubHeaders;
    this.githubToken = githubToken;
    this.transport = transport;
  }

  async request(url, { method = 'GET', body, github = false, signal } = {}) {
    const response = await this.transport(url, { method, redirect: 'error', signal,
      headers: github ? { Authorization: `Bearer ${this.githubToken}`, Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json' }
        : { ...this.hubHeaders, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    // Never include response bodies, credentials or local diffs in exceptions.
    if (!response.ok) {
      const error = new Error(`${github ? 'git' : 'hub'}_http_${response.status}`);
      error.status = response.status;
      throw error;
    }
    const result = await response.json();
    return github ? result : result.result;
  }

  async execute({ campaignId, bindingId, repository, contents, idempotencyKey = randomUUID(), signal }) {
    requireValue(uuidPattern.test(campaignId) && uuidPattern.test(bindingId) && uuidPattern.test(idempotencyKey), 'invalid_identity');
    requireValue(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)
      && !repository.split('/').some((part) => ['.', '..'].includes(part)), 'invalid_repository');
    requireValue(contents && typeof contents === 'object' && !Array.isArray(contents), 'patch_contents_required');
    const root = `${this.hub}/model-hub/skills/contexts/campaigns/${campaignId}/`;
    const targetRoot = `${root}targets/${bindingId}/`;
    const gitRoot = `https://api.github.com/repos/${repository}`;
    const git = (suffix, options = {}) => this.request(gitRoot + suffix, { ...options, github: true, signal });
    const hub = (url, options = {}) => this.request(url, { ...options, signal });

    // Pagination is part of the public contract; never narrow a campaign to page one.
    let target;
    for (let page = 1; page <= 100; page++) {
      const response = await hub(`${root}targets/?page=${page}`);
      requireValue(Array.isArray(response?.results) && Number.isInteger(response.total_pages)
        && response.total_pages <= 100, 'invalid_target_page');
      target = response.results.find((row) => row.binding_id === bindingId);
      if (target || page >= response.total_pages) break;
    }
    requireValue(target?.decision === 'include' && target.plan && target.repository, 'target_not_authorized');
    const plan = target.plan;
    requireValue(plan.plan_digest === target.plan_digest && digestPattern.test(plan.plan_digest)
      && commitPattern.test(plan.base_commit) && Array.isArray(plan.changes) && plan.changes.length <= 500
      && plan.repository_id === target.repository.repository_id, 'invalid_server_plan');
    const { campaign_revision: _revision, plan_digest: _digest, ...digestBody } = plan;
    requireValue(`sha256:${sha256(canonical(digestBody))}` === plan.plan_digest, 'plan_digest_mismatch');
    const paths = new Set();
    const patch = [];
    for (const change of plan.changes) {
      requireValue(typeof change.path === 'string' && !change.path.startsWith('/') && !change.path.includes('\\')
        && !/[\u0000-\u001f\u007f]/.test(change.path)
        && !change.path.split('/').some((part) => ['', '.', '..', '.git'].includes(part.toLowerCase()))
        && !paths.has(change.path), 'invalid_patch_path');
      paths.add(change.path);
      requireValue(['add', 'replace', 'remove'].includes(change.action)
        && (change.before_sha256 === null || /^[0-9a-f]{64}$/.test(change.before_sha256))
        && (change.after_sha256 === null || /^[0-9a-f]{64}$/.test(change.after_sha256)), 'invalid_change');
      if (change.action === 'remove') {
        requireValue(change.after_sha256 === null && change.before_sha256 !== null
          && !Object.hasOwn(contents, change.path), 'invalid_removal');
        patch.push({ ...change, content: null });
      } else {
        requireValue(change.after_sha256 !== null && ((change.action === 'add') === (change.before_sha256 === null))
          && typeof contents[change.path] === 'string', 'missing_patch_bytes');
        const bytes = Buffer.from(contents[change.path], 'base64');
        requireValue(bytes.toString('base64') === contents[change.path] && bytes.length <= 1_048_576
          && sha256(bytes) === change.after_sha256, 'patch_hash_mismatch');
        const text = bytes.toString('utf8');
        requireValue(Buffer.from(text).equals(bytes), 'patch_must_be_utf8');
        patch.push({ ...change, content: text });
      }
    }
    requireValue(Object.keys(contents).every((path) => patch.some((row) => row.path === path && row.content !== null)), 'unreviewed_patch_bytes');
    requireValue(patch.reduce((size, row) => size + Buffer.byteLength(row.content ?? ''), 0) <= 4 * 1024 * 1024, 'patch_too_large');
    const metadata = await git('');
    requireValue(String(metadata.id) === plan.repository_id && metadata.full_name.toLowerCase() === repository.toLowerCase()
      && metadata.default_branch === target.repository.default_branch, 'repository_identity_mismatch');
    const base = await git(`/git/commits/${plan.base_commit}`);
    requireValue(base.sha === plan.base_commit && commitPattern.test(base.tree?.sha), 'invalid_base_commit');
    const baseTree = await git(`/git/trees/${base.tree.sha}?recursive=1`);
    requireValue(baseTree.truncated === false && Array.isArray(baseTree.tree), 'incomplete_base_tree');
    for (const row of patch) {
      const entry = baseTree.tree.find((item) => item.path === row.path);
      if (row.before_sha256 === null) requireValue(!entry, 'stale_preimage');
      else {
        requireValue(entry?.type === 'blob' && ['100644', '100755'].includes(entry.mode), 'unsupported_file_mode');
        const file = await git(`/contents/${row.path.split('/').map(encodeURIComponent).join('/')}?ref=${plan.base_commit}`);
        requireValue(file.encoding === 'base64' && sha256(Buffer.from(file.content, 'base64')) === row.before_sha256, 'stale_preimage');
      }
    }
    // This is the sole write authorization. A replay is reconciliation-only even
    // when the first response was lost before any local process saw the claim.
    const claimResponse = await this.transport(`${targetRoot}claim/`, { method: 'POST', redirect: 'error', signal,
      headers: { ...this.hubHeaders, 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ plan_digest: plan.plan_digest }) });
    requireValue(claimResponse.ok, 'claim_refused');
    const claim = (await claimResponse.json()).result;
    const branch = `futureagi/context/${campaignId}/${bindingId}`;
    const marker = `futureagi-context:${campaignId}:${bindingId}:${plan.plan_digest}`;
    requireValue(claim.branch === branch && claim.marker === marker && claim.approved_plan_digest === plan.plan_digest
      && ['execute', 'reconcile_only'].includes(claim.mode) && uuidPattern.test(claim.attempt_id)
      && Number.isInteger(claim.claim_generation) && claim.claim_generation > 0, 'invalid_claim');
    if (claim.replay === true && target.attempt?.attempt_id === claim.attempt_id && target.attempt.receipt) {
      // Attempt receipts are immutable. Recover the original result, including
      // unknown, rather than trying to replace it with a later observation.
      // A separately keyed reconciliation records a new fenced observation.
      return { ...target.attempt.receipt, branch, marker, replay: true, installed: false, delivered: false };
    }
    const receipt = { attempt_id: claim.attempt_id, claim_generation: claim.claim_generation,
      state: 'unknown', repository_commit: null, pull_request_number: null, reason: '' };
    const assertLease = () => requireValue(Date.parse(claim.expires_at) > Date.now(), 'claim_expired');
    const writeGit = async (suffix, body) => {
      assertLease();
      return git(suffix, { method: 'POST', body });
    };
    try {
      // Read the exact deterministic branch before every possible creation.
      const pulls = await git(`/pulls?state=all&head=${encodeURIComponent(`${repository.split('/')[0]}:${branch}`)}&per_page=100`);
      requireValue(Array.isArray(pulls) && pulls.length < 100, 'incomplete_pr_inventory');
      const candidates = pulls.filter((pr) => pr.head?.ref === branch);
      requireValue(candidates.length <= 1, 'duplicate_pull_requests');
      if (candidates.length) {
        const candidate = candidates[0];
        requireValue(Number.isInteger(candidate.number) && candidate.number > 0, 'invalid_pull_request_number');
        // GitHub's list response is not the merge-status contract. Read the
        // individual PR; its base SHA may have advanced since review/merge.
        // verifyCommit below binds the immutable parent to the approved base.
        const pr = await git(`/pulls/${candidate.number}`);
        requireValue(pr.number === candidate.number && pr.head?.ref === branch
          && pr.body?.split('\n').includes(marker) && pr.base?.ref === metadata.default_branch
          && commitPattern.test(pr.head?.sha) && typeof pr.merged === 'boolean', 'pull_request_identity_mismatch');
        // A marker alone is not evidence of authorized bytes. Compare the whole
        // Git tree, including unchanged files, and require exactly one parent.
        await this.verifyCommit(git, pr.head.sha, plan.base_commit, baseTree, patch);
        requireValue(pr.state === 'open' || pr.merged === true, 'pull_request_closed_unmerged');
        receipt.state = pr.merged === true ? 'merged' : 'pr_open';
        receipt.repository_commit = pr.head.sha;
        receipt.pull_request_number = pr.number;
        receipt.reason = 'Reconciled exact branch, marker, parent and complete tree.';
      } else if (claim.mode === 'reconcile_only' || claim.replay === true) {
        receipt.reason = 'No matching PR observed; uncertain attempts are never replayed. Inspect or create a newly reviewed campaign.';
      } else {
        const current = await git(`/commits/${encodeURIComponent(metadata.default_branch)}`);
        requireValue(current.sha === plan.base_commit, 'base_moved');
        const tree = await writeGit('/git/trees', { base_tree: base.tree.sha, tree: patch.map((row) => ({
          path: row.path, mode: baseTree.tree.find((entry) => entry.path === row.path)?.mode ?? '100644', type: 'blob',
          ...(row.content === null ? { sha: null } : { content: row.content }),
        })) });
        requireValue(commitPattern.test(tree.sha), 'invalid_tree_response');
        const commit = await writeGit('/git/commits', { message: `Apply reviewed context plan\n\n${marker}`, tree: tree.sha, parents: [plan.base_commit] });
        requireValue(commitPattern.test(commit.sha), 'invalid_commit_response');
        await this.verifyCommit(git, commit.sha, plan.base_commit, baseTree, patch);
        // Recheck movement immediately before publishing. Never force-update an
        // existing branch; a competing writer or uncertain write stays visible.
        const beforePublish = await git(`/commits/${encodeURIComponent(metadata.default_branch)}`);
        requireValue(beforePublish.sha === plan.base_commit, 'base_moved');
        await writeGit('/git/refs', { ref: `refs/heads/${branch}`, sha: commit.sha });
        const pr = await writeGit('/pulls', { title: 'Apply reviewed agent context', head: branch,
          base: metadata.default_branch, body: `Exact reviewed repository plan. Installation and delivery are not implied.\n${marker}` });
        requireValue(Number.isInteger(pr.number) && pr.number > 0 && pr.head?.ref === branch
          && pr.head?.sha === commit.sha && pr.base?.sha === plan.base_commit
          && pr.body?.split('\n').includes(marker), 'invalid_pull_request_response');
        receipt.state = 'pr_open'; receipt.repository_commit = commit.sha; receipt.pull_request_number = pr.number;
        receipt.reason = 'Created one exact preimage-bound PR; merge, install and delivery remain unobserved.';
      }
    } catch (error) {
      // An exception after a write is not evidence that the write failed. No
      // automatic retry, no fallback host, and no false successful receipt.
      receipt.reason = error.message === 'base_moved' ? 'Base moved; review a new plan before further writes.'
        : 'Execution or reconciliation was not confirmed; inspect the deterministic branch before retry.';
    }
    // If this response is lost, the same key recovers the claim read-only. Do
    // not overwrite an immutable unknown receipt with a later guessed result.
    await hub(`${targetRoot}attempts/`, { method: 'POST', body: receipt });
    return { ...receipt, branch, marker, installed: false, delivered: false };
  }

  async verifyCommit(git, commitId, baseId, baseTree, patch) {
    const commit = await git(`/git/commits/${commitId}`);
    requireValue(commit.sha === commitId && commit.parents?.length === 1 && commit.parents[0].sha === baseId
      && commitPattern.test(commit.tree?.sha), 'commit_parent_mismatch');
    const tree = await git(`/git/trees/${commit.tree.sha}?recursive=1`);
    requireValue(tree.truncated === false && Array.isArray(tree.tree), 'incomplete_result_tree');
    const leaves = (rows) => rows.filter((row) => row.type !== 'tree');
    const expected = new Map(leaves(baseTree.tree).map((row) => [row.path, { ...row }]));
    for (const row of patch) {
      if (row.content === null) expected.delete(row.path);
      else {
        const bytes = Buffer.from(row.content);
        const blob = createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
        expected.set(row.path, { path: row.path, mode: expected.get(row.path)?.mode ?? '100644', type: 'blob', sha: blob });
      }
    }
    const actual = leaves(tree.tree);
    requireValue(actual.length === expected.size && new Set(actual.map((row) => row.path)).size === expected.size
      && actual.every((row) => { const wanted = expected.get(row.path); return wanted && row.sha === wanted.sha && row.mode === wanted.mode && row.type === wanted.type; }), 'unauthorized_tree_change');
  }
}
