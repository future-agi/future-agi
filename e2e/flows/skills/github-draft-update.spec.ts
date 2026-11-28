import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';
import { SkillsFixtures, githubRepository } from '../../lib/skills-fixtures';
import { sha256, skillDigest } from '../../lib/skills-consumption';
import type { SkillFile } from '../../lib/skills-consumption';

// model_hub/urls_skills_hub{,_v11}.py and frontend/src/routes/paths.js.
const SKILLS = '/model-hub/skills/';
const UI = '/dashboard/skills';
const UI_READY = 60_000;
type Envelope<T> = { result: T };
type Draft = { revision: number; files: SkillFile[]; source: { commit_sha: string; upstream_digest: string } };
type Preview = { id: string; status: string; outcome: string; roots: string[]; local_diverged: boolean;
  source: { commit_sha: string; ref: string; ref_kind: string }; source_revision: number;
  expected_latest_version: number; expected_latest_digest: string;
  bundle: { digest: string; files: { path: string; sha256: string }[] };
  license: { files: { path: string; text: string; sha256: string }[] };
  diff: { files: { path: string; status: string; from_text?: string; to_text?: string }[] } };
type Source = { imported_sha: string; upstream_digest: string; revision: number; associated_version: number };

async function selectSourceIfAvailable(page: Page) {
  const monaco = page.getByRole('textbox', { name: /Editor content/ });
  const source = page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true });
  await expect(monaco.or(source).first()).toBeVisible({ timeout: UI_READY });
  if (await source.isVisible()) await source.click();
}

async function editText(page: Page, text: string) {
  await selectSourceIfAvailable(page);
  const input = page.getByRole('textbox', { name: /Editor content/ }); // SkillFileEditor / Monaco
  await expect(input).toBeVisible({ timeout: UI_READY });
  await input.focus();
  await input.press('Control+a');
  await page.evaluate(value => navigator.clipboard.writeText(value), text);
  await input.press('Control+v');
}

