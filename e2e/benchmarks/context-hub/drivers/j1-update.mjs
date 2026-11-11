import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { unzipSync } from 'fflate';
import { digest, requireValue, sha256 } from '../../../lib/skills-r3/canonical.mjs';

const filesDigest = files => `sha256:${sha256(JSON.stringify(files.map(file => [file.path, sha256(file.content)]).sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0)))}`;

/** Compare downloaded bytes AND mapping identity with an independently read
 * immutable version. A ZIP header or UI success toast is never an oracle. */
export function assertMigrationExport({ archive, mapping, version, row, files, source }) {
  const entries = unzipSync(archive);
  assert.deepEqual(Object.keys(entries).sort(), ['.futureagi-skills.lock.json', ...files.map(file => `${row.name}/${file.path}`)].sort());
  for (const file of files) assert.deepEqual(Buffer.from(entries[`${row.name}/${file.path}`]), Buffer.from(file.content));
  const lock = JSON.parse(Buffer.from(entries['.futureagi-skills.lock.json']).toString('utf8'));
  assert.equal(lock.skills.length, 1);
  assert.equal(lock.skills[0].version_id, version.id);
  assert.equal(lock.skills[0].digest, filesDigest(files));
  assert.equal(mapping.resource_id, row.result.resource_id);
  assert.equal(mapping.version_id, version.id);
  assert.equal(mapping.digest, version.digest);
  assert.equal(mapping.digest, filesDigest(files));
  assert.equal(mapping.mapping_receipts.length, 1);
  for (const [key, value] of Object.entries(source)) assert.equal(mapping.mapping_receipts[0][key], value);
  assert.equal(mapping.exit.source_modified, false);
  // An export alone must not claim that the credential was revoked.
  assert.equal(mapping.exit.credentials_revoked, false);
  return files.map(file => ({ path: `${row.name}/${file.path}`, sha256: sha256(file.content), size_bytes: Buffer.byteLength(file.content) }));
}

/** Later migration stages of the SAME driver. These do not skip installation or
 * establish native delivery. The registered runner must reach them in order;
 * isolated tests of this helper remain explicitly prerequisite-only. */
