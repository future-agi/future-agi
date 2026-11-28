import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { unzipSync } from 'fflate';
import { digest, requireValue, sha256 } from '../../../lib/skills-r3/canonical.mjs';

export const filesDigest = files => `sha256:${sha256(JSON.stringify(files.map(file => [file.path, sha256(file.content)]).sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0)))}`;

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
  const { input, imported, plans, output, operator, ledger, signal, expect, read, record, receipt, newPage, click, go } = tools;
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
    // Only navigate and switch to Source when the owner is not already there:
    // after publishing, the editor stays open in the Source view.
    const source = page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true });
    const onEditor = page.url().endsWith(`/dashboard/skills/${id}/edit`) && await source.count() > 0;
    if (!onEditor) await go(page, `/dashboard/skills/${id}/edit`, 'diverged-leaf-editor');
    if (!(onEditor && await source.getAttribute('aria-selected') === 'true')) await operator.click(source, 'source-view');
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
  // Retained-migration preview in the import dialog. The exchange format is the
  // preselected default; the owner opens the dialog unless already on the library.
  const preview = async page => {
    await go(page, '/dashboard/skills', 'second-migration-library');
    await operator.click(page.getByRole('button', { name: /^(Import a folder or ZIP|Import)$/ }), 'open-import');
    await click(page, 'tab', 'From another tool');
    await ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'owner', step: stage.id, phase: stage.phase, target: 'select-source-revision-two' }, () => page.getByLabel('Retained source files', { exact: true }).setInputFiles(input.update.exchange));
    const response = page.waitForResponse(res => res.url().endsWith('/intake/langfuse/preview/') && res.request().method() === 'POST');
    await click(page, 'button', 'Preview import');
    const result = await response;
    assert.equal(result.status(), 200);
    const plan = await read(`intake/${(await result.json()).result.id}/`, signal);
    await expect(page.getByRole('heading', { name: 'Review all items', exact: true })).toBeVisible();
    assert.deepEqual(plan.inventory.items.map(item => item.name).sort(), Object.keys(input.update.files).sort());
    for (const item of plan.inventory.items) assert.equal(item.destination_id, imported.find(member => member.name === item.name)?.result.resource_id);
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
    // The personal draft is an exception the batch cannot decide: it is held
    // out of the reviewed set until it is explicitly preserved and suspended.
    await expect(page.getByText('Your personal draft blocks replacement. Preserve it, then preview again.', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Import and publish 1 item', exact: true })).toBeDisabled();
    await page.screenshot({ path: resolve(output, 'shots', 'second-source-divergence.png'), fullPage: true });
    state.page = page;
    await receipt('api', { plan: state.plan, personal: state.personal });
    return true;
  }
  if (stage.id === 'preserve-draft') {
    requireValue(state.page && state.personal && state.plan, 'Blocked second-source preview must precede preservation');
    const page = state.page;
    const decision = { packet_sha256: digest(state.plan), preconditions_sha256: digest(state.personal) };
    await operator.recoveryDecision('preserve-before-source-switch', async () => {
      await operator.transition('draft-preservation', () => click(page, 'button', `Preserve draft for ${row.name}`));
      await expect(page.getByRole('heading', { name: 'Preserve your draft', exact: true })).toBeVisible();
      // Every personal edit is already saved on the server (asserted when it was
      // made), so the private checkpoint is the preservation; there are no
      // unsent edits to download.
      await operator.click(page.getByRole('checkbox', { name: 'Save a recovery checkpoint and suspend my current draft.', exact: true }), 'confirm-checkpoint-and-suspend');
      await click(page, 'button', 'Preserve and suspend draft');
      await expect(page).toHaveURL(/\/dashboard\/skills$/);
    }, decision);
    // Independent API read: the retained checkpoint is exactly the personal draft.
    const checkpoints = (await read(`${id}/draft/checkpoints/?page_size=100`, signal)).results;
    // The recovery digest binds the exact personal files; the editor may save the
    // same bytes again (a later revision) before suspension.
    state.checkpoint = checkpoints.find(item => item.reason === 'suspension' && item.digest === state.personal.recovery_digest && item.draft_revision >= state.personal.revision);
    requireValue(state.checkpoint && checkpoints.length === 1, 'The personal draft must be retained as exactly one private checkpoint');
    // Suspension returns to the same retained review: the immutable plan and
    // fingerprint are reloaded with live destination/draft state, no re-upload.
    await expect(page.getByRole('heading', { name: 'Review all items', exact: true })).toBeVisible();
    const fresh = await read(`intake/${state.plan.id}/`, signal);
    assert.equal(fresh.fingerprint, state.plan.fingerprint);
    const names = fresh.inventory.items.map(item => item.name);
    // The diverged item is an exception: the owner inspects Base/Source/Hub and
    // chooses replacement explicitly; the unchanged-Hub item has the safe default.
    await expect(page.getByText('Source and Hub both changed. Choose keep or replace; nothing is merged.', { exact: true })).toBeVisible();
    // The exception's Base/Source/Hub differences are already open in the row.
    await operator.review('base-source-hub-differences', names, [row.name], async () => {
      await expect(page.getByRole('table', { name: 'Retained migration items' }).getByText(/^SKILL\.md · /).first()).toBeVisible();
    }, { packet_sha256: digest(fresh), preconditions_sha256: digest(state.personal) });
    const decisionGroup = page.getByRole('radiogroup', { name: `Decision for ${row.name}`, exact: true });
    await expect(decisionGroup.getByRole('radio', { name: 'Replace with reviewed source', exact: true })).not.toBeChecked();
    await operator.click(decisionGroup.getByRole('radio', { name: 'Replace with reviewed source', exact: true }), 'choose-replace');
    await operator.click(page.getByRole('checkbox', { name: /^I confirm my rights to copy all 2 items/ }), 'confirm-source-rights');
    await ledger.measure({ dimension: 'A', primitive: 'approve', actor: 'owner', step: stage.id, phase: stage.phase, target: 'replace-and-publish-reviewed-set',
      items: names, exceptionItems: [row.name], decision: { packet_sha256: digest(fresh), preconditions_sha256: digest(fresh.fingerprint) } }, async () => {
      await page.getByRole('button', { name: 'Import and publish 2 items', exact: true }).click();
      await expect(page.getByText('2 versions published without labels. Production was not changed.', { exact: true })).toBeVisible({ timeout: 120_000 });
    });
    const receipts = await read(`intake/${fresh.id}/receipts/?page_size=100`, signal);
    assert.equal(receipts.count, fresh.inventory.items.length);
    assert.equal(new Set(receipts.results.map(item => item.result.resource_id)).size, receipts.count);
    const updated = [];
    for (const item of fresh.inventory.items) {
      const versions = await read(`${item.destination_id}/versions/`, signal);
      assert.equal(versions.count, item.destination_id === id ? 3 : 2);
      assert.equal(versions.results.find(version => version.number === versions.count).digest, filesDigest(input.update.files[item.name]));
      assert.deepEqual(await read(`${item.destination_id}/labels/`, signal), []);
      updated.push({ id: item.destination_id, name: item.name });
    }
    await page.screenshot({ path: resolve(output, 'shots', 'second-source-published.png'), fullPage: true });
    state.updated = updated;
    state.fresh = fresh;
    state.receipts = receipts;
    await receipt('api', { fresh, updated, receipts, personal_checkpoint: { id: state.checkpoint.id, digest: state.checkpoint.digest } }, decision);
    return true;
  }
  requireValue(state.updated?.length && state.checkpoint, 'Reviewed update and preserved personal draft must precede export');
  // One export of every linked skill's current version from the exact context,
  // each nested archive byte-identical to its single-version export.
  const contextId = tools.contextId();
  requireValue(contextId, 'Published pilot context must precede export');
  const page = await newPage();
  await go(page, `/dashboard/skills/contexts/${contextId}?version=1`, 'exact-context-exit');
  await click(page, 'tab', 'Install');
  const download = page.waitForEvent('download');
  await click(page, 'button', 'Download 14 linked skills');
  const bundlePath = resolve(output, 'migration-export.zip');
  await (await download).saveAs(bundlePath);
  const bundle = unzipSync(await readFile(bundlePath));
  const summary = JSON.parse(Buffer.from(bundle['migration-export.json']).toString('utf8'));
  assert.equal(summary.items.length, 14);
  const exports = [];
  for (const member of imported) {
    const destination = member.result.resource_id;
    const wasUpdated = state.updated.some(item => item.id === destination);
    const number = destination === id ? 3 : wasUpdated ? 2 : 1;
    const expected = wasUpdated ? input.update.files[member.name] : input.files[member.name];
    const entry = summary.items.find(item => item.resource_id === destination);
    requireValue(entry && entry.version === number, 'Export must contain the current version of every linked skill');
    const archive = Buffer.from(bundle[entry.archive]);
    const mapping = JSON.parse(Buffer.from(bundle[entry.mapping]).toString('utf8'));
    await writeFile(resolve(output, `export-${exports.length}.zip`), archive, { mode: 0o600 });
    await writeFile(resolve(output, `export-${exports.length}-mapping.json`), JSON.stringify(mapping, null, 2), { mode: 0o600 });
    const version = await read(`${destination}/versions/${number}/`, signal);
    const original = plans.flatMap(plan => plan.plan.inventory.items).find(item => item.name === member.name);
    requireValue(original, 'Original retained source identity must be available');
    const manifest = assertMigrationExport({ archive, mapping, version, row: member, files: expected,
      source: { relation: 'identical', source_revision: wasUpdated ? 'v2' : 'v1' } });
    exports.push({ destination, version, mapping, manifest });
  }
  state.exitPage = page;
  await page.screenshot({ path: resolve(output, 'shots', 'pilot-export.png'), fullPage: true });
  state.exports = exports;
  await record('roundtrip-manifest', exports.flatMap(item => item.manifest));
  await receipt('filesystem', exports);
  return true;
}