test('SKILLS-E2E-003: review and stage an exact GitHub update without overwriting local work', {
  tag: ['@flow'], annotation: flowAnnotation({ id: 'SKILLS-E2E-003', area: 'skills',
    userGoal: 'A maintainer reviews a public source and stages its exact update without overwriting a personal draft, silently merging local customizations or changing production',
    steps: [
      'preview a synthetic multi-root repository and advance its branch before choosing a root at the original commit',
      'review the exact files and inherited LICENSE/NOTICE, confirm rights and import an unpublished draft',
      'publish explicitly, then author and publish a local customization with honest source provenance',
      'create a personal draft, check upstream updates and inspect the replacement diff and divergence',
      'verify that staging is blocked and preserves the personal draft, then explicitly discard it and refresh draft status without replacing the reviewed preview',
      'review replacement consequences, stage the retained update and inspect unchanged releases and the replay receipt',
    ], backendChecks: [
      'root continuation keeps the original immutable SHA and tracked ref despite branch movement',
      'the staged initial bundle includes the exact reviewed paths, license and NOTICE bytes and provenance without publication',
      'publication alone adopts the source binding and local edits retain honest source and local_modifications metadata',
      'update diff compares the retained upstream basis and current local content and exposes divergence',
      'existing-draft rejection preserves exact files and revision, and explicit discard enables only the reviewed replacement',
      'staging leaves immutable releases and labels unchanged and replay returns the same destination and draft revision without another save',
    ] }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(1_200_000); // approved: 16 UI_READY stages + three ASYNC_JOB waits +60s setup.
  const actor = scopeActors.ownerB;
  const name = `e2e-skills3-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const repo = `synthetic/${name}`;
  const shaA = 'a'.repeat(40);
  const shaB = 'b'.repeat(40);
  const root = `---\nname: ${name}\ndescription: Use when reviewing a synthetic policy source with explicit rights and retained provenance.\n---\nRead [policy](references/policy.md).\n`;
  const license = 'MIT License\nCopyright synthetic fixture authors. Permission is hereby granted, free of charge, to any person obtaining a copy.\n';
  const notice = 'Synthetic notice: keep this attribution with the reviewed policy.\n';
  const oldPolicy = 'Synthetic upstream policy: request human review at the boundary.\n';
  const newPolicy = 'Synthetic upstream policy: escalate boundary requests to the policy owner.\n';
  const archive = (policy: string) => ({ 'policy/SKILL.md': root, 'policy/references/policy.md': policy,
    'other/SKILL.md': root.replace(`name: ${name}\n`, `name: ${name}-other\n`), LICENSE: license, NOTICE: notice });
  const sourceFixture = githubRepository(repo, [{ sha: shaA, files: archive(oldPolicy) }, { sha: shaB, files: archive(newPolicy) }]);
  // repository_zip.py inherited-notice destinations are pinned from the original source path.
  const files = [
    { path: 'SKILL.md', content: root }, { path: 'references/policy.md', content: oldPolicy },
    { path: `references/upstream-notices/${sha256('LICENSE').slice(0, 12)}-LICENSE`, content: license },
    { path: `references/upstream-notices/${sha256('NOTICE').slice(0, 12)}-NOTICE`, content: notice },
  ].sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  const upstream = files.map(file => file.path === 'references/policy.md' ? { ...file, content: newPolicy } : file);
  const fake = new SkillsFixtures();
  await fake.register([], sourceFixture.recordings);
  const context = await scopeActors.openContext(browser, actor);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  await page.setViewportSize({ width: 1440, height: 900 });
  const read = async <T>(path: string) => (await actor.api.get<Envelope<T>>(path)).result;
  const savedRoot = async () => {
    // The editor starts a new local draft after publication; its first autosave
    // legitimately races a draft_not_found read. Other failures must not be hidden.
    const response = await scopeActors.send<Envelope<Draft> & { code: string }>(actor, 'GET', `${SKILLS}${id}/draft/`);
    if (response.status === 404 && response.body.code === 'draft_not_found') return null;
    expect(response.status).toBe(200);
    return response.body.result.files.find(file => file.path === 'SKILL.md')?.content;
  };
  const finishPreview = async (id: string) => {
    await expect.poll(async () => (await read<Preview>(`${SKILLS}github/previews/${id}/`)).status, POLL.ASYNC_JOB).toBe('completed');
    return read<Preview>(`${SKILLS}github/previews/${id}/`);
  };
  let parent: Preview;
  let reviewed: Preview;
  let id = '';
  let imported!: Draft;
  let update: Preview;
  let personal: Draft;
  try {
    await testInfo.attach('github-fixture-identities', { body: JSON.stringify({ repo, shaA, shaB, fixtureHash: fake.fixtureHash, files }), contentType: 'application/json' });
    await test.step('Preview the multi-root source through Import', async () => {
      await page.goto(UI);
      await page.getByRole('button', { name: /^(Import|Import a folder or ZIP)$/ }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('tab', { name: 'GitHub', exact: true }).click();
      await dialog.getByRole('textbox', { name: 'Public repository URL', exact: true }).fill(`https://github.com/${repo}`);
      await dialog.getByRole('textbox', { name: 'Branch, tag or full commit (optional)', exact: true }).fill('main');
      const response = page.waitForResponse(res => res.url().endsWith(`${SKILLS}github/previews/`) && res.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Preview source', exact: true }).click();
      const ack = await response;
      expect(ack.status(), await ack.text()).toBe(202);
      parent = (await ack.json()).result;
    }, { timeout: UI_READY });
    parent = await finishPreview(parent!.id);
    expect(parent.outcome).toBe('needs_path');
    expect([...parent.roots].sort()).toEqual(['other', 'policy']);
    await fake.replaceRecording(sourceFixture.ref(shaB));
    // Model a later check without sleeps or quota overrides. This expires the
    // real 60-second policy and opens the next unchanged REST budget period.
    // Continuation must still retain A even when re-resolving main would yield B.
    const policyTime = await fake.advanceGitHubTime(3600);
    await testInfo.attach('github-policy-clock', { body: JSON.stringify({ advanceSeconds: 3600, now: policyTime, retainedPreview: parent.id }), contentType: 'application/json' });
    await test.step('Choose the root at the original immutable commit', async () => {
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('combobox', { name: 'Skill folder', exact: true }).click();
      await page.getByRole('option', { name: 'policy', exact: true }).click();
      const response = page.waitForResponse(res => res.url().endsWith(`${SKILLS}github/previews/`) && res.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Preview this folder at the same commit', exact: true }).click();
      const ack = await response;
      expect(ack.request().postDataJSON()).toEqual({ parent_preview_id: parent.id, path: 'policy' });
      expect(ack.status()).toBe(202);
      reviewed = (await ack.json()).result;
    }, { timeout: UI_READY });
    reviewed = await finishPreview(reviewed!.id);
    expect(reviewed.source).toMatchObject({ commit_sha: shaA, ref: 'main', ref_kind: 'branch' });
    expect((await fake.ledger()).map(row => row.url)).toEqual(sourceFixture.recordings.slice(0, 3).map(row => row.url));
    await test.step('Review files, inherited notices and rights, then import as draft', async () => {
      const dialog = page.getByRole('dialog');
      await expect(dialog.getByRole('table', { name: 'Reviewed GitHub files' }).locator('tbody tr td:first-child'))
        .toHaveText(files.map(file => file.path), { timeout: UI_READY });
      await dialog.getByText('LICENSE', { exact: true }).click();
      await expect(dialog.getByText(license.trim(), { exact: true })).toBeVisible({ timeout: UI_READY });
      await dialog.getByText('NOTICE', { exact: true }).click();
      await expect(dialog.getByText(notice.trim(), { exact: true })).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('checkbox', { name: 'I confirm that I have the rights to import these files.', exact: true }).check();
      await dialog.getByRole('textbox', { name: /^Rights-review note/ }).fill('Synthetic fixture authored for this regression; retain the notices.');
      const response = page.waitForResponse(res => res.url().endsWith(`${SKILLS}github/import/`) && res.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Import as draft', exact: true }).click();
      const accepted = await response;
      expect(accepted.status(), await accepted.text()).toBe(201);
      const result = (await accepted.json()).result;
      expect(result.published).toBe(false);
      id = result.skill.id;
      imported = result.draft;
      await expect(page).toHaveURL(new RegExp(`${UI}/${id}/edit`), { timeout: UI_READY });
    }, { timeout: UI_READY });
    expect([...imported!.files].sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0)).toEqual(files);
    expect(imported!.source).toMatchObject({ commit_sha: shaA, upstream_digest: skillDigest(files) });
    expect(reviewed.bundle.files.map(file => [file.path, file.sha256])).toEqual(files.map(file => [file.path, sha256(file.content)]));
    const releaseRows = async () => await probe.pg<{ id: string; number: number; digest: string; source: { commit_sha: string; local_modifications: boolean } }>(
      'SELECT id, number, digest, source FROM model_hub_hubskillversion WHERE skill_id = $1 ORDER BY number', [id]);
    const labelRows = async () => await probe.pg<{ name: string; version_id: string }>('SELECT name, version_id FROM model_hub_hubskilllabel WHERE skill_id = $1 ORDER BY name', [id]);
    expect(await releaseRows()).toEqual([]);
    expect(await labelRows()).toEqual([]);
    expect((await scopeActors.send(actor, 'GET', `${SKILLS}${id}/source/`)).status).toBe(404);
    await testInfo.attach('imported-source-draft', { body: JSON.stringify({ id, parent: parent.id, preview: reviewed.id, imported }), contentType: 'application/json' });
    const publish = async () => {
      await page.getByRole('button', { name: 'Publish', exact: true }).click();
      const dialog = page.getByRole('dialog');
      const response = page.waitForResponse(res => res.url().endsWith(`${SKILLS}${id}/draft/publish/`) && res.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Publish', exact: true }).click();
      const result = await response;
      expect(result.status(), await result.text()).toBe(201);
      await expect(dialog).not.toBeVisible({ timeout: UI_READY });
      return (await result.json()).result.version;
    };
    await test.step('Publish the reviewed upstream draft explicitly', async () => { await publish(); }, { timeout: UI_READY });
    expect(await read<Source>(`${SKILLS}${id}/source/`)).toMatchObject({ imported_sha: shaA, upstream_digest: skillDigest(files), associated_version: 1 });
    const localRoot = `${root}\nLocal customization: ask the maintainer before interpreting exceptions.\n`;
    await test.step('Author and publish a local customization', async () => {
      await page.goto(`${UI}/${id}/edit`);
      await editText(page, localRoot);
      await expect.poll(savedRoot,
        { timeout: UI_READY }).toBe(localRoot);
      await expect(page.getByText('Saved', { exact: true })).toBeVisible({ timeout: UI_READY });
      await publish();
    }, { timeout: UI_READY });
    expect((await releaseRows()).map(row => [row.number, row.source.commit_sha, row.source.local_modifications]))
      .toEqual([[1, shaA, false], [2, shaA, true]]);
    const retainedSource = await read<Source>(`${SKILLS}${id}/source/`);
    expect(retainedSource.imported_sha).toBe(shaA);
    expect(retainedSource.upstream_digest).toBe(skillDigest(files));
    await test.step('Save another personal draft before checking upstream', async () => {
      await page.goto(`${UI}/${id}/edit`);
      await editText(page, `${localRoot}\nPersonal work in progress; do not replace silently.\n`);
      await expect.poll(savedRoot,
        { timeout: UI_READY }).toBe(`${localRoot}\nPersonal work in progress; do not replace silently.\n`);
      personal = await read(`${SKILLS}${id}/draft/`);
      await expect(page.getByText('Saved', { exact: true })).toBeVisible({ timeout: UI_READY });
      await page.goto(`${UI}/${id}?tab=files`);
    }, { timeout: UI_READY });
    await test.step('Check upstream and retain the exact update preview', async () => {
      const response = page.waitForResponse(res => res.url().endsWith(`${SKILLS}${id}/source/check-updates/`) && res.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Check for updates', exact: true }).click();
      const ack = await response;
      expect(ack.status(), await ack.text()).toBe(202);
      update = (await ack.json()).result;
    }, { timeout: UI_READY });
    update = await finishPreview(update!.id);
    await testInfo.attach('upstream-check-receipt', { body: JSON.stringify({ update, ledger: await fake.ledger(),
      personalDraft: await read<Draft>(`${SKILLS}${id}/draft/`), retainedSource: await read<Source>(`${SKILLS}${id}/source/`) }), contentType: 'application/json' });
    expect(update.outcome).toBe('changed');
    expect(update.source.commit_sha).toBe(shaB);
    expect(update.local_diverged).toBe(true);
    expect(update.expected_latest_version).toBe(2);
    expect(update.diff.files.filter(file => file.status !== 'unchanged').map(file => [file.path, file.status, file.from_text, file.to_text]))
      .toEqual([['SKILL.md', 'modified', localRoot, root], ['references/policy.md', 'modified', oldPolicy, newPolicy]]);
    const stageBody = { preview_id: update.id, revision: 0, expected_source_revision: update.source_revision,
      expected_latest_version: update.expected_latest_version, expected_latest_digest: update.expected_latest_digest,
      accept_replacement: true, rights: { confirmed: true, review_note: 'Synthetic source replacement reviewed; retain notices.' } };
    await test.step('Inspect divergence and the blocked personal-draft replacement', async () => {
      await expect(page.getByText('Your draft already exists. Open or discard it before staging an update.', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByRole('button', { name: 'Stage replacement draft', exact: true })).toBeDisabled({ timeout: UI_READY });
      await page.getByRole('combobox', { name: /^Changed file/ }).click();
      await page.getByRole('option', { name: 'SKILL.md · modified', exact: true }).click();
      await testInfo.attach('source-divergence', { body: await page.screenshot(), contentType: 'image/png' });
    }, { timeout: UI_READY });
    const refused = await scopeActors.send<{ code: string }>(actor, 'POST', `${SKILLS}${id}/source/stage-update/`, stageBody);
    expect([refused.status, refused.body.code]).toEqual([409, 'draft_conflict']);
    expect(await read<Draft>(`${SKILLS}${id}/draft/`)).toEqual(personal!);
    const releasesBefore = await releaseRows();
    const labelsBefore = await labelRows();
    await test.step('Explicitly discard personal work while retaining the reviewed source page', async () => {
      const editor = await context.newPage();
      editor.setDefaultTimeout(UI_READY);
      await editor.goto(`${UI}/${id}/edit`);
      await editor.getByRole('button', { name: 'Discard draft', exact: true }).click();
      await editor.getByRole('dialog').getByRole('button', { name: 'Discard draft', exact: true }).click();
      await expect(editor.getByRole('dialog')).not.toBeVisible({ timeout: UI_READY });
      await editor.close();
      await page.bringToFront();
      expect((await scopeActors.send(actor, 'GET', `${SKILLS}${id}/draft/`)).status).toBe(404);
      // Keep the reviewed preview and inputs; do not depend on browser focus timing
      // or re-check a mutable source just to notice another tab discarded the draft.
      const draftStatus = page.waitForResponse(res => res.url().endsWith(`${SKILLS}${id}/draft/`) && res.request().method() === 'GET', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Refresh draft status', exact: true }).click();
      expect((await draftStatus).status()).toBe(404);
      await expect(page.getByText('Your draft already exists. Open or discard it before staging an update.', { exact: true })).not.toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    let staged: Draft;
    await test.step('Review replacement consequences and stage the retained bytes', async () => {
      await page.getByRole('checkbox', { name: 'Replace my reviewed local changes. They will not be merged.', exact: true }).check();
      await page.getByRole('checkbox', { name: 'I confirm that I have the rights to import these files.', exact: true }).check();
      await page.getByRole('textbox', { name: /^Rights-review note/ }).fill(stageBody.rights.review_note);
      const response = page.waitForResponse(res => res.url().endsWith(`${SKILLS}${id}/source/stage-update/`) && res.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Stage replacement draft', exact: true }).click();
      const accepted = await response;
      expect(accepted.status(), await accepted.text()).toBe(200);
      expect(accepted.request().postDataJSON()).toEqual(stageBody);
      const result = (await accepted.json()).result;
      expect(result.published).toBe(false);
      staged = result.draft;
      expect(result.consumed).toEqual({ skill_id: id, draft_revision: staged.revision });
      await expect(page.getByText('Replacement draft staged. Nothing was published or promoted.', { exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    expect([...staged!.files].sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0)).toEqual(upstream);
    expect(staged!.revision).toBeGreaterThan(personal!.revision);
    expect(staged!.source).toMatchObject({ commit_sha: shaB, upstream_digest: skillDigest(upstream) });
    expect(await releaseRows()).toEqual(releasesBefore);
    expect(await labelRows()).toEqual(labelsBefore);
    // StageSourceOut returns the immutable acceptance receipt on replay, not the
    // mutable draft payload. Read the actual draft independently to prove no save.
    const replay = (await actor.api.post<Envelope<{ draft: Draft | null; published: boolean; consumed: { skill_id: string; draft_revision: number } }>>(`${SKILLS}${id}/source/stage-update/`, stageBody)).result;
    expect(replay).toEqual({ published: false, draft: null, consumed: { skill_id: id, draft_revision: staged!.revision } });
    expect(await read<Draft>(`${SKILLS}${id}/draft/`)).toEqual(staged!);
    expect((await read<Source>(`${SKILLS}${id}/source/`)).imported_sha).toBe(shaA);
    await testInfo.attach('github-staging-receipt', { body: JSON.stringify({ id, update, personalRevision: personal!.revision, staged: staged!, replay, releasesBefore, labelsBefore, ledger: await fake.ledger() }), contentType: 'application/json' });
    await test.step('Inspect the staged source receipt in the browser', async () => {
      await page.getByRole('button', { name: 'Review and test changes', exact: true }).click();
      await selectSourceIfAvailable(page);
      await expect(page.getByRole('textbox', { name: /Editor content/ })).toBeVisible({ timeout: UI_READY });
      await testInfo.attach('github-replacement-draft', { body: await page.screenshot(), contentType: 'image/png' });
    }, { timeout: UI_READY });
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) await testInfo.attach('github-update-failure', { body: await page.screenshot(), contentType: 'image/png' });
    await context.close();
    await fake.dispose();
  }
});
