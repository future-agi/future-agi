import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { test as base, expect } from './skills-native-fixtures';
import { E2E } from './env';
import { publishRoot } from './skills-r3/j2-fixture';
import type { TestActor } from './provisioning';
import type { APIRequestContext } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
type Version = { id: string; number: number; digest: string };
type Resource = { id: string; name: string; version: Version };
type Selector = { version: number } | { label: string };
export type Agent = { name: string; skills: { name: string; selector: Selector }[]; context?: { name: string; selector: Selector; reads?: string[] } };

// Read the package identity from the isolated interpreter, independently of
// the API response and UI assertions. Do not expect an unpublished version.
export function installedPythonSdkVersion(noOtel = false): string {
  const python = noOtel ? process.env.SKILL_TRACES_NO_OTEL_PYTHON : process.env.SKILL_TRACES_PYTHON;
  if (!python) throw new Error('Prepare the lock-verified Skills Python interpreters first');
  const result = spawnSync(python, ['-I', '-c', "from importlib.metadata import version; print(version('futureagi'))"],
    { encoding: 'utf8', timeout: 10000 });
  const value = result.stdout?.trim();
  if (result.status !== 0 || !value || !/^\d[\w.+-]*$/.test(value)) throw new Error('Cannot read installed SDK version');
  return value;
}

export async function createTraceScenario(actor: TestActor, http: APIRequestContext) {
  const prefix = `st12-${randomUUID().slice(0, 8)}`;
  const project = `${prefix}-support`;
  const call = async (method: string, path: string, data?: unknown) => {
    const response = await http.fetch(`${E2E.apiUrl}/model-hub/skills/${path}`, { method, data, headers: {
      Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId,
      'X-Workspace-Id': actor.workspaceId, 'Idempotency-Key': randomUUID(),
    }});
    if (!response.ok()) throw new Error(`${method} ${path} returned ${response.status()}`);
    return response.status() === 204 ? undefined : (await response.json()).result;
  };
  const publish = async (name: string): Promise<Resource> => {
    const body = await call('POST', 'publish/', { files: [{ path: 'SKILL.md', content:
      `---\nname: ${name}\ndescription: Review the synthetic ${name} fixture.\n---\nRead the support policy.\n` }],
      labels: ['production'], expected_release_revision: 0, acknowledge_warnings: true });
    return { id: body.skill.id, name, version: body.version };
  };
  const [refund, tone, unread] = await Promise.all(['refund-policy', 'tone', 'unread'].map(name => publish(`${prefix}-${name}`)));
  const name = `${prefix}-context`;
  const text = '# Support\nRead only skills relevant to this request.\n';
  const created = await call('POST', 'contexts/', { name, description: 'Support app', tags: [], files: [{ path: 'AGENTS.md', content: text }] });
  const id = created.context_id;
  const draft = await call('GET', `contexts/${id}/draft/`);
  const deps = { revision: draft.revision, links: [refund, tone, unread].map(s => ({ skill_id: s.id, selector: { label: 'production' } })), refresh_skill_ids: [] };
  const preview = await call('POST', `contexts/${id}/dependencies/preview/`, deps);
  await call('POST', `contexts/${id}/dependencies/apply/`, { ...deps, expected_fingerprint: preview.fingerprint });
  const context: Resource = { id, name, version: (await publishRoot(call, id, null, text, 'Initial context')).version };
  const grant = (r: Resource, kind: string) => ({ kind, resource_id: r.id, labels: kind === 'skill' ? ['production'] : [], version_ids: [r.version.id] });
  const issue = (currentContext: Resource) => call('POST', 'contexts/credentials/', { purpose: 'consume', binding_ids: [],
    grants: [...[refund, tone, unread].map(s => grant(s, 'skill')), grant(currentContext, 'agent_context')],
    expires_at: new Date(Date.now() + 3600000).toISOString() });
  let issued = await issue(context);
  const advanceContext = async (version:Version) => {
    await call('DELETE', `contexts/credentials/${issued.id}/`);
    issued = await issue({...context,version});
  };
  const agent = (name: string, resources: Resource[], withContext = true): Agent => ({ name,
    skills: resources.map(s => ({ name: s.name, selector: { version: s.version.number } })),
    ...(withContext ? { context: { name: context.name, selector: { version: context.version.number } } } : {}) });
  const emit = (lang: 'python' | 'typescript' | 'noOtel', agents: Agent[], options: { off?: boolean; project?: string; failedRequest?: boolean; oldVersion?: boolean; fetchOnly?: boolean; damaged?: boolean; requests?: number } = {}) => {
    const env = { ...process.env, E2E_STACK: 'standalone', FI_SKILLS_TRACING: options.off ? 'off' : 'on' };
    const node = lang === 'typescript';
    const interpreter = lang === 'noOtel' ? process.env.SKILL_TRACES_NO_OTEL_PYTHON : process.env.SKILL_TRACES_PYTHON;
    if (!node && !interpreter) throw new Error(`Set the ${lang} standalone interpreter path`);
    const invocation = spawnSync(node ? `${root}/bin/e2e` : interpreter!, node ? ['compose', 'run', '--rm', '--no-deps', '-T', 'skills-ts-runtime'] : [`${root}/e2e/stack/native-runtime/emit.py`], {
      cwd: root, env, encoding: 'utf8', timeout: 90000, input: JSON.stringify({ credential: issued.secret,
        project: options.project ?? project, agents, noOtel: lang === 'noOtel', ...options,
        ...(lang !== 'noOtel' ? { apiKey: actor.apiKey, secretKey: actor.secretKey } : {}) }) });
    if (invocation.error || invocation.status !== 0) {
      // Never attach inputs, request headers, or arbitrary SDK/transport diagnostics.
      const errorClass = invocation.stderr?.match(/^([A-Za-z.]+(?:Error|Exception)):/m)?.[1] ?? 'runtime error';
      throw new Error(`${lang} exited ${invocation.status}: ${errorClass}`);
    }
    return JSON.parse(invocation.stdout.trim().split('\n').at(-1)!).trace_id as string | null;
  };
  const publishV2 = async () => {
    const detail = await call('GET', `${refund.id}/`);
    const next = await call('POST','publish/', { files: [{path:'SKILL.md', content:`---\nname: ${refund.name}\ndescription: Review an updated synthetic refund.\n---\nRevised support instructions.\n`}],
      labels:[], expected_release_revision:detail.release_revision, acknowledge_warnings:true });
    expect(next.version.number).toBe(2);
    return next.version as Version;
  };
  const waitForTraces = async (traces:number) => {
    await expect.poll(async () => {
      const window = new URLSearchParams({ from:new Date(Date.now()-3600000).toISOString(), to:new Date().toISOString(), resource_id:refund.id, group_by:'version' });
      return (await call('GET', `usage/?${window}`)).summary.traces;
    }, {timeout:45000, intervals:[500,1000,2000]}).toBe(traces);
  };
  return { project, context, refund, tone, unread, call, agent, emit, publishV2, advanceContext, waitForTraces,
    revoke: () => call('DELETE', `contexts/credentials/${issued.id}/`) };
}
export const test = base.extend<{ scenario: Awaited<ReturnType<typeof createTraceScenario>> }>({
  scenario: async ({ actor, request }, use) => {
    const data = await createTraceScenario(actor, request);
    try { await use(data); } finally { await data.revoke(); }
  },
});
export { expect };
