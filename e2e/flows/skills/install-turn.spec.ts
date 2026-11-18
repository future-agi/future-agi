import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { publishRoot } from '../../lib/skills-r3/j2-fixture';
import { SkillsFixtures, answerRule, toolRule } from '../../lib/skills-fixtures';

// Opt-in: the operator provisions an empty, unprivileged Linux account and the exact wheel venv.
// Run from e2e/: SKILL_INSTALL_USER=harnessguest SKILL_INSTALL_PYTHON=/tmp/harness-python/bin/python
//   yarn playwright test flows/skills/install-turn.spec.ts --workers=1
// Build the SPA with VITE_SKILL_TRACES=on. Fresh standalone databases at this revision
// also need the one-shot bootstrap command: ch25_apply_schema --files 028_skill_attribution.sql.
// No browser traces or videos: the install page legitimately displays a one-time secret.
test.skip(!process.env.SKILL_INSTALL_USER || process.platform !== 'linux', 'Requires the standalone Linux harness');
test.use({ trace: 'off', screenshot: 'off', video: 'off' });
const python = process.env.SKILL_INSTALL_PYTHON ?? '/tmp/harness-python/bin/python';
function run(script: string, data: Record<string, unknown>, guest = false) {
  const code = readFileSync(resolve('harness/install-turn', script), 'utf8');
  const args = ['-I', '-c', code];
  const options = { input: JSON.stringify(data), encoding: 'utf8' as const, timeout: 120_000 };
  const result = guest ? spawnSync('sudo', ['-n', '-H', '-u', process.env.SKILL_INSTALL_USER!, 'env', '-i',
    `HOME=/home/${process.env.SKILL_INSTALL_USER}`, 'PATH=/usr/bin:/bin', python, ...args], options)
    : spawnSync(python, args, options);
  if (result.status !== 0) {
    let msg = result.stderr ?? 'no stderr';
    for (const k of ['credential', 'apiKey', 'secretKey', 'fixture']) if (data[k]) msg = msg.replaceAll(String(data[k]), '[redacted]');
    throw new Error(`${script} (${result.status}): ${msg.slice(-2400)}`);
  }
  return JSON.parse(result.stdout.trim().split('\n').at(-1)!);
}

