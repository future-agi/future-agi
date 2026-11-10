import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { strToU8, zipSync } from 'fflate';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';

type File = { path: string; content: string };
type Plan = { id: string; inventory: { items: { key: string; name: string; blockers: unknown[] }[]; rejected: { path: string; reason: string }[] } };
type Receipt = { item_key: string; result: { resource_id: string; published: boolean } };
type Envelope<T> = { status: boolean; result: T };

// Deliberately a retained-intake subflow, not a complete J1/10x qualification.
// All writes go through UI; API reads are independent of the visible success state.
test('SKILLS-E2E-010: retain all supported members and recover one lost import response', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-010', area: 'skills',
    userGoal: 'Trial the supported subset of a 15-member private library as drafts without publishing or losing an import receipt',
    steps: ['open retained migration and upload the 15-member private ZIP',
      'review all members and explicitly acknowledge the unsupported member exclusion',
      'stage each supported member deliberately; recover the first lost response with its original key',
      'inspect exact draft bytes, no published versions or production labels, and complete receipt accounting'],
    backendChecks: ['the independent plan accounts for all 15 roots and blocks the binary-containing member',
      '14 exact supported drafts exist, no version or production label was published, and the unsupported member was not staged',
      'the lost-response retry has the same request/key and exactly one receipt per supported member'],
  }),
}, async ({ browser, scopeActors }, testInfo) => {
  test.setTimeout(1_020_000); // 14 reviewed items × 60s, boot/recovery/cleanup 180s.
  const actor = scopeActors.ownerB;
  const root = process.env.J1_EVIDENCE_DIR || testInfo.outputPath('j1');
  await mkdir(join(root, 'shots'), { recursive: true });
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 }, recordVideo: { dir: join(root, 'video'), size: { width: 1440, height: 900 } } });
  await context.addInitScript(authInitScript, { access: actor.tokens.access, refresh: actor.tokens.refresh, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await context.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), actor.organizationId);
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  const prefix = `j1-${Date.now().toString(36)}`;
  const supported = Array.from({ length: 14 }, (_, index) => `${prefix}-${index}`);
  const unsupported = `${prefix}-binary`;
  const files = new Map<string, File[]>();
  const entries: Record<string, Uint8Array> = {};
  for (const name of [...supported, unsupported]) {
    const content = `---\nname: ${name}\ndescription: Synthetic retained migration policy\n---\n\n# Policy\nKeep exact café text.\n`;
    const member = [{ path: 'SKILL.md', content }, { path: 'references/policy.md', content: 'Private reference\r\n' }];
    files.set(name, member);
    member.forEach(file => { entries[`${name}/${file.path}`] = strToU8(file.content); });
  }
  entries[`${unsupported}/assets/example.bin`] = new Uint8Array([0, 255, 0, 1]);
  const read = async <T>(path: string): Promise<T> => (await actor.api.get<Envelope<T>>(path)).result;
  const choose = async (name: string, option: string) => {
    await page.getByRole('combobox', { name, exact: true }).click();
    await page.getByRole('option', { name: option, exact: true }).click();
  };
  const attempts: { body: string | null; key: string | undefined }[] = [];
  let dropped = false;
  try {
    await page.goto('/dashboard/skills');
    await page.getByRole('button', { name: /^(Import a folder or ZIP|Import)$/ }).click();
    await page.getByRole('tab', { name: 'Retained migration', exact: true }).click();
    await choose('Retained source format', 'Private folder or ZIP');
    await page.getByLabel('Retained source files', { exact: true }).setInputFiles({ name: 'private-library.zip', mimeType: 'application/zip', buffer: Buffer.from(zipSync(entries, { level: 0 })) });
    const previewResponse = page.waitForResponse(response => response.url().endsWith('/skills/intake/preview/') && response.status() === 200);
    await page.getByRole('button', { name: 'Preview retained migration', exact: true }).click();
    const preview = (await (await previewResponse).json()).result as Plan;
    await expect(page.getByRole('heading', { name: 'Review retained migration plan' })).toBeVisible();
    const plan = await read<Plan>(`/model-hub/skills/intake/${preview.id}/`);
    const inventoryOracle = (value: Plan) => {
      assert.deepEqual(value.inventory.items.map(item => item.name).sort(), [...supported, unsupported].sort());
      assert.ok(value.inventory.items.find(item => item.name === unsupported)!.blockers.length > 0);
      assert.ok(value.inventory.rejected.some(item => item.path.endsWith('assets/example.bin')));
    };
    inventoryOracle(plan);
    const corruptInventory = structuredClone(plan);
    corruptInventory.inventory.items.find(item => item.name === unsupported)!.blockers = [];
    assert.throws(() => inventoryOracle(corruptInventory));
    await page.screenshot({ path: join(root, 'shots', '01-supported-subset.png') });
    await page.getByRole('checkbox', { name: /I explicitly exclude/ }).check();
    await page.route(`**/skills/intake/${plan.id}/accept/`, async route => {
      const request = route.request();
      if (!dropped || attempts.length === 1) attempts.push({ body: request.postData(), key: request.headers()['idempotency-key'] });
      if (!dropped) {
        const response = await route.fetch();
        assert.equal(response.status(), 200);
        dropped = true;
        await route.abort('failed');
      } else await route.continue();
    });
    for (const [index, name] of supported.entries()) {
      await choose('Review item', name);
      await choose('Source update decision', 'Replace with reviewed source');
      await page.getByRole('checkbox', { name: /I have reviewed source rights/ }).check();
      await page.getByRole('checkbox', { name: /Apply this exact decision/ }).check();
      await page.getByRole('button', { name: 'Import reviewed item as draft', exact: true }).click();
      if (index === 0) {
        await expect(page.getByText(/The response was lost/)).toBeVisible();
        await page.screenshot({ path: join(root, 'shots', '02-recover-original-operation.png') });
        await page.getByRole('button', { name: 'Retry exact decision', exact: true }).click();
      }
      await expect(page.getByText(/Decision recorded for this item/)).toBeVisible();
    }
    const receipts = await read<{ count: number; results: Receipt[] }>(`/model-hub/skills/intake/${plan.id}/receipts/?page_size=100`);
    const receiptOracle = (value: typeof receipts, requests: typeof attempts) => {
      assert.equal(value.count, 14);
      assert.equal(new Set(value.results.map(row => row.item_key)).size, 14);
      assert.deepEqual(requests[0], requests[1]);
      assert.ok(requests[0].key);
      assert.equal(value.results.some(row => row.item_key === plan.inventory.items.find(item => item.name === unsupported)!.key), false);
    };
    receiptOracle(receipts, attempts);
    assert.throws(() => receiptOracle({ ...receipts, count: 15 }, attempts));
    assert.throws(() => receiptOracle(receipts, [attempts[0], { ...attempts[1], key: 'different-operation' }]));
    for (const row of receipts.results) {
      const name = plan.inventory.items.find(item => item.key === row.item_key)!.name;
      const draft = await read<{ files: File[] }>(`/model-hub/skills/${row.result.resource_id}/draft/`);
      const versions = await read<{ count: number }>(`/model-hub/skills/${row.result.resource_id}/versions/`);
      const oracle = (value: typeof draft, count: number) => {
        assert.deepEqual([...value.files].sort((a, b) => a.path.localeCompare(b.path)), [...files.get(name)!].sort((a, b) => a.path.localeCompare(b.path)));
        assert.equal(count, 0);
        assert.equal(row.result.published, false);
      };
      oracle(draft, versions.count);
      assert.throws(() => oracle({ files: [{ path: 'SKILL.md', content: 'corrupted' }] }, versions.count));
      assert.throws(() => oracle(draft, 1));
    }
    await page.screenshot({ path: join(root, 'shots', '03-retained-drafts.png') });
    await writeFile(join(root, 'intake-observations.json'), JSON.stringify({ scope: 'J1 retained-intake subflow only', plan, receipts, lostResponseAttempts: attempts.length, originalOperationReused: true, oneSidedProofs: ['blocked-member', 'duplicate-count', 'retry-key', 'draft-bytes', 'publication-count'], completeJobQualified: false }, null, 2));
  } finally { await context.close(); }
});
