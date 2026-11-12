import { createHash } from 'node:crypto';
import { GitFixture } from './git-fixture.mjs';

const hash = (value) => createHash('sha1').update(value).digest('hex');
const blob = (text) => hash(Buffer.concat([Buffer.from(`blob ${Buffer.byteLength(text)}\0`), Buffer.from(text)]));
const out = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

/** Synthetic Git-object routes around the existing repository/PR fake. No live fallback. */
export class J5GitFixture {
  constructor(repositories) {
    this.base = new GitFixture(repositories);
    this.repositories = structuredClone(repositories);
    this.commits = new Map(); this.trees = new Map(); this.refs = new Map(); this.objects = new Map();
    this.requests = [];
    this.merged = new Map();
    this.armed = new Set();
    for (const repo of repositories) {
      const tree = Object.entries(repo.files).map(([path, content]) => {
        const sha = blob(content); this.objects.set(sha, content);
        return { path, type: 'blob', mode: '100644', sha };
      });
      const sha = hash(JSON.stringify(tree));
      this.trees.set(sha, tree); this.commits.set(repo.base, { sha: repo.base, tree: { sha }, parents: [] });
    }
  }
  // A synthetic concurrent contributor can advance main without changing any
  // files. Keep a real immutable parent/tree so replanning uses the new base,
  // rather than restoring an impossible old branch or inventing a commit ID.
  advanceUnchangedBase(id) {
    const current = this.base.inventory().find((repo) => repo.id === id)?.base;
    const parent = this.commits.get(current);
    if (!parent) throw new Error('unknown_fixture_base');
    const sha = hash(JSON.stringify({ fixture: 'concurrent-empty-commit', repository: id, parent: current }));
    this.commits.set(sha, { sha, tree: { ...parent.tree }, parents: [{ sha: current }] });
    this.base.advanceBase(id, sha);
    return sha;
  }
  /** Lose the next PR-creation response for one repository after the PR is durable. */
  armUnknownResponse(id) {
    if (!this.repositories.some((repo) => repo.id === id && repo.disposition === 'unknown_response')) throw new Error('unknown_response_fixture_only');
    this.armed.add(id);
  }
  pulls(id) {
    const repo = this.repositories.find((row) => row.id === id);
    return this.base.pulls(id).map((pr) => this.enrich(repo, pr));
  }
  enrich(repo, pr) {
    const merged = this.merged.get(`${repo.id}/${pr.number}`);
    return { ...pr, ...(merged ? { state: 'closed', merged: true, merge_commit_sha: merged } : {}),
      head: { ...pr.head, sha: this.refs.get(`${repo.id}/${pr.head.ref}`) } };
  }
  files(commit) {
    return Object.fromEntries(this.trees.get(this.commits.get(commit).tree.sha).map((row) => [row.path, this.objects.get(row.sha)]));
  }
  async fetch(input, init) {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);
    if (url.origin !== 'https://api.github.com') throw new Error('No live fallback');
    const match = /^\/repos\/synthetic\/([^/]+)(?:\/(.*))?$/.exec(url.pathname);
    const repo = match && this.repositories.find((row) => row.name === match[1]);
    if (!repo || repo.disposition === 'unavailable') return this.base.fetch(request);
    const suffix = match[2] ?? '';
    this.requests.push({ repository: repo.id, method: request.method, suffix });
    if (suffix.startsWith('git/commits/') && request.method === 'GET') {
      const value = this.commits.get(suffix.slice('git/commits/'.length)); return out(value ?? {}, value ? 200 : 404);
    }
    if (suffix.startsWith('git/trees/') && request.method === 'GET') {
      const tree = this.trees.get(suffix.slice('git/trees/'.length)); return out({ tree, truncated: false }, tree ? 200 : 404);
    }
    if (suffix === 'git/trees' && request.method === 'POST') {
      const body = await request.json(); const base = this.trees.get(body.base_tree);
      if (!base) return out({}, 404);
      const tree = new Map(base.map((row) => [row.path, structuredClone(row)]));
      for (const entry of body.tree) {
        if (entry.sha === null) tree.delete(entry.path);
        else {
          const sha = blob(entry.content); this.objects.set(sha, entry.content);
          tree.set(entry.path, { path: entry.path, mode: entry.mode, type: entry.type, sha });
        }
      }
      const rows = [...tree.values()]; const sha = hash(JSON.stringify(rows)); this.trees.set(sha, rows);
      return out({ sha }, 201);
    }
    if (suffix === 'git/commits' && request.method === 'POST') {
      const body = await request.json();
      if (!this.trees.has(body.tree) || !body.parents.every((sha) => this.commits.has(sha))) return out({}, 422);
      const sha = hash(JSON.stringify(body));
      const commit = { sha, tree: { sha: body.tree }, parents: body.parents.map((sha) => ({ sha })) };
      this.commits.set(sha, commit); return out(commit, 201);
    }
    if (suffix === 'git/refs' && request.method === 'POST') {
      const body = await request.json(); const key = `${repo.id}/${body.ref.replace(/^refs\/heads\//, '')}`;
      if (this.refs.has(key)) return out({}, 422);
      if (!this.commits.has(body.sha)) return out({}, 404);
      this.refs.set(key, body.sha); return out({ ref: body.ref, object: { sha: body.sha } }, 201);
    }
    if (suffix.startsWith('contents/') && request.method === 'GET') {
      const ref = url.searchParams.get('ref');
      const path = decodeURIComponent(suffix.slice('contents/'.length));
      if (!this.commits.has(ref)) return out({}, 404);
      const files = this.files(ref);
      if (!Object.hasOwn(files, path)) return out({}, 404);
      return out({ path, sha: blob(files[path]), encoding: 'base64', content: Buffer.from(files[path]).toString('base64') });
    }
    const pull = /^pulls\/(\d+)(\/merge)?$/.exec(suffix);
    if (pull) {
      const pr = this.pulls(repo.id).find((row) => row.number === Number(pull[1]));
      if (!pr) return out({}, 404);
      if (!pull[2] && request.method === 'GET') return out(pr);
      if (pull[2] && request.method === 'PUT') {
        const body = await request.json();
        if (body.sha !== pr.head.sha || body.merge_method !== 'merge') return out({}, 409);
        if (pr.merged) return out({ merged: true, sha: pr.merge_commit_sha });
        const current = this.base.inventory().find((row) => row.id === repo.id).base;
        if (current !== pr.base.sha) return out({ merged: false }, 409);
        // A merge commit is distinct from both approved parent and PR head.
        const sha = hash(JSON.stringify({ repository: repo.id, parents: [current, pr.head.sha] }));
        this.commits.set(sha, { sha, tree: this.commits.get(pr.head.sha).tree,
          parents: [{ sha: current }, { sha: pr.head.sha }] });
        this.merged.set(`${repo.id}/${pr.number}`, sha);
        this.base.advanceBase(repo.id, sha);
        return out({ merged: true, sha });
      }
      return out({}, 501);
    }
    if (suffix === 'pulls') {
      // Delegate duplicate protection and disconnect-after-durable-creation to
      // the repo's existing GitFixture, rather than inventing success receipts.
      const response = await this.base.fetch(request);
      if (!response.ok) return response;
      if (request.method === 'POST' && this.armed.delete(repo.id)) {
        // The PR is durable before the transport is lost, exactly like the
        // base fixture's first-write fault, but for a later configured round.
        throw new TypeError('Fixture transport disconnected after durable PR creation');
      }
      const result = await response.json();
      return out(Array.isArray(result) ? result.map((pr) => this.enrich(repo, pr)) : this.enrich(repo, result), response.status);
    }
    return this.base.fetch(request);
  }
}
