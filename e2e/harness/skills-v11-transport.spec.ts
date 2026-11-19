import { randomUUID } from 'node:crypto';
import { test, expect } from '../lib/scope-actors';
import { E2E } from '../lib/env';
import { POLL } from '../lib/state-probe';
import { SkillsFixtures, answerRule, toolRule, githubRepository } from '../lib/skills-fixtures';
import { skillDigest, sha256 } from '../lib/skills-consumption';

type Envelope<T> = { result: T };
const SKILLS = '/model-hub/skills/'; // urls_skills_hub.py and urls_skills_hub_v11.py

test('Skills v1.1 harness: synthetic repositories have stable, isolated identities', () => {
  const branches = [{ sha: 'a'.repeat(40), files: { 'SKILL.md': 'synthetic fixture' } }];
  const first = githubRepository('synthetic/first', branches);
  const same = githubRepository('synthetic/first', branches);
  const other = githubRepository('synthetic/other', branches);
  const metadata = (fixture: ReturnType<typeof githubRepository>) => JSON.parse(Buffer.from(fixture.recordings[0].body_base64, 'base64').toString());
  expect(first.recordings).toEqual(same.recordings);
  expect(metadata(first).id).not.toBe(metadata(other).id);
  expect(metadata(first)).toMatchObject({ full_name: 'synthetic/first', private: false, default_branch: 'main' });
  expect(first.ref('b'.repeat(40)).url).toBe('https://api.github.com/repos/synthetic/first/commits/refs%2Fheads%2Fmain');
});

test('Skills v1.1 harness: real scoped adapter and Temporal persist structured tool turns', async ({ scopeActors, scopeProbe, request }, testInfo) => {
  test.setTimeout(180_000); // provisioning + two transport/Temporal stages, each <=60s
  const actor = scopeActors.ownerA;
  const fake = new SkillsFixtures();
  const name = `e2e-skills-h2-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prompt = `${name}: evaluate a synthetic boundary request`;
  const files = [
    { path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Use when checking synthetic policy boundary requests in a text fixture.\n---\nRead [policy](references/policy.md).\n` },
    { path: 'references/policy.md', content: 'Synthetic policy café 漢字. No actual refunds.\n' },
  ];
  await fake.register([
    toolRule(prompt, 0, 'load_skill', { skill_id: '$skill_id' }),
    toolRule(prompt, 1, 'read_skill_file', { skill_id: '$skill_id', path: 'references/policy.md', offset: 0, max_bytes: 24000 }),
    answerRule(prompt, 2, { eligible: true }),
  ]);
  try {
    const connectionId = await fake.connect(actor.api);
    for (const foreign of [scopeActors.ownerB, scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id)]) {
      const options = (await foreign.api.get<Envelope<{ models: { can_run: boolean; disabled_reason: string }[] }>>(`${SKILLS}testing-options/`)).result;
      expect(options.models.map(model => [model.can_run, model.disabled_reason])).toEqual([[false, 'model_not_configured']]);
    }
    const pushed = await actor.api.post<Envelope<{ skill: { id: string }; version: { id: string; digest: string } }>>(`${SKILLS}publish/`, {
      files, labels: ['production'], expected_release_revision: 0,
    });
    const id = pushed.result.skill.id;
    const draft = (await actor.api.put<Envelope<{ revision: number }>>(`${SKILLS}${id}/draft/`, {
      files, revision: 0, base_version: 1,
    })).result;
    // serializers/skills_hub_v11.py: CaseInput and RunConfig. No seeded outcomes.
    const caseId = randomUUID();
    const config = { candidate: { kind: 'draft', revision: draft.revision, digest: skillDigest(files) },
      cases: { kind: 'ad_hoc', cases: [{ id: caseId, title: name, source_group_id: randomUUID(), prompt,
        fixtures: [], expected_trigger: true, reviewed: true, checks: [
          { id: randomUUID(), kind: 'json_equals', critical: true, config: { pointer: '/eligible', value: true } },
          { id: randomUUID(), kind: 'file_delivered', critical: true, config: { path: 'references/policy.md', complete: true } },
        ] }] },
      baselines: ['production'], competitors: [], model: { model_id: 'gpt-4o-mini', temperature: 0, max_output_tokens: 128 },
      judge: null, profile: 'quick', repetitions: 1, limits: { max_total_tokens: 150000, max_estimated_cost_usd: '1' },
      purpose: 'regression', parent_run_id: null };
    const preview = (await actor.api.post<Envelope<{ fingerprint: string; outbound_findings: unknown[] }>>(`${SKILLS}${id}/test-runs/preview/`, config)).result;
    expect(preview.outbound_findings).toEqual([]);
    expect(await fake.ledger()).toEqual([]); // preflight is non-paid
    const started = await request.post(`${E2E.apiUrl}${SKILLS}${id}/test-runs/`, {
      headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() },
      data: { configuration: config, expected_fingerprint: preview.fingerprint, acknowledge_provider_processing: true,
        inventory_review: { fingerprint: preview.fingerprint, reviewed_all_files: true, no_known_answer_keys: true,
          classifications: [], false_positive_secret_findings: [] } },
    });
    expect(started.status(), await started.text()).toBe(202);
    const run = (await started.json()).result;
    await testInfo.attach('transport-seeded-ids', { body: JSON.stringify({ skillId: id, runId: run.id, caseId, fixtureHash: fake.fixtureHash }), contentType: 'application/json' });
    await expect.poll(async () => (await actor.api.get<Envelope<{ status: string }>>(`${SKILLS}${id}/test-runs/${run.id}/`)).result.status, POLL.ASYNC_JOB).toBe('completed');
    const rows = await scopeProbe.pg<{ variant: string; answer: string; target_loaded: boolean; root_fully_delivered: boolean; answer_verdict: string; delivery_verdict: string }>(
      'SELECT variant, answer, target_loaded, root_fully_delivered, answer_verdict, delivery_verdict FROM model_hub_hubskilltestattempt WHERE run_id = $1 ORDER BY variant', [run.id]);
    expect(rows).toEqual(['candidate', 'production'].map(variant => ({ variant, answer: '{"eligible":true}',
      target_loaded: true, root_fully_delivered: true, answer_verdict: 'pass', delivery_verdict: 'pass' })));
    const ledger = await fake.ledger();
    expect(ledger.map(row => [row.request, row.turn, row.status]).sort()).toEqual(
      [0, 0, 1, 1, 2, 2].map(turn => [prompt, turn, 200]).sort());
    for (const final of ledger.filter(row => row.turn === 2)) {
      const delivered = final.messages!.filter(message => message.role === 'tool').map(message => JSON.parse(message.content!));
      expect(delivered.map(item => [item.path, item.sha256, item.text])).toEqual(files.map(file => [file.path, sha256(file.content), file.content]));
    }
    await testInfo.attach('synthetic-transport-ledger', { body: JSON.stringify(ledger), contentType: 'application/json' });
    const usage = (await actor.api.get<Envelope<{ total: number }>>(`${SKILLS}${id}/usage/`)).result;
    expect(usage.total).toBe(0); // internal progressive reads are not customer fetches
    await actor.api.delete(`/model-hub/api-keys/${connectionId}/`);
    await expect(actor.api.post(`${SKILLS}${id}/test-runs/preview/`, config))
      .rejects.toMatchObject({ status: 422, body: { code: 'model_not_configured' } });
    expect(await fake.ledger()).toEqual(ledger);
  } finally { await fake.dispose(); }
});

