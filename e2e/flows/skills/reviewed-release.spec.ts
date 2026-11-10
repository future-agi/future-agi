import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';
import { SkillsFixtures, toolRule, answerRule } from '../../lib/skills-fixtures';
import { sha256, skillDigest } from '../../lib/skills-consumption';
import type { SkillFile } from '../../lib/skills-consumption';

// routes/paths.js; model_hub/urls_skills_hub{,_v11}.py.
const SKILLS = '/model-hub/skills/';
const UI = '/dashboard/skills';
const UI_READY = 60_000;
type Envelope<T> = { result: T };
type Draft = { revision: number; base_version: number; files: SkillFile[] };
type Version = { id: string; number: number; digest: string };
type Run = { id: string; status: string; visibility: string; candidate: { draft_revision: number; digest: string };
  production: { version_id: string; digest: string }; decision: { state: string }; counts: { planned: number; errors: number } };
type SavedCase = { id: string; title: string; prompt: string; expected_trigger: boolean; reviewed: boolean; reviewed_by: { id: string } };
type Attempt = { id: string; case_id: string; variant: string; answer: string; target_loaded: boolean; root_fully_delivered: boolean;
  routing_verdict: string; answer_verdict: string; delivery_verdict: string; receipts: { path: string; sha256: string; offset: number; next_offset: number | null; eof: boolean; total_chars: number }[] };

async function select(page: Page, label: string | RegExp, option: string) {
  // MUI Select's accessible name includes its currently selected option.
  await page.getByRole('combobox', { name: label }).click({ timeout: UI_READY });
  await page.getByRole('option', { name: option, exact: true }).click({ timeout: UI_READY });
}

async function selectSourceIfAvailable(page: Page) {
  const monaco = page.getByRole('textbox', { name: /Editor content/ });
  const source = page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true });
  await expect(monaco.or(source).first()).toBeVisible({ timeout: UI_READY });
  if (await source.isVisible()) await source.click();
}

async function editText(page: Page, content: string) {
  await selectSourceIfAvailable(page);
  // SkillFileEditor -> Monaco's accessible textbox; real clipboard paste, no app-state injection.
  const input = page.getByRole('textbox', { name: /Editor content/ });
  await expect(input).toBeVisible({ timeout: UI_READY });
  await input.focus();
  await input.press('Control+a');
  await page.evaluate(value => navigator.clipboard.writeText(value), content);
  await input.press('Control+v');
}

