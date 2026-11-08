import { canonical, digest, requireValue } from './canonical.mjs';
import { repositoryFixture } from '../../fixtures/skills-r3/library.mjs';

/** In-memory HTTP transport boundary. No sockets, git commands, hooks, or real GitHub writes. */
export class GitFixture {
  #repos;
  #pulls = new Map();
  #ledger = [];
  #unknownSent = new Set();
  #faults = [];
  constructor(repositories = repositoryFixture()) { this.#repos = structuredClone(repositories); }
  inventory() { return structuredClone(this.#repos); }
  ledger() { return structuredClone(this.#ledger); }
  pulls(repositoryId) { return structuredClone(this.#pulls.get(repositoryId) ?? []); }
  faultNext({ status, retryAfter = null, location = null }) {
    requireValue([301, 302, 403, 429, 500].includes(status), 'Unsupported fixture fault');
    this.#faults.push({ status, retryAfter, location });
  }
  advanceBase(repositoryId, base) {
    requireValue(/^[a-f0-9]{40}$/.test(base), 'Full commit required');
    const repo = this.#repos.find((r) => r.id === repositoryId);
    requireValue(repo, 'Unknown repository'); repo.base = base;
  }
  async fetch(input, init) {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);
    requireValue(url.origin === 'https://api.github.com', 'Fixture forbids unregistered host; no live network fallback');
    const text = await request.text();
    this.#ledger.push({ method: request.method, path_sha256: digest(url.pathname), query_sha256: digest(url.search), request_sha256: digest(text) });
    const fault = this.#faults.shift();
    if (fault) return new Response('{}', { status: fault.status, headers: {
      ...(fault.retryAfter === null ? {} : { 'retry-after': String(fault.retryAfter) }),
      ...(fault.location === null ? {} : { location: fault.location }),
    } });
    const match = /^\/repos\/synthetic\/([^/]+)(?:\/(.*))?$/.exec(url.pathname);
    const repo = match && this.#repos.find((r) => r.name === match[1]);
    const out = (body, status = 200, headers = {}) => new Response(canonical(body), { status, headers: { 'content-type': 'application/json', ...headers } });
    if (!repo || repo.disposition === 'unavailable') return out({ message: 'Not Found' }, 404);
    const suffix = match[2] ?? '';
    if (request.method === 'GET' && suffix === '') return out({ id: Number(repo.id), full_name: `synthetic/${repo.name}`, default_branch: 'main', private: true });
    if (request.method === 'GET' && suffix.startsWith('commits/')) {
      const ref = suffix.slice('commits/'.length);
      if (ref !== 'main' && ref !== repo.base) return out({ message: 'Not Found' }, 404);
      const etag = `"${repo.base}"`;
      if (request.headers.get('if-none-match') === etag) return new Response(null, { status: 304, headers: { etag } });
      return out({ sha: repo.base }, 200, { etag });
    }
    if (request.method === 'GET' && suffix.startsWith('contents/')) {
      if (url.searchParams.get('ref') !== repo.base) return out({ message: 'Exact fixture commit required' }, 409);
      const path = decodeURIComponent(suffix.slice('contents/'.length));
      if (!(path in repo.files)) return out({ message: 'Not Found' }, 404);
      return out({ path, sha: digest(repo.files[path]).slice(0, 40), encoding: 'base64', content: Buffer.from(repo.files[path]).toString('base64') });
    }
    if (suffix === 'pulls' && request.method === 'GET') {
      const head = url.searchParams.get('head')?.replace(/^synthetic:/, '');
      return out((this.#pulls.get(repo.id) ?? []).filter((pr) => !head || pr.head.ref === head));
    }
    if (suffix === 'pulls' && request.method === 'POST') {
      let body;
      try { body = JSON.parse(text); } catch { return out({ message: 'Invalid JSON' }, 400); }
      if (body.base !== 'main' || typeof body.head !== 'string' || typeof body.body !== 'string' || typeof body.title !== 'string') return out({ message: 'Invalid PR' }, 422);
      const pulls = this.#pulls.get(repo.id) ?? [];
      if (pulls.some((pr) => pr.head.ref === body.head)) return out({ message: 'Pull request already exists' }, 422);
      const pr = { number: pulls.length + 1, state: 'open', merged: false, head: { ref: body.head }, base: { ref: 'main', sha: repo.base }, body: body.body, title: body.title };
      pulls.push(pr); this.#pulls.set(repo.id, pulls);
      if (repo.disposition === 'unknown_response' && !this.#unknownSent.has(repo.id)) {
        this.#unknownSent.add(repo.id);
        throw new TypeError('Fixture transport disconnected after durable PR creation');
      }
      return out(pr, 201);
    }
    // No implementation of merge or force push: unsupported calls cannot silently succeed.
    return out({ message: 'Fixture route not implemented' }, 501);
  }
}
