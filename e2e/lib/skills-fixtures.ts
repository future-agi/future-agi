import { randomUUID } from 'node:crypto';
import type { ApiClient } from './api-client';
import { skillsArchive } from './skills-upload';
import { sha256 } from './skills-consumption';

export type FixtureRule = {
  match: { request: string; turn: number; tool_text?: string };
  response?: { message: { role: 'assistant'; content: string | null; tool_calls?: unknown[] };
    finish_reason: string; omit_usage?: boolean };
  status?: 200 | 401 | 429 | 500; delay_ms?: number;
};
export type GitHubRecording = { url: string; status: number; headers: Record<string, string>; body_base64: string };
export type FixtureReceipt = { kind: 'model' | 'github'; request?: string; turn?: number;
  request_sha256: string; response_sha256?: string; status: number; url?: string; body_sha256?: string;
  messages?: { role: string; content: string | null; tool_call_id?: string; tool_calls?: unknown[] }[]; tools?: unknown[] };

/** Approved test-image-only service. No arbitrary URL or real credential accepted. */
export class SkillsFixtures {
  readonly namespace = `e2e-skills-${randomUUID()}`;
  private readonly origin: string;
  fixtureHash = '';

  constructor() {
    const port = process.env.E2E_SKILLS_FIXTURE_PORT ?? '28082';
    if (!/^\d{1,5}$/.test(port) || Number(port) > 65535 || Number(port) === 0) throw new Error('Invalid fixture port');
    this.origin = `http://localhost:${port}`;
  }

  private async send<T>(path: string, method: string, body?: unknown): Promise<T> {
    const response = await fetch(this.origin + path, { method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15_000) });
    if (!response.ok) throw new Error(`Skills fixture ${method} ${path}: ${response.status} ${await response.text()}`);
    return await response.json() as T;
  }

  async register(rules: FixtureRule[], github: GitHubRecording[] = []): Promise<void> {
    const result = await this.send<{ fixture_sha256: string }>('/scenarios', 'POST', { namespace: this.namespace, rules, github });
    this.fixtureHash = result.fixture_sha256;
  }

  async connect(api: ApiClient): Promise<string> {
    // model_hub/serializers/run_prompt.py:ApiKeyRequestSerializer; public scoped BYOK setup.
    const response = await api.post<{ result: { id: string } }>('/model-hub/api-keys/', { provider: 'openai', key: this.namespace });
    return response.result.id;
  }

  async ledger(): Promise<FixtureReceipt[]> {
    return (await this.send<{ ledger: FixtureReceipt[] }>(`/scenarios/${this.namespace}`, 'GET')).ledger;
  }

  async replaceRecording(recording: GitHubRecording): Promise<void> {
    await this.send(`/scenarios/${this.namespace}/github`, 'PUT', recording);
  }

  async dispose(): Promise<void> {
    await this.send(`/scenarios/${this.namespace}`, 'DELETE');
  }
}

export function toolRule(request: string, turn: number, name: string, args: Record<string, unknown>): FixtureRule {
  return { match: { request, turn }, response: { message: { role: 'assistant', content: null,
    tool_calls: [{ id: `synthetic-turn-${turn}`, type: 'function', function: { name, arguments: args } }] }, finish_reason: 'tool_calls' } };
}

export function answerRule(request: string, turn: number, answer: unknown, toolText?: string): FixtureRule {
  return { match: { request, turn, ...(toolText === undefined ? {} : { tool_text: toolText }) },
    response: { message: { role: 'assistant', content: JSON.stringify(answer) }, finish_reason: 'stop' } };
}

export function githubRecording(url: string, content: string | Buffer, type = 'application/json'): GitHubRecording {
  return { url, status: 200, headers: { 'Content-Type': type, ETag: `"${sha256(content)}"` },
    body_base64: Buffer.from(content).toString('base64') };
}

/** Recorded synthetic metadata/ref/archive shapes from github_public.py, never a live repository. */
export function githubRepository(repo: string, branches: { sha: string; files: Record<string, string> }[]) {
  if (!/^synthetic\/[a-z0-9-]+$/.test(repo) || branches.some(branch => !/^[a-f0-9]{40}$/.test(branch.sha))) {
    throw new Error('Expected synthetic repository and immutable SHAs');
  }
  const refUrl = `https://api.github.com/repos/${repo}/commits/refs%2Fheads%2Fmain`;
  const ref = (sha: string) => githubRecording(refUrl, sha, 'application/vnd.github.sha');
  const recordings = [githubRecording(`https://api.github.com/repos/${repo}`,
    JSON.stringify({ id: 170001, full_name: repo, default_branch: 'main', private: false })), ref(branches[0].sha),
    ...branches.map(branch => githubRecording(`https://codeload.github.com/${repo}/zip/${branch.sha}`,
      skillsArchive(Object.fromEntries(Object.entries(branch.files).map(([path, content]) => [`repo-${branch.sha}/${path}`, content]))),
      'application/zip'))];
  return { recordings, ref };
}