test('Skills v1.1 harness: recorded multi-root continuation preserves the original SHA', async ({ scopeActors, request }, testInfo) => {
  test.setTimeout(240_000); // setup plus three generic Temporal boundaries
  const fake = new SkillsFixtures();
  const actor = scopeActors.ownerB;
  const name = `e2e-skills-h3-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const repo = `synthetic/${name}`;
  const root = (name: string) => `---\nname: ${name}\ndescription: Use when qualifying a synthetic public source without any real repository.\n---\n# Synthetic instructions\n`;
  const files = { 'first/SKILL.md': root(name), 'second/SKILL.md': root(name + '-other'),
    LICENSE: 'MIT License\nCopyright synthetic fixture authors. Permission is hereby granted, free of charge, to any person obtaining a copy.\n',
    NOTICE: 'Synthetic source qualification notice.\n' };
  const source = githubRepository(repo, [{ sha: 'a'.repeat(40), files }, { sha: 'b'.repeat(40), files }]);
  await fake.register([], source.recordings);
  const start = async (data: object) => {
    const response = await request.post(`${E2E.apiUrl}${SKILLS}github/previews/`, {
      headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() }, data,
    });
    expect(response.status(), await response.text()).toBe(202);
    const id = (await response.json()).result.id;
    await expect.poll(async () => (await actor.api.get<Envelope<{ status: string }>>(`${SKILLS}github/previews/${id}/`)).result.status, POLL.ASYNC_JOB).toBe('completed');
    return (await actor.api.get<Envelope<{ id: string; outcome: string; roots: string[]; source: { commit_sha: string; ref: string; ref_kind: string }; bundle: { files: { path: string }[] } }>>(`${SKILLS}github/previews/${id}/`)).result;
  };
  try {
    const parent = await start({ url: `https://github.com/${repo}`, path: null, ref: 'main' });
    expect(parent.outcome).toBe('needs_path');
    expect(parent.roots).toEqual(['first', 'second']);
    await fake.replaceRecording(source.ref('b'.repeat(40)));
    // Expire the ref cache and enter the next unchanged budget period. A broken
    // continuation that re-resolves main must now observe B rather than pass by cache.
    await fake.advanceGitHubTime(3600);
    const child = await start({ parent_preview_id: parent.id, path: 'first' });
    expect(child.outcome).toBe('ready');
    expect(child.source).toMatchObject({ commit_sha: 'a'.repeat(40), ref: 'main', ref_kind: 'branch' });
    // repository_zip.py: inherited notices get content-independent source-path names.
    expect(child.bundle.files.map(file => file.path).sort()).toEqual(['SKILL.md',
      `references/upstream-notices/${sha256('LICENSE').slice(0, 12)}-LICENSE`,
      `references/upstream-notices/${sha256('NOTICE').slice(0, 12)}-NOTICE`].sort());
    const ledger = await fake.ledger();
    expect(ledger.map(row => row.url)).toEqual(source.recordings.slice(0, 3).map(row => row.url));
    await testInfo.attach('recorded-source-receipt', { body: JSON.stringify({ parent, child, ledger, fixtureHash: fake.fixtureHash }), contentType: 'application/json' });
  } finally { await fake.dispose(); }
});