test('SKILLS-E2E-002: review a policy repair and deliberately release its exact evidence', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-002', area: 'skills',
    userGoal: 'A contributor verifies a policy repair against reviewed examples, publishes exactly the shared evidence-backed candidate without moving production, and an administrator deliberately promotes or rolls it back',
    steps: [
      'open a member draft based on a synthetic production policy and author reviewed boundary, exclusion and near-miss examples',
      'review model, inventory and spend; run the first comparison and inspect its failing answer',
      'repair policy bytes in the editor, await autosave and compare the saved candidate against pinned production again',
      'inspect selection, root/reference delivery and answer outcomes separately, then explicitly share the private evidence',
      'publish the tested revision and digest with its evidence, leaving protected production unchanged',
      'verify member promotion denial, then have an administrator review and promote the exact candidate',
      'inspect history, execute an ABA pointer sequence, reject stale aggregate release state and explicitly roll back',
    ], backendChecks: [
      'saved cases and review attribution exactly match the authored case IDs in the actor workspace',
      'run snapshot candidate revision and digest equal the acknowledged saved draft and production is pinned',
      'the complete planned case-by-arm matrix, answers, grades and root/reference receipts match the programmed transport ledger, preserving exclusion and near-miss behavior',
      'private evidence is absent from non-owner reads and counts until explicit sharing',
      'publication creates the exact tested immutable candidate and evidence association while production remains on baseline, and forbidden promotion changes nothing',
      'authorized promotion records the intended version, actor, evidence and release revision',
      'an ABA sequence rejects stale release_revision even when expected_version matches, without another event',
      'rollback moves only the pointer to the exact prior immutable version and retained history remains available',
    ] }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(1_620_000); // approved: 24 UI_READY stages + 2 ASYNC_JOB waits + 60s setup.
  const owner = scopeActors.ownerA;
  const member = scopeActors.member;
  const name = `e2e-skills2-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prompts = [
    `${name}: Is an unopened purchase delivered exactly thirty days ago eligible? Return eligible as JSON.`,
    `${name}: Is an opened purchase from yesterday eligible? Return eligible as JSON.`,
    `${name}: Rewrite the greeting hello politely without adjudicating a purchase. Return kind rewrite as JSON.`,
  ];
  const titles = [`${name} boundary`, `${name} excluded`, `${name} near-miss`];
  const oldPolicy = 'Synthetic policy only. Unopened purchases qualify through day 29 inclusive. Opened purchases are excluded.\n';
  const newPolicy = 'Synthetic policy only. Unopened purchases qualify through day 30 inclusive. Opened purchases are excluded.\n';
  const original = [
    { path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Use when determining refund eligibility from an approved purchase policy, not when rewriting greetings.\n---\nRead [policy](references/policy.md) before deciding.\n` },
    { path: 'references/policy.md', content: oldPolicy },
  ];
  const repaired = [original[0], { path: original[1].path, content: newPolicy }];
  const fake = new SkillsFixtures();
  await fake.register([
    ...prompts.slice(0, 2).flatMap(prompt => [
      toolRule(prompt, 0, 'load_skill', { skill_id: '$skill_id' }),
      toolRule(prompt, 1, 'read_skill_file', { skill_id: '$skill_id', path: 'references/policy.md', offset: 0, max_bytes: 24000 }),
    ]),
    answerRule(prompts[0], 2, { eligible: false }, 'day 29'),
    answerRule(prompts[0], 2, { eligible: true }, 'day 30'),
    answerRule(prompts[1], 2, { eligible: false }),
    answerRule(prompts[2], 0, { kind: 'rewrite' }),
  ]);
  const context = await scopeActors.openContext(browser, member);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = await context.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });
  page.setDefaultTimeout(UI_READY);
  const read = async <T>(path: string) => (await member.api.get<Envelope<T>>(path)).result;
  let id = '';
  let saved: Draft;
  let published: Version;
  let tested: Run;
  let suite: { cases: SavedCase[]; revision: number };
  try {
    await fake.connect(owner.api);
    // PushRequest and DraftSaveRequest: prerequisites only. The repair below uses the editor.
    const seed = (await owner.api.post<Envelope<{ skill: { id: string }; version: Version }>>(`${SKILLS}publish/`, {
      files: original, labels: ['production'], expected_release_revision: 0,
    })).result;
    id = seed.skill.id;
    saved = (await member.api.put<Envelope<Draft>>(`${SKILLS}${id}/draft/`, { revision: 0, base_version: 1, files: original })).result;
    await testInfo.attach('reviewed-release-seed', { body: JSON.stringify({ id, baseline: seed.version, draftRevision: saved.revision,
      ownerId: owner.userId, memberId: member.userId, workspaceId: member.workspaceId, fixtureHash: fake.fixtureHash }), contentType: 'application/json' });
    await test.step('Open the member draft and its Tests tab', async () => {
      await page.goto(`${UI}/${id}?tab=tests`);
      await expect(page.getByRole('button', { name: 'Add first example', exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    for (let index = 0; index < 3; index++) {
      await test.step(`Author and review ${titles[index]}`, async () => {
        await page.getByRole('button', { name: index ? 'Add example' : 'Add first example', exact: true }).click();
        const dialog = page.getByRole('dialog');
        await dialog.getByRole('textbox', { name: 'Title', exact: true }).fill(titles[index]);
        await dialog.getByRole('textbox', { name: 'Request', exact: true }).fill(prompts[index]);
        await select(page, 'Use this skill?', index === 2 ? 'No' : 'Yes');
        await dialog.getByRole('button', { name: 'Add success check', exact: true }).click();
        await select(page, 'Check 1', 'JSON value equals');
        await dialog.getByRole('textbox', { name: 'JSON pointer', exact: true }).fill(index === 2 ? '/kind' : '/eligible');
        await dialog.getByRole('textbox', { name: 'Expected JSON value', exact: true }).fill(index === 0 ? 'true' : index === 1 ? 'false' : '"rewrite"');
        if (index < 2) {
          await dialog.getByRole('button', { name: 'Add success check', exact: true }).click();
          await select(page, 'Check 2', 'File delivered');
          await dialog.getByRole('textbox', { name: 'Target file path', exact: true }).fill('references/policy.md');
        }
        await dialog.getByRole('checkbox', { name: 'I reviewed the request, supplied facts and expected behavior', exact: true }).check();
        await dialog.getByRole('button', { name: 'Save example', exact: true }).click();
        await expect(dialog).not.toBeVisible({ timeout: UI_READY });
      }, { timeout: UI_READY });
    }
    suite = await read(`${SKILLS}${id}/tests/`);
    expect(suite.cases.map(row => [row.title, row.prompt, row.expected_trigger, row.reviewed, row.reviewed_by.id]))
      .toEqual(titles.map((title, index) => [title, prompts[index], index !== 2, true, member.userId]));
    expect(new Set(suite.cases.map(row => row.id)).size).toBe(3);
    const persisted = await probe.pg<{ workspace_id: string; cases: SavedCase[] }>(
      'SELECT workspace_id, cases FROM model_hub_hubskilltestsuite WHERE skill_id = $1', [id]);
    expect(persisted.map(row => [row.workspace_id, row.cases.map(item => item.id)])).toEqual([[member.workspaceId, suite.cases.map(row => row.id)]]);
    await testInfo.attach('authored-cases', { body: JSON.stringify(suite), contentType: 'application/json' });

    const runComparison = async (label: string) => {
      let runId = '';
      await test.step(`Review ${label} inventory, model and spend`, async () => {
        await select(page, 'Task model', 'gpt-4o-mini');
        await page.getByRole('textbox', { name: 'Estimated-cost stop (USD)', exact: true }).fill('1');
        await page.getByRole('button', { name: 'Preview work and spend', exact: true }).click();
        await expect(page.getByText('3 examples × 2 arms × 1 repeats = 6 attempts', { exact: true })).toBeVisible({ timeout: UI_READY });
        await page.getByRole('checkbox', { name: 'I know of no bundled answer keys or evaluation material', exact: true }).check();
        await page.getByRole('checkbox', { name: 'I reviewed the entire inventory and outbound inputs', exact: true }).check();
        await page.getByRole('checkbox', { name: 'Send the reviewed files and example inputs to these model providers within this spend stop', exact: true }).check();
        const response = page.waitForResponse(res => res.url().endsWith(`${SKILLS}${id}/test-runs/`) && res.request().method() === 'POST', { timeout: UI_READY });
        await page.getByRole('button', { name: 'Run quick check', exact: true }).click();
        const accepted = await response;
        expect(accepted.status(), await accepted.text()).toBe(202);
        runId = (await accepted.json()).result.id;
        await testInfo.attach(`${label}-run`, { body: JSON.stringify({ runId, request: accepted.request().postDataJSON() }), contentType: 'application/json' });
      }, { timeout: UI_READY });
      await expect.poll(async () => (await read<Run>(`${SKILLS}${id}/test-runs/${runId}/`)).status, POLL.ASYNC_JOB).toBe('completed');
      const run = await read<Run>(`${SKILLS}${id}/test-runs/${runId}/`);
      expect(run.candidate).toMatchObject({ draft_revision: saved.revision, digest: skillDigest(saved.files) });
      expect(run.production).toMatchObject({ version_id: seed.version.id, digest: seed.version.digest });
      const stored = await probe.pg<{ candidate_digest: string }>('SELECT candidate_digest FROM model_hub_hubskilltestrun WHERE job_id = $1', [runId]);
      expect(stored).toEqual([{ candidate_digest: skillDigest(saved.files) }]);
      return run;
    };
    const first = await runComparison('initial');
    expect(first.decision.state).toBe('needs_changes');
    await test.step('Inspect the failing expectation and actual answer', async () => {
      await page.getByRole('row', { name: `Inspect ${titles[0]}`, exact: true }).click();
      await expect(page.getByText('{"eligible":false}', { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText('Expected, actual and evidence', { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      const machineResults = () => probe.pg('SELECT id, check_results, answer_verdict FROM model_hub_hubskilltestattempt WHERE run_id = $1 ORDER BY id', [first.id]);
      const before = await machineResults();
      await page.getByRole('button', { name: 'Review this check', exact: true }).first().click();
      await select(page, 'Your assessment', 'Agree');
      await page.getByRole('textbox', { name: 'Cited clause or answer excerpt', exact: true }).fill('{"eligible":false}');
      await page.getByRole('textbox', { name: 'Review explanation', exact: true }).fill('The retained answer confirms the failing policy boundary.');
      const annotationResponse = page.waitForResponse(res => res.url().endsWith(`/evidence/skills_text/${first.id}/annotations/`) && res.request().method() === 'POST');
      await page.getByRole('button', { name: 'Append review', exact: true }).click();
      const annotated = await annotationResponse;
      expect(annotated.status(), await annotated.text()).toBe(201);
      await expect(page.getByText(/Annotation recorded. Machine grade unchanged/)).toBeVisible();
      const submitted = annotated.request().postDataJSON();
      expect(submitted).toMatchObject({ annotation_revision: 0, verdict: 'agree', cited_text: '{"eligible":false}' });
      expect(await probe.pg('SELECT actor_id::text, check_id::text, attempt_id::text, revision, verdict, cited_text FROM model_hub_hubskillevidenceannotation WHERE source_id = $1', [first.id]))
        .toEqual([{ actor_id: member.userId, check_id: submitted.check_id, attempt_id: submitted.attempt_id, revision: '1', verdict: 'agree', cited_text: '{"eligible":false}' }]);
      expect(await machineResults()).toEqual(before);
      await testInfo.attach('human-review-preserves-machine-grade', { body: await page.screenshot(), contentType: 'image/png' });
      await page.getByRole('button', { name: 'Close attempt evidence', exact: true }).click();
      await testInfo.attach('initial-failure', { body: await page.screenshot(), contentType: 'image/png' });
    }, { timeout: UI_READY });
    await test.step('Repair the policy in Monaco and await the acknowledged save', async () => {
      await page.goto(`${UI}/${id}/edit`);
      await page.getByRole('treeitem', { name: 'policy.md', exact: true }).click();
      await editText(page, newPolicy);
      await expect.poll(async () => (await read<Draft>(`${SKILLS}${id}/draft/`)).files, { timeout: UI_READY }).toEqual(repaired);
      saved = await read(`${SKILLS}${id}/draft/`);
      await expect(page.getByText('Saved', { exact: true })).toBeVisible({ timeout: UI_READY });
      await page.goto(`${UI}/${id}?tab=tests`);
    }, { timeout: UI_READY });
    tested = await runComparison('repaired');
    expect(tested.decision.state).toBe('works_on_examples');
    expect(tested.counts).toMatchObject({ planned: 6, errors: 0 });
    const attempts = await probe.pg<Attempt>(
      'SELECT id, case_id, variant, answer, target_loaded, root_fully_delivered, routing_verdict, answer_verdict, delivery_verdict, receipts FROM model_hub_hubskilltestattempt WHERE run_id = $1 ORDER BY case_id, variant', [tested.id]);
    const expectedMatrix = suite.cases.flatMap((row, index) => ['candidate', 'production'].map(variant => ({
      case_id: row.id, variant, answer: index === 2 ? '{"kind":"rewrite"}' : JSON.stringify({ eligible: index === 0 && variant === 'candidate' }),
      target_loaded: index !== 2, answer_verdict: index === 0 && variant === 'production' ? 'fail' : 'pass', routing_verdict: 'pass',
    }))).sort((a, b) => a.case_id.localeCompare(b.case_id) || a.variant.localeCompare(b.variant));
    expect(attempts.map(({ case_id, variant, answer, target_loaded, answer_verdict, routing_verdict }) =>
      ({ case_id, variant, answer, target_loaded, answer_verdict, routing_verdict }))).toEqual(expectedMatrix);
    for (const attempt of attempts) {
      const index = suite.cases.findIndex(row => row.id === attempt.case_id);
      const files = attempt.variant === 'candidate' ? repaired : original;
      expect(attempt.receipts.map(row => [row.path, row.sha256, row.offset, row.next_offset, row.eof, row.total_chars]))
        .toEqual(index === 2 ? [] : files.map(file => [file.path, sha256(file.content), 0, null, true, Array.from(file.content).length]));
      if (index !== 2) expect([attempt.root_fully_delivered, attempt.delivery_verdict]).toEqual([true, 'pass']);
    }
    const results = await read<{ results: { case_id: string; variants: { variant: string; attempts: { answer_verdict: string }[] }[] }[] }>(`${SKILLS}${id}/test-runs/${tested.id}/results/`);
    expect(results.results.flatMap(row => row.variants.map(variant => [row.case_id, variant.variant, variant.attempts.map(attempt => attempt.answer_verdict)])).sort())
      .toEqual(expectedMatrix.map(row => [row.case_id, row.variant, [row.answer_verdict]]).sort());
    const ledger = await fake.ledger();
    expect(ledger.filter(row => row.kind === 'model')).toHaveLength(28);
    const calls = await probe.pg<{ provider_request_id: string }>('SELECT provider_request_id FROM model_hub_hubskillmodelcall WHERE job_id IN ($1, $2)', [first.id, tested.id]);
    expect(calls.map(call => call.provider_request_id).sort())
      .toEqual(ledger.map(row => `chatcmpl-${row.request_sha256.slice(0, 24)}`).sort());
    expect(ledger.filter(row => row.request === prompts[2]).map(row => row.turn)).toEqual([0, 0, 0, 0]);
    await testInfo.attach('verified-attempt-matrix', { body: JSON.stringify({ attempts, ledger }), contentType: 'application/json' });
    await test.step('Inspect separate delivery and answer results', async () => {
      await page.getByRole('row', { name: `Inspect ${titles[0]}`, exact: true }).click();
      await expect(page.getByText('{"eligible":true}', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText('Delivery receipts', { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await page.getByRole('button', { name: 'Close attempt evidence', exact: true }).click();
    }, { timeout: UI_READY });
    const visible = async () => (await owner.api.get<Envelope<{ count: number; results: { id: string }[] }>>(`${SKILLS}${id}/test-runs/`)).result;
    expect(await visible()).toMatchObject({ count: 0, results: [] });
    expect((await scopeActors.send(owner, 'GET', `${SKILLS}${id}/test-runs/${tested.id}/`)).status).toBe(404);
    await test.step('Explicitly share the frozen evidence', async () => {
      await page.getByRole('button', { name: 'Share evidence', exact: true }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'Share with workspace', exact: true }).click();
      await expect(page.getByText('Shared with workspace', { exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    expect((await visible()).results.map(row => row.id)).toEqual([tested.id]);
    expect((await visible()).count).toBe(1);
    await test.step('Publish the exact tested revision with shared evidence', async () => {
      await page.goto(`${UI}/${id}/edit`);
      await page.getByRole('button', { name: 'Publish', exact: true }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog.getByText('Publish skill', { exact: true })).toBeVisible({ timeout: UI_READY });
      // The editor flushes before opening publication. A same-byte save may
      // advance revision; it must not change the tested candidate's content.
      const publicationDraft = await read<Draft>(`${SKILLS}${id}/draft/`);
      expect(publicationDraft.files).toEqual(repaired);
      expect(skillDigest(publicationDraft.files)).toBe(tested.candidate.digest);
      await dialog.getByRole('button', { name: 'Attach this evidence', exact: true }).click();
      const response = page.waitForResponse(res => res.url().endsWith(`${SKILLS}${id}/draft/publish/`) && res.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Publish', exact: true }).click();
      const result = await response;
      expect(result.status(), await result.text()).toBe(201);
      expect(result.request().postDataJSON()).toMatchObject({ revision: publicationDraft.revision, expected_digest: skillDigest(repaired), evidence_run_id: tested.id, labels: [] });
      published = (await result.json()).result.version;
      expect(published).toMatchObject({ number: 2, digest: tested.candidate.digest });
      await expect(dialog).not.toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    const labels = async () => (await owner.api.get<Envelope<{ name: string; version: number }[]>>(`${SKILLS}${id}/labels/`)).result.map(row => [row.name, row.version]);
    const release = async () => (await owner.api.get<Envelope<{ release_revision: number }>>(`${SKILLS}${id}/`)).result.release_revision;
    const events = async () => await probe.pg<{ from_version: number | null; to_version: number; actor_id: string; release_revision: string }>(
      'SELECT from_version, to_version, actor_id, release_revision::text FROM model_hub_hubskilllabelevent WHERE skill_id = $1 ORDER BY created_at', [id]);
    expect(await labels()).toEqual([['production', 1]]);
    const beforeDenied = await events();
    const denied = await scopeActors.send<{ code: string }>(member, 'PUT', `${SKILLS}${id}/labels/production/`, {
      version: 2, expected_version: 1, expected_release_revision: await release(), expected_digest: published!.digest, evidence_run_id: tested.id,
    });
    expect([denied.status, denied.body.code]).toEqual([403, 'protected_label_forbidden']);
    expect(await events()).toEqual(beforeDenied);
    expect(await labels()).toEqual([['production', 1]]);
    const evidenceRows = async () => await probe.pg<{ version_id: string; run_id: string; attached_by_id: string; label_event_id: string | null }>(
      'SELECT version_id, run_id, attached_by_id, label_event_id FROM model_hub_hubskilltestevidence WHERE skill_id = $1 ORDER BY created_at', [id]);
    expect(await evidenceRows()).toEqual([{ version_id: published!.id, run_id: tested.id, attached_by_id: member.userId, label_event_id: null }]);

    const adminContext = await scopeActors.openContext(browser, owner);
    const admin = await adminContext.newPage();
    admin.setDefaultTimeout(UI_READY);
    await admin.setViewportSize({ width: 1440, height: 900 });
    const moveInUi = async (target: number, attach: boolean) => {
      await admin.goto(`${UI}/${id}?tab=versions`);
      await admin.getByRole('button', { name: 'Move production', exact: true }).click();
      await select(admin, /^Version(?:\s|$)/, `v${target}`);
      const dialog = admin.getByRole('dialog');
      if (attach) await dialog.getByRole('button', { name: 'Attach this evidence', exact: true }).click();
      await dialog.getByRole('textbox', { name: 'Note (optional)', exact: true }).fill(`${name}: reviewed ${target === 2 ? 'promotion' : 'rollback'}`);
      await dialog.getByRole('button', { name: `Move production from v${target === 2 ? 1 : 2} to v${target}`, exact: true }).click();
      await expect(dialog).not.toBeVisible({ timeout: UI_READY });
    };
    await test.step('Administrator reviews and promotes the exact candidate', async () => { await moveInUi(2, true); }, { timeout: UI_READY });
    expect(await labels()).toEqual([['production', 2]]);
    expect((await events()).map(row => [row.from_version, row.to_version, row.actor_id, row.release_revision]))
      .toEqual([[null, 1, owner.userId, '1'], [1, 2, owner.userId, '2']]);
    const attached = await evidenceRows();
    expect(attached.map(row => [row.version_id, row.run_id, row.attached_by_id, row.label_event_id === null]))
      .toEqual([[published!.id, tested.id, member.userId, true], [published!.id, tested.id, owner.userId, false]]);
    await test.step('Inspect label history before the ABA probe', async () => {
      await admin.getByRole('button', { name: 'History for production', exact: true }).click();
      await expect(admin.getByText('production: v1 → v2', { exact: true })).toBeVisible({ timeout: UI_READY });
      await admin.getByRole('button', { name: 'Close label history', exact: true }).click();
    }, { timeout: UI_READY });
    // LabelPutRequest: deliberate negative API race; user-facing promotion/rollback stay UI-driven.
    const staleRevision = await release();
    await owner.api.put(`${SKILLS}${id}/labels/production/`, { version: 1, expected_version: 2, expected_release_revision: staleRevision });
    await owner.api.put(`${SKILLS}${id}/labels/production/`, { version: 2, expected_version: 1, expected_release_revision: await release() });
    const beforeAba = await events();
    const stale = await scopeActors.send<{ code: string }>(owner, 'PUT', `${SKILLS}${id}/labels/production/`, {
      version: 1, expected_version: 2, expected_release_revision: staleRevision,
    });
    expect([stale.status, stale.body.code]).toEqual([409, 'release_conflict']);
    expect(await events()).toEqual(beforeAba);
    expect(await release()).toBe(4);
    await test.step('Explicitly roll back without rewriting either immutable release', async () => { await moveInUi(1, false); }, { timeout: UI_READY });
    expect(await labels()).toEqual([['production', 1]]);
    expect((await events()).map(row => [row.from_version, row.to_version, row.release_revision]))
      .toEqual([[null, 1, '1'], [1, 2, '2'], [2, 1, '3'], [1, 2, '4'], [2, 1, '5']]);
    const versions = await probe.pg<{ id: string; digest: string }>('SELECT id, digest FROM model_hub_hubskillversion WHERE skill_id = $1 ORDER BY number', [id]);
    expect(versions).toEqual([{ id: seed.version.id, digest: skillDigest(original) }, { id: published!.id, digest: skillDigest(repaired) }]);
    for (const [versionId, expectedFiles] of [[seed.version.id, original], [published!.id, repaired]] as const) {
      const storedFiles = await probe.pg<{ path: string; content: string }>(
        'SELECT f.path, b.content FROM model_hub_hubskillfile f JOIN model_hub_hubskillblob b ON b.id = f.blob_id WHERE f.version_id = $1 ORDER BY f.path', [versionId]);
      // Database collation need not match manifest code-point ordering.
      expect(storedFiles.sort((a, b) => a.path.localeCompare(b.path)))
        .toEqual([...expectedFiles].sort((a, b) => a.path.localeCompare(b.path)));
    }
    expect(await evidenceRows()).toEqual(attached);
    await test.step('Retain inspectable rollback history in light and dark', async () => {
      for (const themeMode of ['light', 'dark'] as const) {
        await admin.evaluate(mode => { const settings = JSON.parse(localStorage.getItem('settings') || '{}'); localStorage.setItem('settings', JSON.stringify({ ...settings, themeMode: mode })); }, themeMode);
        await admin.reload();
        await admin.getByRole('button', { name: 'History for production', exact: true }).click();
        await expect(admin.getByText('production: v2 → v1', { exact: true })).toHaveCount(2, { timeout: UI_READY });
        await testInfo.attach(`reviewed-release-${themeMode}`, { body: await admin.screenshot(), contentType: 'image/png' });
        await admin.getByRole('button', { name: 'Close label history', exact: true }).click();
      }
    }, { timeout: 2 * UI_READY });
    await testInfo.attach('release-events', { body: JSON.stringify({ id, testedRunId: tested.id, published: published!, events: await events(), evidence: attached }), contentType: 'application/json' });
    await adminContext.close();
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) await testInfo.attach('reviewed-release-failure', { body: await page.screenshot(), contentType: 'image/png' });
    await context.close();
    await fake.dispose();
  }
});
