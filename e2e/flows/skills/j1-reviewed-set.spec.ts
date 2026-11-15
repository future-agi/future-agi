import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { strToU8, unzipSync, zipSync } from 'fflate';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';
import { skillDigest } from '../../lib/skills-consumption';

type File = { path: string; content: string };
type Item = { key: string; name: string; blockers: unknown[] };
type Plan = { id: string; fingerprint: string; inventory: { items: Item[] } };
type Receipt = { item_key: string; result: { resource_id: string } };
type Version = { number: number; digest: string };
type Envelope<T> = { status: boolean; result: T };
type ExportSummary = { items: { resource_id: string; version: number; digest: string; archive: string; mapping: string }[] };

// ROUND3-PLAN J1 steps 2–3 and 6 as one reviewed set: a retained-intake and
// export subflow, deliberately NOT the complete 15-member cold/configured job.
// Wire/locators: RetainedMigrationPanel, MigrationBatchReview, ContextImportDialog,
// ContextLinksPanel, ContextMigrationExport; r3/import_plans.py, drafts.publish_draft,
// migration_receipts.export_migrated_version.
const UI_READY = 60_000;
test('SKILLS-E2E-012: review, import and publish a retained set in one decision, link it and export it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-012', area: 'skills',
    userGoal: 'Trial a private library as one reviewed decision with the unsupported member explicitly left out, then link the published set into a context and leave with one export',
    steps: ['preview a versioned exchange with three supported members and a private ZIP whose only member contains a binary, in one import session',
      'acknowledge the left-out member, confirm rights once and import and publish the three items in one decision while the first import response is lost; acknowledge the held publication warnings explicitly',
      'create an agent context from the published set; the pinned links are proposed and previewed, then explicitly accepted',
      'publish the context, then export every linked skill with its mapping receipt from the Use tab in one download'],
    backendChecks: ['exactly one receipt per supported member and none for the blocked member; the lost response was replayed with the same body and operation key',
      'each supported member has exactly one published version whose digest equals its source bytes and no release label',
      'the context version links exactly the three skills pinned at v1',
      'each nested export archive is byte-identical to the single-version export and its mapping receipt binds the same immutable version'],
  }),
}, async ({ browser, scopeActors }, testInfo) => {
  test.setTimeout(900_000); // 8 UI stages × 60s, one 180s batch, boot/cleanup 240s.
  const actor = scopeActors.ownerB;
  const root = process.env.J1_SET_EVIDENCE_DIR || testInfo.outputPath('j1-set');
  await mkdir(join(root, 'shots'), { recursive: true });
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  await context.addInitScript(authInitScript, { access: actor.tokens.access, refresh: actor.tokens.refresh, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await context.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), actor.organizationId);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const prefix = `j1-set-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const supported = Array.from({ length: 3 }, (_, index) => `${prefix}-${index}`);
  const unsupported = `${prefix}-binary`;
  const files = new Map<string, File[]>();
  for (const name of [...supported, unsupported]) {
    files.set(name, [{ path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Synthetic reviewed-set migration policy\n---\n\n# Policy\nKeep exact café text.\n` },
      { path: 'references/policy.md', content: 'Private reference\r\n' }]);
  }
  const exchange = { format: 'langfuse-skills-v1', origin: 'synthetic.invalid', project: prefix,
    skills: supported.map((name, index) => ({ resource: `resource-${index}`, revision: 'v1', digest: skillDigest(files.get(name)!), files: files.get(name)! })) };
  const entries: Record<string, Uint8Array> = {};
  files.get(unsupported)!.forEach(file => { entries[`${unsupported}/${file.path}`] = strToU8(file.content); });
  entries[`${unsupported}/assets/example.bin`] = new Uint8Array([0, 255, 0, 1]);
  const read = async <T>(path: string): Promise<T> => (await actor.api.get<Envelope<T>>(path)).result;
  const attempts: { body: string | null; key: string | undefined }[] = [];
  let dropped = false;
  try {
    await page.goto('/dashboard/skills');
    await page.getByRole('button', { name: /^(Import a folder or ZIP|Import)$/ }).click();
    await page.getByRole('tab', { name: 'From another tool', exact: true }).click();
    await page.getByLabel('Retained source files', { exact: true }).setInputFiles({ name: 'exchange.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(exchange)) });
    const exchangeResponse = page.waitForResponse(response => response.url().endsWith('/skills/intake/langfuse/preview/') && response.status() === 200);
    await page.getByRole('button', { name: 'Preview import', exact: true }).click();
    const plan = await read<Plan>(`/model-hub/skills/intake/${((await (await exchangeResponse).json()).result as Plan).id}/`);
    await page.getByRole('button', { name: 'Change sources', exact: true }).click();
    await page.getByRole('combobox', { name: 'Source', exact: true }).click();
    await page.getByRole('option', { name: 'Folder', exact: true }).click();
    await page.getByLabel('Retained source files', { exact: true }).setInputFiles({ name: 'private-library.zip', mimeType: 'application/zip', buffer: Buffer.from(zipSync(entries, { level: 0 })) });
    const zipResponse = page.waitForResponse(response => response.url().endsWith('/skills/intake/preview/') && response.status() === 200);
    await page.getByRole('button', { name: 'Preview import', exact: true }).click();
    const blockedPlan = await read<Plan>(`/model-hub/skills/intake/${((await (await zipResponse).json()).result as Plan).id}/`);
    assert.equal(blockedPlan.inventory.items.length, 1);
    assert.ok(blockedPlan.inventory.items[0].blockers.length > 0);
    await expect(page.getByRole('heading', { name: 'Review all items', exact: true })).toBeVisible();
    await expect(page.getByRole('table', { name: 'Retained migration items' }).getByRole('row')).toHaveCount(5);
    await page.screenshot({ path: join(root, 'shots', '01-reviewed-set.png'), fullPage: true });

    await page.route('**/skills/intake/*/accept/', async route => {
      const request = route.request();
      if (!dropped || attempts.length === 1) attempts.push({ body: request.postData(), key: request.headers()['idempotency-key'] });
      if (!dropped) {
        const response = await route.fetch();
        assert.equal(response.status(), 200);
        dropped = true;
        await route.abort('failed');
      } else await route.continue();
    });
    const submit = page.getByRole('button', { name: 'Import and publish 3 items', exact: true });
    await page.getByRole('checkbox', { name: /^I confirm my rights to copy all 3 items/ }).check();
    // The blocked member is an exception the owner must acknowledge explicitly.
    await expect(submit).toBeDisabled();
    await page.getByRole('checkbox', { name: /^Leave out 1 skill/ }).check();
    await submit.click();
    // This fixture's descriptions raise publication warnings: the three imported
    // drafts are held until the owner acknowledges them explicitly; imports are not repeated.
    const acknowledgeWarnings = page.getByRole('checkbox', { name: 'I reviewed the publication warnings for 3 items and want to publish them.', exact: true });
    await expect(acknowledgeWarnings).toBeVisible({ timeout: 180_000 });
    await expect(page.getByRole('button', { name: 'Publish 3 items with reviewed warnings', exact: true })).toBeDisabled();
    await acknowledgeWarnings.check();
    await page.getByRole('button', { name: 'Publish 3 items with reviewed warnings', exact: true }).click();
    await expect(page.getByText('3 versions published without labels. Production was not changed.', { exact: true })).toBeVisible({ timeout: 180_000 });
    await expect(page.getByText(/lost response settled by exact replay/)).toHaveCount(1);
    await page.screenshot({ path: join(root, 'shots', '02-published-set.png'), fullPage: true });

    const receipts = await read<{ count: number; results: Receipt[] }>(`/model-hub/skills/intake/${plan.id}/receipts/?page_size=100`);
    const blockedKey = blockedPlan.inventory.items[0].key;
    assert.equal((await read<{ count: number }>(`/model-hub/skills/intake/${blockedPlan.id}/receipts/`)).count, 0);
    const receiptOracle = (value: typeof receipts, requests: typeof attempts) => {
      assert.equal(value.count, 3);
      assert.equal(new Set(value.results.map(row => row.item_key)).size, 3);
      assert.equal(value.results.some(row => row.item_key === blockedKey), false);
      assert.equal(requests.length, 2);
      assert.ok(requests[0].key);
      assert.deepEqual(requests[0], requests[1]);
    };
    receiptOracle(receipts, attempts);
    assert.throws(() => receiptOracle(receipts, [attempts[0], { ...attempts[1], key: 'different-operation' }]));
    assert.throws(() => receiptOracle({ ...receipts, results: [...receipts.results.slice(1), { ...receipts.results[0], item_key: blockedKey }] }, attempts));
    const leaves: { id: string; name: string; digest: string }[] = [];
    for (const row of receipts.results) {
      const name = plan.inventory.items.find(item => item.key === row.item_key)!.name;
      const versions = await read<{ count: number; results: Version[] }>(`/model-hub/skills/${row.result.resource_id}/versions/`);
      const labels = await read<unknown[]>(`/model-hub/skills/${row.result.resource_id}/labels/`);
      const versionOracle = (value: typeof versions, released: unknown[]) => {
        assert.equal(value.count, 1);
        assert.equal(value.results[0].digest, skillDigest(files.get(name)!));
        assert.deepEqual(released, []);
      };
      versionOracle(versions, labels);
      assert.throws(() => versionOracle({ ...versions, results: [{ ...versions.results[0], digest: skillDigest([{ path: 'SKILL.md', content: 'x' }]) }] }, labels));
      assert.throws(() => versionOracle(versions, [{ name: 'production' }]));
      leaves.push({ id: row.result.resource_id, name, digest: versions.results[0].digest });
    }

    await page.getByRole('button', { name: 'Create agent context from these skills', exact: true }).click();
    await expect(page.getByText(/3 published skills from your migration will be proposed/)).toBeVisible();
    await page.getByLabel('AGENTS.md', { exact: true }).fill('# Reviewed set pilot\nUse the explicitly linked policies.\n');
    await page.getByLabel('Immutable name', { exact: true }).fill(`${prefix}-context`);
    await page.getByLabel('Purpose', { exact: true }).fill('Synthetic reviewed-set trial');
    const created = page.waitForResponse(res => res.url().endsWith('/contexts/') && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Create draft', exact: true }).click();
    const createdResponse = await created;
    assert.equal(createdResponse.status(), 201);
    const createdBody = (await createdResponse.json()).result;
    const contextId: string = createdBody.context_id ?? createdBody.context?.id;
    assert.ok(contextId);
    await expect(page.getByText(/3 skills from your migration are proposed/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Copy full Compiled candidate', exact: true })).toBeVisible();
    const apply = page.getByRole('button', { name: 'Apply selected updates to draft', exact: true });
    await expect(apply).toBeDisabled();
    await page.getByRole('checkbox', { name: 'Accept these skill versions and update rules into 1 context draft.', exact: true }).check();
    await apply.click();
    await expect(page.getByRole('heading', { name: 'Linked skills (3)', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Review changes', exact: true }).click();
    await expect(page.getByText('What publishing creates', { exact: true })).toBeVisible();
    const reviewCandidate = async () => {
      const reviewed = page.getByRole('checkbox', { name: 'I reviewed these changes against the current published version.', exact: true });
      if (!(await reviewed.isChecked())) await reviewed.check();
      const warning = page.getByRole('checkbox', { name: 'I acknowledge the static warnings for this exact candidate.', exact: true });
      if (await warning.count() && !(await warning.isChecked())) await warning.check();
    };
    await reviewCandidate();
    const acknowledge = page.getByRole('button', { name: 'Confirm review', exact: true });
    if (await acknowledge.count()) {
      await acknowledge.click();
      await expect(page.getByRole('button', { name: 'Publish version', exact: true })).toBeVisible();
      await reviewCandidate();
    }
    const published = page.waitForResponse(res => res.url().endsWith(`/contexts/${contextId}/draft/publish/`) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Publish version', exact: true }).click();
    assert.equal((await published).status(), 201);
    const inspection = await read<{ descriptor: { links: { skill_id: string; version: number; requested: { version?: number } }[] } }>(`/model-hub/skills/contexts/${contextId}/versions/1/inspection/`);
    const linkOracle = (links: typeof inspection.descriptor.links) => {
      assert.deepEqual(links.map(link => link.skill_id).sort(), leaves.map(leaf => leaf.id).sort());
      assert.ok(links.every(link => link.version === 1 && link.requested.version === 1));
    };
    linkOracle(inspection.descriptor.links);
    assert.throws(() => linkOracle(inspection.descriptor.links.slice(1)));

    await page.goto(`/dashboard/skills/contexts/${contextId}?version=1`);
    await page.getByRole('tab', { name: 'Install', exact: true }).click();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download 3 linked skills', exact: true }).click();
    const bundlePath = join(root, 'migration-export.zip');
    await (await download).saveAs(bundlePath);
    await page.screenshot({ path: join(root, 'shots', '03-export.png'), fullPage: true });
    const bundle = unzipSync(await readFile(bundlePath));
    const summary = JSON.parse(Buffer.from(bundle['migration-export.json']).toString('utf8')) as ExportSummary;
    for (const leaf of leaves) {
      const single = await read<{ archive_base64: string; version_id: string; digest: string; mapping_receipts: unknown[] }>(`/model-hub/skills/${leaf.id}/versions/1/migration-export/`);
      const entry = summary.items.find(item => item.resource_id === leaf.id);
      const exportOracle = (archive: Uint8Array | undefined, mapping: { version_id: string; digest: string; mapping_receipts: unknown[] }) => {
        assert.ok(entry && entry.version === 1 && entry.digest === leaf.digest);
        assert.deepEqual(Buffer.from(archive ?? []), Buffer.from(single.archive_base64, 'base64'));
        assert.equal(mapping.version_id, single.version_id);
        assert.equal(mapping.digest, leaf.digest);
        assert.equal(mapping.mapping_receipts.length, 1);
      };
      const mapping = JSON.parse(Buffer.from(bundle[entry!.mapping]).toString('utf8'));
      exportOracle(bundle[entry!.archive], mapping);
      assert.throws(() => exportOracle(new Uint8Array([0x50, 0x4b]), mapping));
      assert.throws(() => exportOracle(bundle[entry!.archive], { ...mapping, mapping_receipts: [] }));
    }
    assert.equal(summary.items.length, 3);
    await writeFile(join(root, 'reviewed-set-observations.json'), JSON.stringify({ scope: 'J1 reviewed-set subflow only', plan: plan.id, receipts: receipts.count, lostResponseAttempts: attempts.length, contextId, exported: summary.items, evidenceClass: 'synthetic fixture', completeJobQualified: false }, null, 2));
  } finally { await context.close(); }
});