test('Skills v1.1 harness: scoped clock preserves ref freshness and REST caps until period rollover', async ({ scopeActors, request }, testInfo) => {
  test.setTimeout(420_000); // setup and six generic Temporal boundaries <=60s each
  const actor = scopeActors.ownerB;
  const fake = new SkillsFixtures();
  const name = `e2e-skills-clock-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const repo = `synthetic/${name}`;
  const root = `---\nname: ${name}\ndescription: Use when qualifying scoped cache freshness and REST budgets with synthetic files.\n---\n# Synthetic instructions\n`;
  const files = { 'SKILL.md': root, LICENSE: 'MIT License\nPermission is hereby granted, free of charge, to any person obtaining a copy.\n' };
  const source = githubRepository(repo, [{ sha: 'a'.repeat(40), files }, { sha: 'b'.repeat(40), files }]);
  await fake.register([], source.recordings);
  type Preview = { id: string; status: string; source: { commit_sha: string }; error: { code: string } | null };
  const start = async () => {
    const response = await request.post(`${E2E.apiUrl}${SKILLS}github/previews/`, {
      headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() },
      data: { url: `https://github.com/${repo}`, path: null, ref: 'main' },
    });
    expect(response.status(), await response.text()).toBe(202);
    const id = (await response.json()).result.id;
    const read = async () => (await actor.api.get<Envelope<Preview>>(`${SKILLS}github/previews/${id}/`)).result;
    await expect.poll(async () => (await read()).status, POLL.ASYNC_JOB).toMatch(/^(completed|failed)$/);
    return read();
  };
  try {
    const initial = await start();
    expect(initial).toMatchObject({ status: 'completed', source: { commit_sha: 'a'.repeat(40) } });
    await fake.replaceRecording(source.ref('b'.repeat(40)));
    await fake.advanceGitHubTime(59);
    const cached = await start();
    expect(cached).toMatchObject({ status: 'completed', source: { commit_sha: 'a'.repeat(40) } });
    expect((await fake.ledger()).map(row => row.url)).toEqual(source.recordings.slice(0, 3).map(row => row.url));
    await fake.advanceGitHubTime(1);
    const limited = await start(); // metadata is call 3; the conditional ref would be call 4
    expect(limited).toMatchObject({ status: 'failed', error: { code: 'github_rate_limited' } });
    expect((await fake.ledger()).map(row => row.url)).toEqual([...source.recordings.slice(0, 3).map(row => row.url), source.recordings[0].url]);
    await fake.advanceGitHubTime(3540);
    const refreshed = await start();
    expect(refreshed).toMatchObject({ status: 'completed', source: { commit_sha: 'b'.repeat(40) } });
    const ledger = await fake.ledger();
    expect(ledger.map(row => row.url)).toEqual([...source.recordings.slice(0, 3).map(row => row.url), source.recordings[0].url,
      source.recordings[0].url, source.recordings[1].url, source.recordings[3].url]);
    expect(ledger.filter(row => row.url === source.recordings[1].url).map(row => row.if_none_match)).toEqual([null, source.recordings[1].headers.ETag]);
    expect((await start()).source.commit_sha).toBe('b'.repeat(40));
    expect(await fake.ledger()).toEqual(ledger);
    await testInfo.attach('scoped-clock-receipts', { body: JSON.stringify({ initial, cached, limited, refreshed, ledger, fixtureHash: fake.fixtureHash }), contentType: 'application/json' });
  } finally { await fake.dispose(); }
});
