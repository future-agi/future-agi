import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { uploadSkillsArchive } from '../../lib/skills-upload';
import { installSkillCommand, readSkillViaMcp, sha256, skillDigest } from '../../lib/skills-consumption';
import type { SkillFile, SkillManifest } from '../../lib/skills-consumption';

// frontend/src/routes/paths.js:skills; model_hub/urls_skills_hub.py.
const LIBRARY = '/dashboard/skills';
const SKILLS = '/model-hub/skills/';
// README first-paint/autosave budget, not CDC: every write below is acknowledged.
const UI_READY = 60_000;
// templates/support-policy: three files, including the reference renamed in v2.
const TEMPLATE_PATHS = ['SKILL.md', 'references/examples.md', 'references/policy.md'];
// An obviously synthetic generic-secret-rule fixture, never a real provider key.
const FAKE_SECRET = '\n\nSynthetic detector fixture only: api_key=FAKE_ONLY_0123456789_abcdefghijklmnopqrstuvwxyz\n';
// UsageTab.jsx deliberately describes retrieval, not native-agent execution.
const FETCH_CAVEAT = "Counts requests that fetched this skill. A fetch doesn't prove an agent used it.";

type Envelope<T> = { status: boolean; result: T };
type Draft = { base_version: number | null; revision: number; files: SkillFile[]; note: string };
type Version = { id: string; number: number; digest: string; labels: string[] };
type Label = { name: string; version: number };
type Library = { results: { id: string; name: string }[]; stats: { total: number } };
type Usage = { total: number; by_channel: { api: number; download: number; mcp: number } };
type Checks = { items: { code: string }[]; summary: { errors: number; warnings: number } };

async function selectSourceIfAvailable(page: Page) {
  const monaco = page.getByRole('textbox', { name: /Editor content/ });
  const source = page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true });
  await expect(monaco.or(source).first()).toBeVisible({ timeout: UI_READY });
  if (await source.isVisible()) await source.click();
}

async function editText(page: Page, value: string) {
  await selectSourceIfAvailable(page);
  // SkillFileEditor -> FormCodeEditor/Monaco accessible input. Paste through the
  // real clipboard; never call editor models, app state or a replacement API write.
  const editor = page.getByRole('textbox', { name: /Editor content/ });
  await expect(editor).toBeVisible({ timeout: UI_READY });
  await editor.focus();
  await editor.press('Control+a');
  await page.evaluate(text => navigator.clipboard.writeText(text), value);
  await editor.press('Control+v');
}

const sortedFiles = (files: SkillFile[]) => [...files].sort((a, b) => a.path.localeCompare(b.path));