test('SKILLS-E2E-130: install a reviewed bundle and observe a Deep Agents turn', { tag: '@flow', annotation: flowAnnotation({
  id: 'SKILLS-E2E-130', area: 'skills', userGoal: 'Distinguish correct project installation from an actual runtime skill read.',
  steps: ['Publish a skill and a reviewed context linking its exact version.', 'Create read-only keys in the Install tabs and run their copied commands in clean projects.', 'Run Deep Agents against the deterministic stack model; inspect the matching Observe request and Runtime row.'],
  backendChecks: ['Compare installed bytes and locks with both Hub manifests.', 'Check model-boundary disclosure and the exact skill and context identities stored for this trace.'],
  browserChecks: ['Observe shows the filtered request, detail shows the loaded skill, and Runtime shows the release.'],
}) }, async ({ actor, request, page, probe }, info) => {
  test.setTimeout(480_000);
  for (const url of [E2E.appUrl, E2E.apiUrl, E2E.collectorUrl, E2E.chUrl])
    expect(['localhost', '127.0.0.1']).toContain(new URL(url).hostname);
  const base = '/model-hub/skills/';
  const call = async (method: string, path: string, data?: unknown) => {
    const res = await request.fetch(E2E.apiUrl + base + path, { method, data, headers: {
      Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId,
      'X-Workspace-Id': actor.workspaceId, 'Idempotency-Key': randomUUID(),
    } });
    if (!res.ok()) throw new Error(`${method} ${path}: HTTP ${res.status()}`);
    return res.status() === 204 ? null : (await res.json()).result;
  };
  const stamp = randomUUID().slice(0, 8), name = `handoff-${stamp}`, context = `desk-${stamp}`;
  const marker = `ORCHID-${randomUUID().slice(0, 8)}`, prompt = 'Which phrase identifies a valid handoff receipt?';
  const root = `---\nname: ${name}\ndescription: Look up the required handoff receipt phrase.\n---\nThe handoff receipt phrase is ${marker}.\n`;
  const leaf = await call('POST', 'publish/', { files: [{ path: 'SKILL.md', content: root }],
    labels: ['production'], acknowledge_warnings: true, expected_release_revision: 0 });
  const draft = await call('POST', 'contexts/', { name: context, description: 'Synthetic handoff desk', tags: [],
    files: [{ path: 'AGENTS.md', content: '# Handoff desk\nUse the linked handoff skill.\n' }] });
  const prefix = `contexts/${draft.context_id}/`, deps = { revision: draft.revision, refresh_skill_ids: [], links: [{ skill_id: leaf.skill.id, selector: { version: 1 } }] };
  const preview = await call('POST', prefix + 'dependencies/preview/', deps);
  await call('POST', prefix + 'dependencies/apply/', { ...deps, expected_fingerprint: preview.fingerprint });
  await publishRoot(call, draft.context_id, null, '# Handoff desk\nUse the linked handoff skill.\n', 'Reviewed handoff context');
  const parent = (await call('GET', prefix + 'versions/1/inspection/')).manifest;
  const skill = (await actor.api.get<any>(base + 'resolve/', { name, label: 'production' })).result;
  const files = (m: any) => Object.fromEntries(m.files.map((f: any) => [f.path, f.sha256]));
  const fixture = new SkillsFixtures(), keys: string[] = [], receipts: any[] = [];
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.context().addInitScript(org => sessionStorage.setItem('workspaceOrgId', org), actor.organizationId);
  page.setDefaultTimeout(45_000);
  const copy = async (label: string, expected: string) => {
    await page.getByRole('button', { name: label, exact: true }).click();
    const read = () => page.evaluate(() => navigator.clipboard.readText());
    await expect.poll(read).toContain(expected);
    return read();
  };
  let ctxCredential = '';
  const emit = (name: string, value: unknown) => writeFileSync(info.outputPath(name), JSON.stringify(value, null, 2));
  try {
    for (const isSkill of [true, false]) {
      const m = isSkill ? skill : parent;
      await page.goto(`/dashboard/skills/${isSkill ? leaf.skill.id : `contexts/${draft.context_id}`}?tab=use`);
      const issuing = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/contexts/credentials/'));
      await page.getByRole('button', { name: 'Create key', exact: true }).click();
      const response = await issuing;
      expect(response.ok()).toBe(true);
      const key = (await response.json()).result;
      keys.push(key.id);
      expect(key.metadata.purpose).toBe('consume');
      expect(key.metadata.grants).toEqual([{ kind: isSkill ? 'skill' : 'agent_context', resource_id: isSkill ? leaf.skill.id : draft.context_id, labels: isSkill ? ['production'] : [], version_ids: isSkill ? [] : [parent.version_id] }]);
      if (!isSkill) ctxCredential = key.secret;
      for (const profile of isSkill ? ['claude-code', 'codex'] : ['claude-code']) {
        await page.getByRole('tab', { name: profile === 'codex' ? 'Codex' : 'Claude Code', exact: true }).click();
        const plan = await copy('Copy plan command', `--client '${profile}'`);
        const apply = await copy('Copy apply command', '--quiescent');
        expect(plan.includes(key.secret) || apply.includes(key.secret)).toBe(false);
        receipts.push(run('consumer.py', { credential: key.secret, profile, kind: isSkill ? 'skill' : 'agent_context',
          skill: name, versionId: m.version_id, digest: m.digest,
          skillFiles: files(skill), manifest: files(m), commands: `${plan}\n${apply}\n` }, true));
        emit('install-receipts.json', receipts);
      }
    }
    const read = toolRule(prompt, 0, 'read_file', { file_path: `/fi_context/skills/${name}/SKILL.md` });
    // The model fixture won't answer unless the marker arrived in the tool's response, not the user prompt.
    // Deep Agents may send a system message as content blocks rather than plain text; inspect the ledger below.
    await fixture.register([read, answerRule(prompt, 1, marker, marker)]);
    const { traceId } = run('deep_agent.py', { context, digest: parent.digest, credential: ctxCredential,
      api: E2E.apiUrl, collector: E2E.collectorUrl, org: actor.organizationId, workspace: actor.workspaceId,
      apiKey: actor.apiKey, secretKey: actor.secretKey, fixture: fixture.namespace, marker, request: prompt,
      modelUrl: `http://localhost:${process.env.E2E_SKILLS_FIXTURE_PORT ?? '28082'}/v1` });
    const ledger = await fixture.ledger();
    expect(ledger.map(e => e.status)).toEqual([200, 200]);
    expect(ledger[0].messages?.some(m => m.role === "system" && JSON.stringify(m.content).includes(name))).toBe(true);
    expect(ledger[1].messages?.some(m => m.role === 'tool' && String(m.content).includes(marker))).toBe(true);
    const sql = `SELECT project_id, name, attrs_string AS attrs FROM spans FINAL WHERE trace_id={t:String}`;
    const rows = () => probe.ch<{ project_id: string; name: string; attrs: Record<string,string> }>(sql, { t: traceId });
    await expect.poll(async () => (await rows()).some(r => r.name === 'skill.load' && r.attrs['fi.skill.version_id'] === skill.version_id), { timeout: 60_000 }).toBe(true);
    const stored = await rows(), load = stored.find(r => r.name === 'skill.load')!;
    expect(load.attrs).toMatchObject({ 'fi.skill.id': leaf.skill.id, 'fi.skill.digest': skill.digest, 'fi.skill.load.evidence': 'agent_read' });
    for (const span of [load, stored.find(r => r.name === 'Install test turn')!])
      expect(span.attrs).toMatchObject({ 'fi.context.id': draft.context_id, 'fi.context.version_id': parent.version_id });
    emit('turn-receipt.json', { traceId, project: load.project_id, skill: skill.version_id, context: parent.version_id, modelTurns: ledger.map(e => e.status), spanNames: stored.map(r => r.name) });
    const detail = await actor.api.get<any>(`/tracer/trace/${traceId}/`);
    expect(JSON.stringify(detail.result.observation_spans)).toContain(skill.version_id);
    const now = Date.now();
    const usage = await actor.api.get<any>(base + 'usage/', { from: new Date(now-86400000).toISOString(), to: new Date(now).toISOString(), resource_id: leaf.skill.id, project_ids: load.project_id, group_by: 'version' });
    expect(JSON.stringify(usage.result.rows)).toContain(skill.version_id);
    await page.setViewportSize({ width: 1440, height: 900 });
    const filter = [{ column_id: 'skill.version_id', filter_config: { col_type: 'SKILL', filter_type: 'text', filter_op: 'equals', filter_value: skill.version_id } }];
    const filtered = page.waitForResponse(r => {
      if (!r.url().includes('/tracer/trace/list_traces_of_session/') || !['GET', 'POST'].includes(r.request().method())) return false;
      const query = r.request().method() === 'POST' ? r.request().postDataJSON()?.filters : new URL(r.url()).searchParams.get('filters');
      const filters = typeof query === 'string' ? JSON.parse(query) : query;
      return Array.isArray(filters) && filters.some(f => f.column_id === 'skill.version_id' && f.filter_config.filter_value === skill.version_id);
    });
    await page.goto(`/dashboard/observe/${load.project_id}/llm-tracing?tab=traces&observeLinkFilter=${encodeURIComponent(JSON.stringify(filter))}`);
    const filteredResponse = await filtered;
    expect(filteredResponse.ok()).toBe(true);
    const filteredIds = (await filteredResponse.json()).result.table.map((r: { trace_id: string }) => r.trace_id);
    expect(filteredIds).toContain(traceId);
    emit('api-reads.json', { traceId, skillVersion: skill.version_id, filteredIds, detailContainsVersion: true, usageContainsVersion: true });
    const row = page.locator(`.ag-row[row-id="${traceId}"]:visible [col-id="trace_name"]`).first();
    await expect(row).toBeVisible({ timeout: 45_000 });
    await page.screenshot({ path: info.outputPath('observe-skills-filter.png') });
    await row.click();
    await page.getByRole('button', { name: /Skills:/ }).click();
    await page.getByRole('list', { name: 'Skills loaded' }).getByText(name, { exact: true }).click();
    await expect(page.getByRole('region', { name: 'Skill load', exact: true })).toContainText(skill.digest);
    await page.screenshot({ path: info.outputPath('trace-detail.png') });
    await page.goto(`/dashboard/skills/${leaf.skill.id}?tab=runtime`);
    await expect(page.getByRole('table', { name: /Loaded by/i })).toContainText('v1', { timeout: 45_000 });
    await page.screenshot({ path: info.outputPath('skill-runtime.png') });
  } finally {
    for (const id of keys) await call('DELETE', `contexts/credentials/${id}/`);
    if (fixture.fixtureHash) {
      emit("model-boundary.json", (await fixture.ledger()).map(e => ({ turn: e.turn, status: e.status, messages: e.messages?.map(m => ({ role: m.role, shape: typeof m.content === "string" ? "text" : "blocks", catalog: JSON.stringify(m.content).includes(name), marker: JSON.stringify(m.content).includes(marker) })) })));
      await fixture.dispose();
    }
  }
});