export async function performUpdate(stage, tools, state) {
  const { input, imported, plans, output, operator, ledger, signal, expect, read, record, receipt, newPage, click, choose, go } = tools;
  if (!['second-import', 'preserve-draft', 'export'].includes(stage.id)) return false;
  requireValue(input.update?.exchange && input.update?.name && input.update?.files, 'Preregistered synthetic source revision two is required');
  const row = imported.find(item => item.name === input.update.name);
  requireValue(row && imported.length === 14, 'Complete initial supported intake must precede second update');
  const id = row.result.resource_id;
  const publish = async (page, destination, expectedNumber) => {
    await click(page, 'button', 'Publish');
    const dialog = page.getByRole('dialog');
    const warnings = dialog.getByRole('checkbox', { name: 'I reviewed the warnings and want to publish this version.', exact: true });
    await expect.poll(async () => await warnings.count() > 0 || await dialog.getByRole('button', { name: 'Publish', exact: true }).isEnabled()).toBeTruthy();
    if (await warnings.count()) await operator.click(warnings, 'acknowledge-publication-warning');
    await expect(dialog.getByRole('checkbox', { name: 'production', exact: true })).not.toBeChecked();
    await expect(dialog.getByRole('checkbox', { name: 'staging', exact: true })).not.toBeChecked();
    const response = page.waitForResponse(res => res.url().endsWith(`/${destination}/draft/publish/`) && res.request().method() === 'POST');
    await operator.click(dialog.getByRole('button', { name: 'Publish', exact: true }), 'publish-reviewed-update');
    assert.equal((await response).status(), 201);
    await expect(dialog).not.toBeVisible();
    assert.equal((await read(`${destination}/versions/`, signal)).count, expectedNumber);
    assert.deepEqual(await read(`${destination}/labels/`, signal), []);
  };
  const edit = async (page, files) => {
    await go(page, `/dashboard/skills/${id}/edit`, 'diverged-leaf-editor');
    await operator.click(page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true }), 'source-view');
    const root = files.find(file => file.path === 'SKILL.md');
    requireValue(root, 'Explicit SKILL.md revision required');
    await ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'owner', step: stage.id, phase: stage.phase, target: 'edit-policy-source' }, async () => {
      const editor = page.getByRole('textbox', { name: /Editor content/ });
      await editor.focus();
      await editor.press('Control+a');
      await page.evaluate(text => navigator.clipboard.writeText(text), root.content);
      await editor.press('Control+v');
    });
    await expect(async () => assert.deepEqual((await read(`${id}/draft/`, signal)).files, files)).toPass({ timeout: 60_000 });
  };
  const preview = async page => {
    await go(page, '/dashboard/skills', 'second-migration-library');
    await operator.click(page.getByRole('button', { name: /^(Import a folder or ZIP|Import)$/ }), 'open-import');
    await click(page, 'tab', 'Retained migration');
    await choose(page, 'Retained source format', 'Versioned Langfuse exchange JSON');
    await ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'owner', step: stage.id, phase: stage.phase, target: 'select-source-revision-two' }, () => page.getByLabel('Retained source files', { exact: true }).setInputFiles(input.update.exchange));
    const response = page.waitForResponse(res => res.url().endsWith('/intake/langfuse/preview/') && res.request().method() === 'POST');
    await click(page, 'button', 'Preview retained migration');
    const result = await response;
    assert.equal(result.status(), 200);
    const plan = await read(`intake/${(await result.json()).result.id}/`, signal);
    await expect(page.getByRole('heading', { name: 'Review retained migration plan', exact: true })).toBeVisible();
    assert.deepEqual(plan.inventory.items.map(item => item.name).sort(), Object.keys(input.update.files).sort());
    for (const item of plan.inventory.items) assert.equal(item.destination_id, imported.find(member => member.name === item.name)?.result.resource_id);
    await choose(page, 'Review item', row.name);
    return plan;
  };
  if (stage.id === 'second-import') {
    const page = await newPage();
    await edit(page, input.update.hub);
    await publish(page, id, 2);
    await edit(page, input.update.personal);
    ledger.fault('hub-divergence', 'published-hub-change-and-personal-draft');
    state.plan = await preview(page);
    const changed = state.plan.inventory.items.filter(item => item.migration.hub_diverged);
    assert.deepEqual(changed.map(item => item.destination_id), [id]);
    state.personal = await read(`${id}/draft/`, signal);
    assert.deepEqual(state.personal.files, input.update.personal);
    await choose(page, 'Source update decision', 'Replace with reviewed source');
    await expect(page.getByRole('button', { name: 'Import reviewed item as draft', exact: true })).toBeDisabled();
    await expect(page.getByText('Source and Hub both changed. Replacement is not a semantic merge; review both differences.', { exact: true })).toBeVisible();
    await page.screenshot({ path: resolve(output, 'shots', 'second-source-divergence.png') });
    state.page = page;
    await receipt('api', { plan: state.plan, personal: state.personal });
    return true;
  }
  if (stage.id === 'preserve-draft') {
    requireValue(state.page && state.personal && state.plan, 'Blocked second-source preview must precede preservation');
    const page = state.page;
    const decision = { packet_sha256: digest(state.plan), preconditions_sha256: digest(state.personal) };
    await operator.recoveryDecision('preserve-before-source-switch', async () => {
      await operator.transition('draft-preservation', () => click(page, 'button', 'Preserve draft'));
      await expect(page.getByRole('heading', { name: 'Preserve your draft', exact: true })).toBeVisible();
      const download = page.waitForEvent('download');
      await click(page, 'button', 'Download unsent edits');
      const path = resolve(output, 'personal-draft.json');
      await (await download).saveAs(path);
      state.downloaded = JSON.parse(await readFile(path, 'utf8'));
      assert.deepEqual(state.downloaded.files, input.update.personal);
      await operator.click(page.getByRole('checkbox', { name: 'Save a recovery checkpoint and suspend my current draft.', exact: true }), 'confirm-checkpoint-and-suspend');
      await click(page, 'button', 'Preserve and suspend draft');
      await expect(page).toHaveURL(/\/dashboard\/skills$/);
    }, decision);
    const fresh = await preview(page);
    const updated = [];
    for (const item of fresh.inventory.items) {
      await choose(page, 'Review item', item.name);
      await choose(page, 'Source update decision', 'Replace with reviewed source');
      await operator.click(page.getByRole('checkbox', { name: /I have reviewed source rights/ }), 'review-source-rights');
      await operator.click(page.getByRole('checkbox', { name: /Apply this exact decision/ }), 'confirm-source-update');
      const response = page.waitForResponse(res => res.url().endsWith(`/intake/${fresh.id}/accept/`) && res.request().method() === 'POST');
      await click(page, 'button', 'Import reviewed item as draft');
      assert.equal((await response).status(), 200);
      await expect(page.getByText(/Decision recorded for this item/)).toBeVisible();
      const draft = await read(`${item.destination_id}/draft/`, signal);
      assert.deepEqual(draft.files, input.update.files[item.name]);
      updated.push({ id: item.destination_id, name: item.name, draft });
    }
    const receipts = await read(`intake/${fresh.id}/receipts/?page_size=100`, signal);
    assert.equal(receipts.count, updated.length);
    assert.equal(new Set(receipts.results.map(item => item.result.resource_id)).size, updated.length);
    for (const item of updated) {
      await go(page, `/dashboard/skills/${item.id}/edit`, 'source-replacement-editor');
      await publish(page, item.id, item.id === id ? 3 : 2);
    }
    state.updated = updated;
    state.fresh = fresh;
    state.receipts = receipts;
    await receipt('api', { fresh, updated, receipts, personal_download_sha256: digest(state.downloaded) }, decision);
    return true;
  }
  requireValue(state.updated?.length && state.downloaded, 'Reviewed update and preserved personal draft must precede export');
  const exports = [];
  for (const member of imported) {
    const page = await newPage();
    const destination = member.result.resource_id;
    const wasUpdated = state.updated.some(item => item.id === destination);
    const number = destination === id ? 3 : wasUpdated ? 2 : 1;
    const expected = wasUpdated ? input.update.files[member.name] : input.files[member.name];
    await operator.reconcile('export-destination-identity', () => go(page, `/dashboard/skills/${destination}?version=${number}`, 'view-exact-export-version'));
    await click(page, 'button', `Prepare v${number} migration export`);
    await expect(page.getByRole('button', { name: 'Download exact ZIP', exact: true })).toBeVisible();
    const archiveDownload = page.waitForEvent('download');
    await click(page, 'button', 'Download exact ZIP');
    const archivePath = resolve(output, `export-${exports.length}.zip`);
    await (await archiveDownload).saveAs(archivePath);
    const mappingDownload = page.waitForEvent('download');
    await click(page, 'button', 'Download mapping receipt');
    const mappingPath = resolve(output, `export-${exports.length}-mapping.json`);
    await (await mappingDownload).saveAs(mappingPath);
    const mapping = JSON.parse(await readFile(mappingPath, 'utf8'));
    const version = await read(`${destination}/versions/${number}/`, signal);
    const original = plans.flatMap(plan => plan.plan.inventory.items).find(item => item.name === member.name);
    requireValue(original, 'Original retained source identity must be available');
    const manifest = assertMigrationExport({ archive: await readFile(archivePath), mapping, version, row: member, files: expected,
      source: { relation: 'identical', source_revision: wasUpdated ? 'v2' : 'v1' } });
    // Cross-check initial/updated source identity against the original plan, not
    // a name-derived replacement. The v2 preview separately binds destinations.
    exports.push({ destination, version, mapping, manifest });
    await page.close();
  }
  state.exports = exports;
  await record('roundtrip-manifest', exports.flatMap(item => item.manifest));
  await receipt('filesystem', exports);
  return true;
}
