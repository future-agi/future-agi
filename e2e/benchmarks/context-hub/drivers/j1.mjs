import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { chromium, expect as playwrightExpect } from '@playwright/test';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue } from '../../../lib/skills-r3/canonical.mjs';

const expect = playwrightExpect.configure({ timeout: 60_000 });

/** Live J1 attempt. J1_DRIVER_INPUT is private VM-local configuration. No writes
 * use an API fixture as a substitute for public UI/CLI. Not a qualification
 * shortcut: the installed product's unavailable delivery handoff fails closed.
 * Input owns two synthetic sources (versioned exchange + private folder), an
 * explicitly authored AGENTS.md, and an isolated pre-existing account. A cold
 * run inventories before opening a product session or issuing any credential.
 */
export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J1', 'J1 requires the approved cloud Linux host');
  requireValue(process.env.J1_DRIVER_INPUT, 'Private cloud-local J1 input is required');
  const input = JSON.parse(await readFile(process.env.J1_DRIVER_INPUT, 'utf8'));
  for (const key of ['appUrl', 'apiUrl']) requireValue(new URL(input[key]).hostname === 'localhost', 'Use only the owned full stack');
  const output = resolve(input.outputDir, runId);
  await mkdir(resolve(output, 'shots'), { recursive: true });
  let browser;
  let context;
  let inventory;
  let contextId;
  let candidate;
  let lastPage;
  const plans = [];
  const imported = [];
  const attempts = [];
  const headers = { Authorization: `Bearer ${input.actor.access}`, 'X-Organization-Id': input.actor.organizationId, 'X-Workspace-Id': input.actor.workspaceId };
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), JSON.stringify(value, null, 2), { mode: 0o600 });
    return digest(value);
  };
  const read = async (path, signal) => {
    const response = await fetch(`${input.apiUrl}/model-hub/skills/${path}`, { headers, signal });
    requireValue(response.ok, 'Independent API observation unavailable');
    return (await response.json()).result;
  };
  const open = async () => {
    if (context) return;
    browser = await chromium.launch({ headless: true });
    context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 } });
    await context.addInitScript(actor => {
      localStorage.setItem('accessToken', actor.access);
      localStorage.setItem('refreshToken', actor.refresh);
      localStorage.setItem('rememberMe', 'true');
      sessionStorage.setItem('organizationId', actor.organizationId);
      sessionStorage.setItem('workspaceId', actor.workspaceId);
      sessionStorage.setItem('workspaceOrgId', actor.organizationId);
    }, input.actor);
  };
  const newPage = async () => {
    await open();
    const page = await context.newPage();
    page.setDefaultTimeout(60_000);
    lastPage = page;
    return page;
  };
  const baseArgs = ['--folder', input.folder, '--langfuse', input.exchange, '--agents', input.agents,
    '--name', input.contextName, '--description', input.description,
    '--source-origin', 'local:synthetic-j1', '--source-project', input.sourceProject, '--source-revision', 'v1'];
  const local = async (operator, selected) => {
    const result = await operator.command('local-library-inventory', 'bash', [resolve(input.productRoot, 'bin/skills-inventory'), ...baseArgs,
      ...(selected ? selected.flatMap(key => ['--select', key]) : [])], {
      cwd: input.productRoot, env: { PATH: process.env.PATH, SKILLS_INVENTORY_PYTHON: input.python },
    });
    return JSON.parse(result.stdout);
  };
  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      const operator = new MeasuredOperator({ ledger, actor: 'owner', step: stage.id, phase: stage.phase, signal });
      const click = (page, role, name) => operator.click(page.getByRole(role, { name, exact: true }), name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-$/, ''));
      const choose = async (page, label, value) => {
        // The existing context-link fields render visible labels without an
        // accessible combobox name. Locate their visible FormControl, as a
        // mouse user does; this does not qualify screen-reader accessibility.
        const field = ['Update policy', 'Published leaf skill'].includes(label)
          ? page.locator('.MuiFormControl-root').filter({ has: page.getByText(label, { exact: true }) }).getByRole('combobox')
          : page.getByRole('combobox', { name: label, exact: true });
        await operator.click(field, 'choose-field');
        await operator.click(page.getByRole('option', { name: value, exact: true }), 'select-option');
      };
      const go = (page, path, target) => operator.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: 'owner', step: stage.id, phase: stage.phase, target }, () => page.goto(path)));
      const receipt = async (source, value, decision = undefined) => ledger.stage({ step: stage.id, phase: stage.phase, source, artifactHash: await record(stage.id, value), actionSequences: [], ...(decision ? { decision } : {}) });
      const abort = () => { void context?.close().catch(() => {}); };
      signal.addEventListener('abort', abort, { once: true });
      try {
        if (stage.id === 'inventory') {
          inventory = await local(operator);
          assert.equal(inventory.source_member_count, 15);
          assert.equal(inventory.complete_inventory, true);
          assert.equal(inventory.compiled_closure.fits, false);
          assert.equal(inventory.members.filter(member => member.blockers.length).length, 1);
          assert.deepEqual(inventory.members.map(member => member.name).sort(), [...input.names].sort());
          ledger.fault('unsupported-member', 'binary-member');
          await receipt('filesystem', inventory);
          return;
        }
        if (stage.id === 'choose-subset') {
          requireValue(inventory, 'Inventory must precede subset selection');
          const selected = inventory.members.filter(member => !member.blockers.length).map(member => member.key);
          const decision = { packet_sha256: digest(inventory), preconditions_sha256: digest(inventory.members.map(member => member.manifest_digest)) };
          await operator.review('supported-subset', inventory.members.map((member, i) => member.blockers.length ? 'binary-member' : `member-${i}`), ['binary-member'], async () => {
            assert.equal(selected.length, 14);
          }, decision);
          inventory = await local(operator, selected);
          assert.equal(inventory.compiled_closure.fits, true);
          assert.equal(inventory.excluded.length, 1);
          await receipt('filesystem', inventory, decision);
          return;
        }
        if (stage.id === 'migration-preview') {
          if (!inventory) {
            // Configured runs still measure any fresh preflight; a prior report
            // cannot be silently treated as an action-free current inventory.
            inventory = await local(operator, input.selected);
            assert.equal(inventory.compiled_closure.fits, true);
            ledger.fault('unsupported-member', 'binary-member');
          }
          for (const kind of ['exchange', 'folder']) {
            const page = await newPage();
            await go(page, '/dashboard/skills', 'migration-library');
            await operator.click(page.getByRole('button', { name: /^(Import a folder or ZIP|Import)$/ }), 'open-import');
            await click(page, 'tab', 'Retained migration');
            await choose(page, 'Retained source format', kind === 'exchange' ? 'Versioned Langfuse exchange JSON' : 'Private folder or ZIP');
            await ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'owner', step: stage.id, phase: stage.phase, target: 'select-source-input' }, () => page.getByLabel(kind === 'exchange' ? 'Retained source files' : 'Retained private folder', { exact: true }).setInputFiles(kind === 'exchange' ? input.exchange : input.folder));
            if (kind === 'folder') await operator.fill(page.getByLabel('Immutable source identities JSON', { exact: true }), JSON.stringify(inventory.folder_upload_source_identities), 'source-identity-map');
            const preview = page.waitForResponse(response => response.url().includes('/intake/') && response.url().endsWith('/preview/') && response.request().method() === 'POST');
            await click(page, 'button', 'Preview retained migration');
            const response = await preview;
            assert.equal(response.status(), 200);
            const result = (await response.json()).result;
            const plan = await read(`intake/${result.id}/`, signal);
            assert.equal(plan.id, result.id);
            await expect(page.getByRole('heading', { name: 'Review retained migration plan' })).toBeVisible();
            plans.push({ kind, page, plan });
            await page.screenshot({ path: resolve(output, 'shots', `preview-${kind}.png`) });
          }
          assert.deepEqual(plans.flatMap(({ plan }) => plan.inventory.items.map(item => item.name)).sort(), [...input.names].sort());
          const packet = plans.map(({ plan }) => plan);
          const decision = { packet_sha256: digest(packet), preconditions_sha256: digest(packet.map(plan => plan.fingerprint)) };
          await operator.review('immutable-migration-plans', input.names.map((_, i) => i === 14 ? 'binary-member' : `member-${i}`), ['binary-member'], async () => {
            assert.equal(packet.flatMap(plan => plan.inventory.items).filter(item => !item.blockers.length).length, 14);
          }, decision);
          await receipt('api', packet, decision);
          return;
        }
        if (stage.id === 'stage-and-publish-leaves') {
          let dropped = false;
          for (const { page, plan } of plans) {
            lastPage = page;
            if (plan.inventory.items.some(item => item.blockers.length)) await operator.click(page.getByRole('checkbox', { name: /I explicitly exclude/ }), 'exclude-unsupported');
            await page.route(`**/skills/intake/${plan.id}/accept/`, async route => {
              if (!dropped || attempts.length === 1) attempts.push({ bodyHash: digest(route.request().postData()), key: route.request().headers()['idempotency-key'] });
              if (!dropped) {
                const response = await route.fetch();
                assert.equal(response.status(), 200);
                dropped = true;
                ledger.fault('lost-import-response', 'response-lost-after-commit');
                await route.abort('failed');
              } else await route.continue();
            });
            for (const item of plan.inventory.items.filter(item => !item.blockers.length)) {
              await choose(page, 'Review item', item.name);
              await choose(page, 'Source update decision', 'Replace with reviewed source');
              await operator.click(page.getByRole('checkbox', { name: /I have reviewed source rights/ }), 'review-source-rights');
              await operator.click(page.getByRole('checkbox', { name: /Apply this exact decision/ }), 'confirm-import-decision');
              await click(page, 'button', 'Import reviewed item as draft');
              if (attempts.length === 1) {
                await expect(page.getByText(/The response was lost/)).toBeVisible();
                await operator.recoveryDecision('reuse-original-import', async () => {
                  await click(page, 'button', 'Retry exact decision');
                });
              }
              await expect(page.getByText(/Decision recorded for this item/)).toBeVisible();
            }
            const receipts = await read(`intake/${plan.id}/receipts/?page_size=100`, signal);
            assert.equal(receipts.count, plan.inventory.items.filter(item => !item.blockers.length).length);
            assert.equal(new Set(receipts.results.map(row => row.item_key)).size, receipts.count);
            for (const row of receipts.results) imported.push({ ...row, name: plan.inventory.items.find(item => item.key === row.item_key).name });
          }
          assert.deepEqual(attempts[0], attempts[1]);
          assert.equal(imported.length, 14);
          for (const row of imported) {
            const page = await newPage();
            // Explicit URL/identity reconciliation is counted, not hidden in a
            // fixture-created draft or a single grouped publication action.
            await operator.reconcile('destination-identity', async () => {
              await go(page, `/dashboard/skills/${row.result.resource_id}/edit`, 'destination-editor');
            });
            const draft = await read(`${row.result.resource_id}/draft/`, signal);
            assert.deepEqual(draft.files, input.files[row.name]);
            await click(page, 'button', 'Publish');
            const dialog = page.getByRole('dialog');
            await expect(dialog.getByText('Publish skill', { exact: true })).toBeVisible();
            await operator.review('publish-leaf', draft.files.map((_, i) => `file-${i}`), [], async () => {
              for (const file of draft.files) {
                const button = dialog.getByRole('button', { name: new RegExp(file.path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) });
                if (await button.count()) await operator.click(button.first(), 'inspect-file');
              }
            });
            const warnings = dialog.getByRole('checkbox', { name: 'I reviewed the warnings and want to publish this version.', exact: true });
            await expect.poll(async () => (await warnings.count()) > 0 || await dialog.getByRole('button', { name: 'Publish', exact: true }).isEnabled()).toBeTruthy();
            if (await warnings.count()) await operator.click(warnings, 'acknowledge-publication-warning');
            await expect(dialog.getByRole('checkbox', { name: 'production', exact: true })).not.toBeChecked();
            await expect(dialog.getByRole('checkbox', { name: 'staging', exact: true })).not.toBeChecked();
            const response = page.waitForResponse(res => res.url().endsWith(`/${row.result.resource_id}/draft/publish/`) && res.request().method() === 'POST');
            await operator.click(dialog.getByRole('button', { name: 'Publish', exact: true }), 'publish-leaf');
            assert.equal((await response).status(), 201);
            const versions = await read(`${row.result.resource_id}/versions/`, signal);
            assert.equal(versions.count, 1);
            assert.equal((await read(`${row.result.resource_id}/labels/`, signal)).length, 0);
            await page.close();
          }
          await receipt('api', { imported, attempts });
          return;
        }
        if (stage.id === 'assemble-context') {
          const page = await newPage();
          await go(page, '/dashboard/skills/contexts', 'context-library');
          await operator.click(page.getByRole('button', { name: /^(Write AGENTS.md|New agent context)$/ }), 'create-context');
          await operator.fill(page.getByLabel('AGENTS.md', { exact: true }), await readFile(input.agents, 'utf8'), 'standing-instructions');
          await operator.fill(page.getByLabel('Immutable name', { exact: true }), input.contextName, 'context-name');
          await operator.fill(page.getByLabel('Purpose', { exact: true }), input.description, 'context-purpose');
          const created = page.waitForResponse(res => res.url().endsWith('/contexts/') && res.request().method() === 'POST');
          await click(page, 'button', 'Create draft');
          const response = await created;
          assert.equal(response.status(), 201);
          const createdReceipt = (await response.json()).result;
          contextId = createdReceipt.context_id ?? createdReceipt.context?.id;
          requireValue(contextId, 'Context creation must retain an identity');
          await choose(page, 'Update policy', 'Pin version');
          for (const item of imported) {
            await choose(page, 'Published leaf skill', item.name);
            await click(page, 'button', 'Add to proposed links');
          }
          await click(page, 'button', 'Preview dependency changes');
          await operator.click(page.getByRole('checkbox', { name: 'Accept these exact resolved versions and selector policies.', exact: true }), 'accept-pinned-links');
          await click(page, 'button', 'Apply selected updates to draft');
          await expect(page.getByRole('heading', { name: 'Linked skills · 14 accepted locks', exact: true })).toBeVisible();
          await click(page, 'button', 'Review changes');
          await expect(page.getByText('Immutable candidate', { exact: true })).toBeVisible();
          const reviewCandidate = async () => {
            const reviewed = page.getByRole('checkbox', { name: 'I reviewed the saved candidate against its content origin and current publication head.', exact: true });
            if (!(await reviewed.isChecked())) await operator.click(reviewed, 'review-context-preconditions');
            const warning = page.getByRole('checkbox', { name: 'I acknowledge the static warnings for this exact candidate.', exact: true });
            if (await warning.count() && !(await warning.isChecked())) await operator.click(warning, 'acknowledge-context-warnings');
          };
          await reviewCandidate();
          const acknowledge = page.getByRole('button', { name: 'Acknowledge reviewed head', exact: true });
          if (await acknowledge.count()) {
            await operator.click(acknowledge, 'acknowledge-current-head');
            await expect(page.getByRole('button', { name: 'Publish version', exact: true })).toBeVisible();
            await reviewCandidate();
          }
          const published = page.waitForResponse(res => res.url().endsWith(`/contexts/${contextId}/draft/publish/`) && res.request().method() === 'POST');
          await operator.click(page.getByRole('button', { name: 'Publish version', exact: true }), 'publish-context');
          assert.equal((await published).status(), 201);
          candidate = await read(`contexts/${contextId}/versions/1/inspection/`, signal);
          assert.deepEqual(candidate.descriptor.links.map(link => link.skill_id).sort(), imported.map(row => row.result.resource_id).sort());
          assert.ok(candidate.descriptor.links.every(link => link.version === 1 && link.requested.version === 1));
          const manifest = [...candidate.manifest.files];
          let offset = candidate.manifest.next_file_offset;
          while (offset !== null) {
            assert.equal(offset, manifest.length);
            const next = await read(`contexts/${contextId}/versions/1/inspection/?file_offset=${offset}&file_limit=25`, signal);
            assert.equal(next.manifest.version_id, candidate.manifest.version_id);
            assert.equal(next.manifest.digest, candidate.manifest.digest);
            assert.ok(next.manifest.files.length);
            manifest.push(...next.manifest.files);
            offset = next.manifest.next_file_offset;
          }
          assert.equal(manifest.length, inventory.compiled_closure.file_count);
          assert.deepEqual(manifest.map(file => file.path).sort(), [...inventory.compiled_closure.paths].sort());
          assert.deepEqual((await read(`contexts/${contextId}/`, signal)).release_state.labels, []);
          await receipt('api', { ...candidate, complete_manifest: manifest });
          return;
        }
        if (stage.id === 'credential-and-install') {
          const page = await newPage();
          await go(page, `/dashboard/skills/contexts/${contextId}`, 'exact-context');
          await click(page, 'tab', 'Use');
          await expect(page.getByText(/A released package and a verified host\/version profile are required/)).toBeVisible();
          await page.screenshot({ path: resolve(output, 'shots', 'unqualified-delivery.png') });
          await record('delivery-blocker', { contextId, candidate, reason: 'Public Use handoff has no released package/verified host profile. This driver cannot replace real discovery/model-boundary evidence with a synthetic success.', qualified: false });
          throw new Error('Public immutable candidate delivery is not qualified');
        }
        throw new Error(`No completed public-interface stage evidence: ${stage.id}`);
      } catch (error) {
        await record('blocked-stage', { stage: stage.id, errorType: error.name, qualified: false });
        if (lastPage && !lastPage.isClosed()) await lastPage.screenshot({ path: resolve(output, 'shots', 'blocked.png') }).catch(() => {});
        throw error;
      } finally { signal.removeEventListener('abort', abort); }
    },
    async observe(check, { signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      if (check.id === 'complete-inventory' && inventory) return { source: 'filesystem', value: inventory.members.map(member => member.name), artifact_sha256: await record('inventory-observation', inventory) };
      // Never promote a prerequisite receipt to whole-job evidence.
      throw new Error(`Whole-job independent evidence has not completed: ${check.id}`);
    },
    async teardown() {
      await context?.close();
      await browser?.close();
      return { qualified: !browser || !browser.isConnected() };
    },
  };
}
