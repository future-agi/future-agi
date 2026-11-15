import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { unzipSync } from 'fflate';
import { test, expect } from '../../lib/scope-actors';
import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { skillDigest } from '../../lib/skills-consumption';

// ROUND3-PLAN J1 steps 5–6 and e2e-flow-approvals.md: source-v2 prerequisite,
// deliberately NOT the complete 15-member cold/configured job or native delivery.
// Wire/locators: RetainedMigrationPanel, MigrationPlanPanel, SkillEditorSession,
// RecoverableDraftChanges; r3/import_plans.py and r3/draft_recovery.py.
const UI_READY = 60_000;
const BASE = '/model-hub/skills/';
type File = { path: string; content: string };
type Draft = { files: File[]; revision: number; recovery_digest: string };
type Item = { key: string; destination_id: string | null; migration: { hub_diverged: boolean } };
type Plan = { id: string; inventory: { items: Item[] } };
type Envelope<T> = { result: T };

test('SKILLS-E2E-011: preserve a personal draft before replacing a diverged retained source', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-011', area: 'skills',
    userGoal: 'Review a second source revision against a diverged Hub leaf and preserve the personal draft before explicitly replacing it, without moving production',
    steps: ['import a synthetic versioned exchange through retained migration and publish v1',
      'publish a Hub-only edit as v2, then save a distinct personal draft',
      'preview source revision two and inspect Base/Source/Hub with replacement blocked by the personal draft',
      'download the personal draft and preserve/suspend it through the migration handoff, recovering a lost response',
      'preview again and explicitly replace the same mapped destination; independently inspect retained checkpoint bytes and unchanged publication/labels',
      'publish the reviewed replacement, then download exact ZIP and mapping receipts for viewed v2 and v3 separately'],
    backendChecks: ['source revision two maps to the original identity and reports Hub divergence with the personal draft still unchanged',
      'the downloaded draft and the single owner-private checkpoint contain the exact personal bytes; suspension retry reuses the original request and key',
      'the replacement draft has exact source-v2 bytes, one destination identity, exactly two published versions and no production or staging labels',
      'downloaded viewed-version ZIPs equal Hub-v2 and source-v2 bytes respectively; each receipt binds the same immutable version/digest and correct derived or identical source relation'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(1_020_000); // 10 UI + 2 recovery + 4 export stages × 60s + setup/cleanup 60s.
  const actor = scopeActors.ownerB;
  const name = `j1-update-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const root = process.env.J1_UPDATE_EVIDENCE_DIR || testInfo.outputPath('j1-update');
  await mkdir(join(root, 'shots'), { recursive: true });
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
  await context.addInitScript(authInitScript, { access: actor.tokens.access, refresh: actor.tokens.refresh, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await context.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), actor.organizationId);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const content = (body: string) => `---\nname: ${name}\ndescription: Use when reviewing a synthetic migration policy\n---\n\n${body}\n`;
  const base: File[] = [{ path: 'SKILL.md', content: content('Base policy café') }];
  const hub: File[] = [{ path: 'SKILL.md', content: content('Hub-only divergence café') }];
  const personal: File[] = [{ path: 'SKILL.md', content: content('Private personal draft café') }];
  const source: File[] = [{ path: 'SKILL.md', content: content('Reviewed source revision two café') }];
  const read = async <T>(path: string): Promise<T> => (await actor.api.get<Envelope<T>>(BASE + path)).result;
  const choose = async (label: string, value: string) => {
    await page.getByRole('combobox', { name: label, exact: true }).click();
    await page.getByRole('option', { name: value, exact: true }).click();
  };
  const preview = async (files: File[], revision: string) => {
    await page.goto('/dashboard/skills');
    await page.getByRole('button', { name: /^(Import a folder or ZIP|Import)$/ }).click();
    await page.getByRole('tab', { name: 'From another tool', exact: true }).click();
    const exportData = { format: 'langfuse-skills-v1', origin: 'synthetic.invalid', project: name,
      skills: [{ resource: 'retained-policy', revision, digest: skillDigest(files), files }] };
    await page.getByLabel('Retained source files', { exact: true }).setInputFiles({ name: 'source.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(exportData)) });
    const response = page.waitForResponse(res => res.url().endsWith('/intake/langfuse/preview/') && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Preview import', exact: true }).click();
    const result = await response;
    assert.equal(result.status(), 200);
    const plan = (await result.json()).result as Plan;
    await page.getByRole('button', { name: 'Decide one item at a time', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Review one item', exact: true })).toBeVisible({ timeout: UI_READY });
    await choose('Review item', name);
    return read<Plan>(`intake/${plan.id}/`);
  };
  const replace = async (plan: Plan) => {
    await choose('Source update decision', 'Replace with reviewed source');
    await page.getByRole('checkbox', { name: /I have reviewed source rights/ }).check();
    await page.getByRole('checkbox', { name: /Apply this exact decision/ }).check();
    const response = page.waitForResponse(res => res.url().endsWith(`/intake/${plan.id}/accept/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Import reviewed item as draft', exact: true }).click();
    assert.equal((await response).status(), 200);
    await expect(page.getByText(/Decision recorded for this item/)).toBeVisible({ timeout: UI_READY });
    const receipts = await read<{ count: number; results: { result: { resource_id: string } }[] }>(`intake/${plan.id}/receipts/`);
    assert.equal(receipts.count, 1);
    return receipts.results[0].result.resource_id;
  };
  const publish = async (id: string, number: number) => {
    await page.getByRole('button', { name: 'Publish', exact: true }).click();
    const dialog = page.getByRole('dialog');
    const warnings = dialog.getByRole('checkbox', { name: 'I reviewed the warnings and want to publish this version.', exact: true });
    await expect.poll(async () => await warnings.count() > 0 || await dialog.getByRole('button', { name: 'Publish', exact: true }).isEnabled(), { timeout: UI_READY }).toBeTruthy();
    if (await warnings.count()) await warnings.check();
    await expect(dialog.getByRole('checkbox', { name: 'production', exact: true })).not.toBeChecked();
    await expect(dialog.getByRole('checkbox', { name: 'staging', exact: true })).not.toBeChecked();
    const response = page.waitForResponse(res => res.url().endsWith(`/${id}/draft/publish/`) && res.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Publish', exact: true }).click();
    assert.equal((await response).status(), 201);
    await expect(dialog).not.toBeVisible({ timeout: UI_READY });
    assert.equal((await read<{ count: number }>(`${id}/versions/`)).count, number);
  };
  const edit = async (id: string, files: File[]) => {
    await page.goto(`/dashboard/skills/${id}/edit`);
    const sourceTab = page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true });
    await expect(sourceTab).toBeVisible({ timeout: UI_READY });
    await sourceTab.click();
    const input = page.getByRole('textbox', { name: /Editor content/ });
    await input.focus();
    await input.press('Control+a');
    await page.evaluate(text => navigator.clipboard.writeText(text), files[0].content);
    await input.press('Control+v');
    // A new editor draft is absent until its first autosave; retry that 404,
    // but require exact bytes rather than treating absence as an empty draft.
    await expect(async () => assert.deepEqual((await read<Draft>(`${id}/draft/`)).files, files)).toPass({ timeout: UI_READY });
  };
  try {
    const first = await preview(base, 'v1');
    const id = await replace(first);
    await testInfo.attach('j1-destination', { contentType: 'application/json', body: JSON.stringify({ id, name, evidenceClass: 'synthetic fixture' }) });
    await page.goto(`/dashboard/skills/${id}/edit`);
    await publish(id, 1);
    await edit(id, hub);
    await publish(id, 2);
    await edit(id, personal);
    const second = await preview(source, 'v2');
    const mappingOracle = (plan: Plan, draft: Draft) => {
      assert.equal(plan.inventory.items.length, 1);
      assert.equal(plan.inventory.items[0].destination_id, id);
      assert.equal(plan.inventory.items[0].migration.hub_diverged, true);
      assert.deepEqual(draft.files, personal);
    };
    const current = await read<Draft>(`${id}/draft/`);
    mappingOracle(second, current);
    const wrongMapping = structuredClone(second);
    wrongMapping.inventory.items[0].destination_id = 'wrong-destination';
    assert.throws(() => mappingOracle(wrongMapping, current));
    assert.throws(() => mappingOracle(second, { ...current, files: source }));
    await choose('Source update decision', 'Replace with reviewed source');
    await expect(page.getByRole('button', { name: 'Import reviewed item as draft', exact: true })).toBeDisabled();
    await expect(page.getByText(`${name} changed both in your source and here. Choose which version to keep.`, { exact: true })).toBeVisible();
    await expect(page.getByText('Preserve your draft before replacing it, then preview again.', { exact: true })).toBeVisible();
    await page.screenshot({ path: join(root, 'shots', '01-divergence.png') });
    await page.getByRole('button', { name: 'Preserve draft', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Preserve your draft', exact: true })).toBeVisible({ timeout: UI_READY });
    await page.screenshot({ path: join(root, 'shots', '02-recovery-light.png') });
    await page.evaluate(() => { const settings = JSON.parse(localStorage.getItem('settings') || '{}'); localStorage.setItem('settings', JSON.stringify({ ...settings, themeMode: 'dark' })); });
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Preserve your draft', exact: true })).toBeVisible({ timeout: UI_READY });
    await page.screenshot({ path: join(root, 'shots', '03-recovery-dark.png') });
    const downloaded = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download unsent edits', exact: true }).click();
    const downloadPath = join(root, 'personal-draft.json');
    await (await downloaded).saveAs(downloadPath);
    const downloadedFiles = JSON.parse(await readFile(downloadPath, 'utf8')).files;
    const attempts: { key: string | undefined; body: string | null }[] = [];
    await page.route(`**/skills/${id}/draft/checkpoints/`, async route => {
      if (route.request().method() !== 'POST') return route.continue();
      attempts.push({ key: route.request().headers()['idempotency-key'], body: route.request().postData() });
      if (attempts.length === 1) {
        assert.equal((await route.fetch()).status(), 200);
        await route.abort('failed');
      } else await route.continue();
    });
    await page.getByRole('checkbox', { name: 'Save a recovery checkpoint and suspend my current draft.', exact: true }).check();
    await page.getByRole('button', { name: 'Preserve and suspend draft', exact: true }).click();
    await expect(page.getByText(/The operation could not be confirmed/)).toBeVisible({ timeout: UI_READY });
    await page.getByRole('button', { name: 'Preserve and suspend draft', exact: true }).click();
    await expect(page).toHaveURL(/\/dashboard\/skills$/, { timeout: UI_READY });
    const checkpoints = await scopeProbe.pg<{ payload: { files: File[] }; digest: string }>('SELECT payload, digest FROM model_hub_hubskilldraftcheckpoint WHERE skill_id = $1 AND workspace_id = $2', [id, actor.workspaceId]);
    const checkpointOracle = (rows: typeof checkpoints, downloads: File[], requests: typeof attempts) => {
      assert.equal(rows.length, 1);
      assert.deepEqual(rows[0].payload.files, personal);
      assert.deepEqual(downloads, personal);
      assert.equal(requests.length, 2);
      assert.ok(requests[0].key);
      assert.deepEqual(requests[0], requests[1]);
      assert.equal(JSON.parse(requests[0].body!).expected_digest, rows[0].digest);
    };
    checkpointOracle(checkpoints, downloadedFiles, attempts);
    assert.throws(() => checkpointOracle([{ ...checkpoints[0], payload: { files: source } }], downloadedFiles, attempts));
    assert.throws(() => checkpointOracle(checkpoints, source, attempts));
    assert.throws(() => checkpointOracle(checkpoints, downloadedFiles, [attempts[0], { ...attempts[1], key: 'different-key' }]));
    const fresh = await preview(source, 'v2');
    assert.equal(await replace(fresh), id);
    const draft = await read<Draft>(`${id}/draft/`);
    const versions = await read<{ count: number }>(`${id}/versions/`);
    const labels = await read<unknown[]>(`${id}/labels/`);
    const identities = await scopeProbe.pg<{ id: string }>('SELECT id FROM model_hub_hubskill WHERE name = $1 AND workspace_id = $2', [name, actor.workspaceId]);
    const replacementOracle = (value: Draft, rows: typeof identities, count: number, labelsValue: unknown[]) => {
      assert.deepEqual(value.files, source);
      assert.deepEqual(rows.map(row => row.id), [id]);
      assert.equal(count, 2);
      assert.deepEqual(labelsValue, []);
    };
    replacementOracle(draft, identities, versions.count, labels);
    assert.throws(() => replacementOracle({ ...draft, files: personal }, identities, versions.count, labels));
    assert.throws(() => replacementOracle(draft, [...identities, identities[0]], versions.count, labels));
    assert.throws(() => replacementOracle(draft, identities, 3, labels));
    assert.throws(() => replacementOracle(draft, identities, versions.count, ['production']));
    await page.screenshot({ path: join(root, 'shots', '04-replaced-source.png') });
    await page.goto(`/dashboard/skills/${id}/edit`);
    await publish(id, 3);
    for (const [number, expectedFiles, relation, revision] of [[2, hub, 'derived', 'v1'], [3, source, 'identical', 'v2']] as const) {
      // Viewed v2 must not silently export latest v3.
      await page.goto(`/dashboard/skills/${id}?version=${number}`);
      await page.getByRole('button', { name: 'Skill actions', exact: true }).click();
      await page.getByRole('menuitem', { name: 'Export with folder structure', exact: true }).click();
      await page.getByRole('button', { name: `Prepare v${number} migration export`, exact: true }).click();
      await expect(page.getByRole('button', { name: 'Download exact ZIP', exact: true })).toBeVisible({ timeout: UI_READY });
      const zipDownload = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Download exact ZIP', exact: true }).click();
      const zipPath = join(root, `version-${number}.zip`);
      await (await zipDownload).saveAs(zipPath);
      const receiptDownload = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Download mapping receipt', exact: true }).click();
      const receiptPath = join(root, `version-${number}-mapping.json`);
      await (await receiptDownload).saveAs(receiptPath);
      const archive = unzipSync(await readFile(zipPath));
      const receipt = JSON.parse(await readFile(receiptPath, 'utf8'));
      const version = await read<{ id: string; digest: string }>(`${id}/versions/${number}/`);
      const exportOracle = (entries: typeof archive, mapping: typeof receipt) => {
        assert.deepEqual(Object.keys(entries).sort(), ['.futureagi-skills.lock.json', `${name}/SKILL.md`].sort());
        assert.deepEqual(Buffer.from(entries[`${name}/SKILL.md`]), Buffer.from(expectedFiles[0].content));
        const lock = JSON.parse(Buffer.from(entries['.futureagi-skills.lock.json']).toString('utf8'));
        assert.equal(lock.skills[0].version_id, version.id);
        assert.equal(lock.skills[0].digest, skillDigest([...expectedFiles]));
        assert.equal(mapping.resource_id, id);
        assert.equal(mapping.version_id, version.id);
        assert.equal(mapping.digest, skillDigest([...expectedFiles]));
        assert.equal(mapping.mapping_receipts.length, 1);
        assert.equal(mapping.mapping_receipts[0].relation, relation);
        assert.equal(mapping.mapping_receipts[0].source_revision, revision);
        assert.equal(mapping.mapping_receipts[0].source_resource, 'retained-policy');
        assert.equal(mapping.exit.source_modified, false);
        assert.equal(mapping.exit.credentials_revoked, false);
      };
      exportOracle(archive, receipt);
      assert.throws(() => exportOracle({ ...archive, [`${name}/SKILL.md`]: Buffer.from('wrong bytes') }, receipt));
      assert.throws(() => exportOracle(archive, { ...receipt, version_id: 'wrong-version' }));
      assert.throws(() => exportOracle(archive, { ...receipt, mapping_receipts: [] }));
      await page.screenshot({ path: join(root, 'shots', `05-export-v${number}.png`) });
    }
    assert.deepEqual(await read(`${id}/labels/`), []);
    const evidence = { evidenceClass: 'synthetic fixture', completeJobQualified: false, id, mapping: second,
      checkpointCount: checkpoints.length, checkpointDigest: checkpoints[0].digest, replacementDigest: skillDigest(draft.files),
      publishedVersionsBeforeReplacementPublication: versions.count, exportedVersions: [2, 3], labels, fallibility: ['mapping-id', 'personal-draft', 'checkpoint-bytes', 'download-bytes', 'retry-key', 'replacement-bytes', 'duplicate-identity', 'publication-count', 'production-label', 'export-bytes', 'export-version', 'export-mapping'] };
    await writeFile(join(root, 'second-update.json'), JSON.stringify(evidence, null, 2));
    await testInfo.attach('j1-second-update-evidence', { contentType: 'application/json', body: JSON.stringify(evidence) });
  } finally { await context.close(); }
});