test('SKILLS-E2E-001: author, restore and consume the reviewed workspace skill', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-001', area: 'skills',
    userGoal: 'A developer authors, publishes, restores and consumes the exact reviewed workspace skill while production changes only through an explicit promotion',
    steps: [
      'open an isolated Skills library and create from Support policy',
      'edit in Monaco to trigger a synthetic secret warning, remove it and wait for autosave',
      'publish v1 with production, rename a reference and repair its link, then publish v2 without promotion',
      'compare v1 and v2, restore v1 as distinct v3, explicitly move production and inspect history',
      'preview a valid-plus-invalid ZIP and import only the valid skill',
      'copy and execute the validated localhost install command in an owned temporary project',
      'reconstruct the immutable release and a separate greater-than-100-KiB UTF-8 fixture through real MCP',
      'inspect fetch usage and the populated library in light and dark themes',
    ],
    backendChecks: [
      'template and draft belong to the actor organization/workspace and persist exact authored files with a monotonic revision',
      'live checks detect the synthetic secret and clear after its removal',
      'v1 and v2 remain distinct immutable snapshots, publication sends the saved revision, and production stays on v1 after v2',
      'compare returns the exact added, removed, modified and unchanged path sets shown in the UI',
      'restore creates distinct v3 with the v1 digest; explicit CAS promotion records exactly the expected history and rejects a stale move',
      'ZIP preview disables the invalid bundle and imports only the valid identity',
      'installed bytes and lock identity plus real MCP reconstruction equal the immutable manifest, including the long UTF-8 fixture',
      'API, download and MCP fetch accounting increases for the minted skill without claiming execution',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(1_260_000); // 16 UI stages × 60s + 2 transport stages × 120s + setup/cleanup 60s.
  // ScopeActors is test-scoped. OwnerB has one empty default workspace; ownerA
  // also has a second workspace, which the legacy unscoped key recipe cannot select.
  // This flow qualifies that recipe only for a single/default workspace.
  const actor = scopeActors.ownerB;
  const credentials = scopeActors.owners[1];
  const prefix = `e2e-skills1-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `${prefix}-policy`;
  const importedName = `${prefix}-imported`;
  const invalidName = `${prefix}-invalid`;
  const largeName = `${prefix}-long`;
  await testInfo.attach('skill-fixtures', { contentType: 'application/json', body: JSON.stringify({
    name, importedName, invalidName, largeName, organizationId: actor.organizationId, workspaceId: actor.workspaceId,
  }) });
  const context = await scopeActors.openContext(browser, actor);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = await context.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });
  page.setDefaultTimeout(UI_READY);
  const read = async <T>(path: string, params?: Record<string, string | number>): Promise<T> => {
    const body = await actor.api.get<Envelope<T>>(path, params);
    expect(body.status).toBe(true);
    return body.result;
  };
  let id = '';
  let initialDraft: Draft;
  let v1Files: SkillFile[] = [];
  let v2Files: SkillFile[] = [];
  let v1: Version;
  let v3: Version;
  let beforeUsage: Usage;
  let afterUsage: Usage;
  let command = '';
  let manifest: SkillManifest;

  const saved = async (expected: SkillFile[]) => {
    await expect.poll(async () => {
      const response = await scopeActors.send<Envelope<Draft>>(actor, 'GET', `${SKILLS}${id}/draft/`);
      if (response.status === 404) return null; // new edit has no live draft before its first autosave
      expect(response.status).toBe(200);
      return sortedFiles(response.body.result.files);
    }, { timeout: UI_READY }).toEqual(sortedFiles(expected));
    await expect(page.getByText('Saved', { exact: true })).toBeVisible({ timeout: UI_READY });
    return read<Draft>(`${SKILLS}${id}/draft/`);
  };
  const publish = async (number: number, note: string, production = false) => {
    // PublishDialog sends PublishDraftRequest.revision, including the save-before-publish acknowledgment.
    await page.getByRole('button', { name: 'Publish', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText('Publish skill', { exact: true })).toBeVisible({ timeout: UI_READY });
    await dialog.getByRole('textbox', { name: 'Version note' }).fill(note);
    if (production) await dialog.getByRole('checkbox', { name: 'production', exact: true }).check();
    const draft = await read<Draft>(`${SKILLS}${id}/draft/`);
    const responsePromise = page.waitForResponse(response => response.url().endsWith(`${SKILLS}${id}/draft/publish/`) &&
      response.request().method() === 'POST', { timeout: UI_READY });
    await dialog.getByRole('button', { name: 'Publish', exact: true }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(201);
    expect(response.request().postDataJSON().revision).toBe(draft.revision);
    const version = ((await response.json()) as Envelope<{ version: Version }>).result.version;
    expect(version.number).toBe(number);
    await testInfo.attach(`published-v${number}`, { body: JSON.stringify({ skillId: id, revision: draft.revision, version }), contentType: 'application/json' });
    await expect(page).toHaveURL(new RegExp(`${LIBRARY}/${id}(?:\\?.*)?$`), { timeout: UI_READY });
    return version;
  };

  try {
    await test.step('Create from the template in an empty library', async () => {
      const initial = await read<Library>(SKILLS);
      expect(initial.results).toEqual([]);
      expect(initial.stats.total).toBe(0);
      await page.goto(LIBRARY, { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('button', { name: 'Start from a template', exact: true })).toBeVisible({ timeout: UI_READY });
      await page.getByRole('button', { name: /^Support policy/ }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('textbox', { name: /^Name/ }).fill(name);
      await dialog.getByRole('textbox', { name: /^Description/ }).fill('Use when a customer asks about refunds, returns, or support-policy exceptions.');
      await expect(dialog.getByRole('combobox', { name: 'Start from' })).toHaveText('Support policy', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Create skill', exact: true }).click();
      // v1.1 offers optional private assistance after identity creation; this
      // lifecycle chooses the ordinary editor and never starts a paid proposal.
      await page.getByRole('dialog').getByRole('button', { name: 'Open editor', exact: true }).click();
      await expect(page).toHaveURL(/\/dashboard\/skills\/[a-f0-9-]+\/edit$/, { timeout: UI_READY });
      id = new URL(page.url()).pathname.split('/')[3];
      await testInfo.attach('created-skill', { body: id, contentType: 'text/plain' });
      initialDraft = await read<Draft>(`${SKILLS}${id}/draft/`);
      expect(initialDraft.files.map(file => file.path).sort()).toEqual(TEMPLATE_PATHS);
      // models/skills_hub.py:HubSkill/HubSkillDraft, no ambient tenant manager.
      const scope = await probe.pg<{ id: string; organization_id: string; workspace_id: string; created_by_id: string }>(
        'SELECT id, organization_id, workspace_id, created_by_id FROM model_hub_hubskill WHERE id = $1 AND name = $2', [id, name]);
      expect(scope).toEqual([{ id, organization_id: actor.organizationId, workspace_id: actor.workspaceId, created_by_id: actor.userId }]);
    }, { timeout: UI_READY });

    await test.step('Live checks find the synthetic secret', async () => {
      const initial = initialDraft.files.find(file => file.path === 'SKILL.md')!.content;
      const [response] = await Promise.all([
        page.waitForResponse(response => response.url().endsWith(`${SKILLS}checks/`) &&
          response.request().postData()?.includes('FAKE_ONLY_') === true, { timeout: UI_READY }),
        editText(page, initial + FAKE_SECRET),
      ]);
      expect(response.status()).toBe(200);
      const checks = ((await response.json()) as Envelope<Checks>).result;
      expect(checks.items.filter(item => item.code === 'secret_suspected').map(item => item.code)).toEqual(['secret_suspected']);
      const warnings = page.getByRole('region', { name: 'Warnings', exact: true });
      await expect(warnings.getByText(/possible .* was found/)).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('Remove the warning and persist the exact revision-one files', async () => {
      v1Files = initialDraft.files.map(file => file.path === 'SKILL.md'
        ? { ...file, content: file.content + `\n\n${prefix}: revision one; escalate ambiguous requests to a human reviewer.\n` } : file);
      const [checked] = await Promise.all([
        page.waitForResponse(response => response.url().endsWith(`${SKILLS}checks/`) &&
          response.request().postData()?.includes(`${prefix}: revision one`) === true && !response.request().postData()?.includes('FAKE_ONLY_'), { timeout: UI_READY }),
        editText(page, v1Files.find(file => file.path === 'SKILL.md')!.content),
      ]);
      const clean = ((await checked.json()) as Envelope<Checks>).result;
      expect(clean.items.filter(item => item.code === 'secret_suspected')).toEqual([]);
      expect(clean.summary).toMatchObject({ errors: 0, warnings: 0 });
      const draft = await saved(v1Files);
      expect(draft.revision).toBeGreaterThan(initialDraft.revision);
      const rows = await probe.pg<{ revision: number; files: SkillFile[]; base_version: number | null }>(
        'SELECT revision, files, base_version FROM model_hub_hubskilldraft WHERE skill_id = $1 AND user_id = $2 AND discarded_at IS NULL', [id, actor.userId]);
      expect(rows).toEqual([{ revision: draft.revision, files: draft.files, base_version: null }]);
      expect(sortedFiles(rows[0].files)).toEqual(sortedFiles(v1Files));
      await expect(page.getByRole('region', { name: 'Warnings', exact: true })).not.toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('Publish v1 and explicitly assign production', async () => {
      v1 = await publish(1, `${prefix}: first reviewed version`, true);
      expect(v1.digest).toBe(skillDigest(v1Files));
      expect((await read<Label[]>(`${SKILLS}${id}/labels/`)).map(label => [label.name, label.version])).toEqual([['production', 1]]);
      await expect(page.getByRole('heading', { name: 'Resolve a return or refund question', exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('Rename a supporting file through the editor', async () => {
      await page.getByRole('link', { name: 'Edit latest v1', exact: true }).click();
      await page.getByRole('treeitem', { name: 'examples.md', exact: true }).click();
      await page.getByRole('button', { name: 'Rename', exact: true }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('textbox', { name: 'File path', exact: true }).fill('references/exceptions.md');
      await dialog.getByRole('button', { name: 'Rename', exact: true }).click();
      await expect(dialog).not.toBeVisible({ timeout: UI_READY });
      await page.getByRole('treeitem', { name: 'SKILL.md', exact: true }).click();
    }, { timeout: UI_READY });

    await test.step('Repair the link and save revision-two content', async () => {
      v2Files = v1Files.map(file => file.path === 'references/examples.md' ? { ...file, path: 'references/exceptions.md' }
        : file.path === 'SKILL.md' ? { ...file, content: file.content.replace('revision one', 'revision two')
          .replace('escalate ambiguous requests', 'escalate all exceptional requests')
          .replace('references/examples.md', 'references/exceptions.md') } : file);
      await editText(page, v2Files.find(file => file.path === 'SKILL.md')!.content);
      const draft = await saved(v2Files);
      expect(draft.base_version).toBe(1);
    }, { timeout: UI_READY });

    await test.step('Publish v2 without changing production or v1 bytes', async () => {
      const v2 = await publish(2, `${prefix}: broaden escalation`);
      expect(v2.id).not.toBe(v1.id);
      expect(v2.digest).toBe(skillDigest(v2Files));
      expect(v2.digest).not.toBe(v1.digest);
      expect((await read<Label[]>(`${SKILLS}${id}/labels/`)).map(label => [label.name, label.version])).toEqual([['production', 1]]);
      const versions = await probe.pg<{ id: string; number: number; digest: string }>(
        'SELECT id, number, digest FROM model_hub_hubskillversion WHERE skill_id = $1 ORDER BY number', [id]);
      expect(versions).toEqual([{ id: v1.id, number: 1, digest: skillDigest(v1Files) }, { id: v2.id, number: 2, digest: skillDigest(v2Files) }]);
      const files = await probe.pg<{ number: number; path: string; content: string }>(
        'SELECT v.number, f.path, b.content FROM model_hub_hubskillversion v JOIN model_hub_hubskillfile f ON f.version_id = v.id JOIN model_hub_hubskillblob b ON b.id = f.blob_id WHERE v.skill_id = $1 ORDER BY v.number, f.path', [id]);
      expect(files).toEqual([...sortedFiles(v1Files).map(file => ({ number: 1, ...file })), ...sortedFiles(v2Files).map(file => ({ number: 2, ...file }))]);
    }, { timeout: UI_READY });

    await test.step('Compare exact file status sets in API and UI', async () => {
      await page.getByRole('tab', { name: 'Versions', exact: true }).click();
      await page.getByRole('button', { name: 'Actions for v2', exact: true }).click();
      await page.getByRole('menuitem', { name: 'Compare with previous', exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/skills/${id}/compare\\?from=1&to=2`), { timeout: UI_READY });
      const diff = await read<{ files: { path: string; status: string }[]; summary: object }>(`${SKILLS}${id}/compare/`, { from: 1, to: 2 });
      expect(diff.files.map(file => [file.path, file.status]).sort()).toEqual([
        ['SKILL.md', 'modified'], ['references/examples.md', 'removed'], ['references/exceptions.md', 'added'], ['references/policy.md', 'unchanged'],
      ]);
      expect(diff.summary).toEqual({ added: 1, removed: 1, modified: 1, unchanged: 1 });
      await expect(page.getByRole('list', { name: 'Compared files' }).getByRole('button')).toHaveText([
        'references/examples.mdRemoved', 'references/exceptions.mdAdded', 'SKILL.mdModified', 'references/policy.mdUnchanged',
      ], { timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('Restore v1 as a distinct v3 without moving production', async () => {
      await page.goto(`${LIBRARY}/${id}?tab=versions`);
      await page.getByRole('button', { name: 'Actions for v1', exact: true }).click();
      await page.getByRole('menuitem', { name: 'Restore as new version', exact: true }).click();
      const dialog = page.getByRole('dialog');
      const responsePromise = page.waitForResponse(response => response.url().endsWith(`${SKILLS}${id}/versions/1/restore/`) && response.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Restore as new version', exact: true }).click();
      const response = await responsePromise;
      expect(response.status()).toBe(201);
      v3 = ((await response.json()) as Envelope<{ version: Version }>).result.version;
      expect(v3.number).toBe(3);
      expect(v3.digest).toBe(v1.digest);
      expect(v3.id).not.toBe(v1.id);
      await testInfo.attach('restored-version', { body: JSON.stringify({ skillId: id, version: v3 }), contentType: 'application/json' });
      await expect(dialog).not.toBeVisible({ timeout: UI_READY });
      expect((await read<Label[]>(`${SKILLS}${id}/labels/`)).map(label => [label.name, label.version])).toEqual([['production', 1]]);
    }, { timeout: UI_READY });

    await test.step('Explicit CAS promotion and exact history reject stale movement', async () => {
      await page.getByRole('button', { name: 'Move production', exact: true }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('combobox', { name: /^Version(?:\s|$)/ }).click();
      await page.getByRole('option', { name: 'v3', exact: true }).click();
      await dialog.getByRole('textbox', { name: 'Note (optional)' }).fill(`${prefix}: restore reviewed policy`);
      const responsePromise = page.waitForResponse(response => response.url().endsWith(`${SKILLS}${id}/labels/production/`) && response.request().method() === 'PUT', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Move production from v1 to v3', exact: true }).click();
      const response = await responsePromise;
      expect(response.status()).toBe(200);
      expect(response.request().postDataJSON()).toMatchObject({ version: 3, expected_version: 1 });
      await expect(dialog).not.toBeVisible({ timeout: UI_READY });
      const release = await read<{ release_revision: number }>(`${SKILLS}${id}/`);
      const stale = await scopeActors.send<{ code: string; detail: { current_version: number; release_revision: number } }>(actor, 'PUT', `${SKILLS}${id}/labels/production/`, {
        version: 2, expected_version: 1, expected_release_revision: release.release_revision,
      });
      expect(stale.status).toBe(409);
      // v11/governance.py reports protected pointer mismatches as release_conflict.
      // The current aggregate revision still matches: this is the pointer CAS.
      expect(stale.body.code).toBe('release_conflict');
      expect(stale.body.detail).toEqual({ current_version: 3, release_revision: release.release_revision });
      expect((await read<Label[]>(`${SKILLS}${id}/labels/`)).map(label => [label.name, label.version])).toEqual([['production', 3]]);
      await page.getByRole('button', { name: 'History for production', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Close label history' })).toBeVisible({ timeout: UI_READY });
      const history = await read<{ results: { from_version: number | null; to_version: number }[] }>(`${SKILLS}${id}/labels/history/`, { label: 'production' });
      expect(history.results.map(event => [event.from_version, event.to_version])).toEqual([[1, 3], [null, 1]]);
      const events = await probe.pg<{ from_version: number | null; to_version: number; actor_id: string }>(
        'SELECT from_version, to_version, actor_id FROM model_hub_hubskilllabelevent WHERE skill_id = $1 AND label = $2 ORDER BY created_at', [id, 'production']);
      expect(events).toEqual([{ from_version: null, to_version: 1, actor_id: actor.userId }, { from_version: 1, to_version: 3, actor_id: actor.userId }]);
      await page.getByRole('button', { name: 'Close label history' }).click();
    }, { timeout: UI_READY });

    await test.step('Preview the ZIP and disable the invalid bundle', async () => {
      await page.goto(LIBRARY);
      await page.getByRole('button', { name: 'Import', exact: true }).click();
      const dialog = page.getByRole('dialog');
      const archive = await uploadSkillsArchive(dialog.locator('input[type=file]:not([webkitdirectory])'), {
        [`${importedName}/SKILL.md`]: `---\nname: ${importedName}\ndescription: Use when a synthetic import exercise needs a reviewed support policy.\n---\n# Synthetic policy\nAsk a human to review exceptions.\n`,
        [`${invalidName}/SKILL.md`]: `---\nname: ${invalidName}\n---\n# Missing description\n`,
      });
      await testInfo.attach('import-archive-sha256', { body: sha256(archive), contentType: 'text/plain' });
      const responsePromise = page.waitForResponse(response => response.url().endsWith(`${SKILLS}import/preview/`), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Preview import', exact: true }).click();
      const response = await responsePromise;
      expect(response.status()).toBe(200);
      const preview = (await response.json()).result;
      expect(preview.bundles.map((bundle: { name: string; status: string }) => [bundle.name, bundle.status]).sort())
        .toEqual([[importedName, 'new'], [invalidName, 'invalid']].sort());
      await expect(dialog.getByRole('checkbox', { name: `Select ${invalidName}`, exact: true })).toBeDisabled({ timeout: UI_READY });
      await expect(dialog.getByRole('checkbox', { name: `Select ${importedName}`, exact: true })).toBeChecked({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('Import only the selected valid identity', async () => {
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('button', { name: 'Import', exact: true }).click();
      await expect(dialog.getByText('1 created · 0 updated · 0 up to date · 0 failed', { exact: true })).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Done', exact: true }).click();
      const library = await read<Library>(SKILLS);
      expect(library.results.map(skill => skill.name).sort()).toEqual([name, importedName].sort());
      const imported = library.results.find(skill => skill.name === importedName)!;
      await testInfo.attach('imported-skill', { body: JSON.stringify(imported), contentType: 'application/json' });
      const rows = await probe.pg<{ name: string; latest_version_number: number }>(
        'SELECT name, latest_version_number FROM model_hub_hubskill WHERE organization_id = $1 AND workspace_id = $2 AND name IN ($3, $4) ORDER BY name',
        [actor.organizationId, actor.workspaceId, importedName, invalidName]);
      expect(rows).toEqual([{ name: importedName, latest_version_number: 1 }]);
    }, { timeout: UI_READY });

    await test.step('Copy the production install command and capture fetch baseline', async () => {
      await page.goto(`${LIBRARY}/${id}?tab=use`);
      await page.getByRole('combobox', { name: /^Coding client/ }).click();
      await page.getByRole('option', { name: 'Claude Code', exact: true }).click();
      await page.getByRole('button', { name: 'Copy code', exact: true }).click();
      command = await page.evaluate(() => navigator.clipboard.readText());
      expect(command.includes(credentials.apiKey)).toBe(false);
      beforeUsage = await read<Usage>(`${SKILLS}${id}/usage/`, { days: 7 });
      manifest = await read<SkillManifest>(`${SKILLS}resolve/`, { name }); // neither selector means production
      expect(manifest.version_id).toBe(v3.id);
      expect(manifest.digest).toBe(v1.digest);
    }, { timeout: UI_READY });

    await test.step('Install the copied command and verify exact bytes and lock', async () => {
      const installed = await installSkillCommand(command, E2E.apiUrl, credentials, manifest);
      expect(installed.versionId).toBe(v3.id);
      expect(installed.digest).toBe(v1.digest);
      expect(sortedFiles(installed.files)).toEqual(sortedFiles(v1Files));
      await testInfo.attach('installed-identity', { body: JSON.stringify({ versionId: installed.versionId,
        digest: installed.digest, files: installed.files.map(file => [file.path, sha256(file.content)]) }), contentType: 'application/json' });
    }, { timeout: 120_000 });

    await test.step('Reconstruct the release and long UTF-8 fixture through real MCP', async () => {
      const reconstructed = await readSkillViaMcp(E2E.apiUrl, credentials, name);
      expect(reconstructed.manifest.version_id).toBe(v3.id);
      expect(sortedFiles(reconstructed.files)).toEqual(sortedFiles(v1Files));
      afterUsage = await read<Usage>(`${SKILLS}${id}/usage/`, { days: 7 });
      // PushRequest/FileInput: only this separate transport fixture is API-seeded.
      const content = `---\nname: ${largeName}\ndescription: Use when testing lossless synthetic UTF-8 skill transport through MCP.\n---\n` +
        'Synthetic reference café 漢字 🧪; do not execute.\n'.repeat(2500);
      expect(Buffer.byteLength(content)).toBeGreaterThan(100 * 1024);
      const seeded = await actor.api.post<Envelope<{ skill: { id: string }; version: Version }>>(`${SKILLS}publish/`, {
        files: [{ path: 'SKILL.md', content }], labels: ['production'], acknowledge_warnings: true,
        expected_release_revision: 0,
      });
      await testInfo.attach('long-file-fixture', { body: JSON.stringify({ skillId: seeded.result.skill.id,
        version: seeded.result.version, sha256: sha256(content), bytes: Buffer.byteLength(content) }), contentType: 'application/json' });
      const long = await readSkillViaMcp(E2E.apiUrl, credentials, largeName);
      expect(long.manifest.version_id).toBe(seeded.result.version.id);
      expect(long.files).toEqual([{ path: 'SKILL.md', content }]);
      expect(long.chunks).toBeGreaterThan(1);
      await testInfo.attach('mcp-receipts', { body: JSON.stringify({ primary: { versionId: reconstructed.manifest.version_id,
        digest: reconstructed.manifest.digest, chunks: reconstructed.chunks }, long: { versionId: long.manifest.version_id,
        digest: long.manifest.digest, chunks: long.chunks } }), contentType: 'application/json' });
    }, { timeout: 120_000 });

    await test.step('Fetch channel deltas and UI explicitly mean retrieval only', async () => {
      const deltas = { api: afterUsage.by_channel.api - beforeUsage.by_channel.api,
        download: afterUsage.by_channel.download - beforeUsage.by_channel.download,
        mcp: afterUsage.by_channel.mcp - beforeUsage.by_channel.mcp };
      // One REST resolve, one install ZIP, one MCP manifest plus offset-zero read per file.
      expect(deltas).toEqual({ api: 1, download: 1, mcp: 1 + v1Files.length });
      await testInfo.attach('fetch-deltas', { body: JSON.stringify(deltas), contentType: 'application/json' });
      await page.getByRole('tab', { name: 'Usage', exact: true }).click();
      await expect(page.getByText(FETCH_CAVEAT, { exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('Populated library shows the exact synthetic skills in light and dark', async () => {
      await page.goto(LIBRARY);
      const library = await read<Library>(SKILLS);
      expect(library.results.map(skill => skill.name).sort()).toEqual([name, importedName, largeName].sort());
      for (const mode of ['light', 'dark'] as const) {
        await page.evaluate(themeMode => {
          const settings = JSON.parse(localStorage.getItem('settings') || '{}');
          localStorage.setItem('settings', JSON.stringify({ ...settings, themeMode }));
        }, mode);
        await page.emulateMedia({ colorScheme: mode });
        await page.reload();
        await expect(page.getByRole('cell').getByRole('link')).toHaveText([largeName, importedName, name], { timeout: UI_READY });
        await testInfo.attach(`skills-library-${mode}`, { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
      }
    }, { timeout: 2 * UI_READY });
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) {
      await testInfo.attach('skills-failure', { body: await page.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
    }
    await context.close();
  }
});
