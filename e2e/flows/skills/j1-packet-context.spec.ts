import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';
import { skillDigest } from '../../lib/skills-consumption';

type File = { path: string; content: string };
type Item = { key: string; name: string; blockers: unknown[] };
type Plan = { id: string; inventory: { items: Item[] } };
type Receipt = { item_key: string; result: { resource_id: string } };
type Envelope<T> = { status: boolean; result: T };
type Lock = { skill_id: string; version: number; requested: { version?: number } };

// ROUND3-PLAN J1 steps 2–4 through the one-review path: one local-inventory
// packet, one reviewed set, the packet's proposed context assembled in place
// and the returned version's pilot credential. A subflow, NOT the complete
// 15-member cold/configured job. Wire/locators: RetainedMigrationPanel
// (packet input), MigrationBatchReview, MigrationContextAssembly,
// ContextPilotCredentials; r3/import_plans.py, v12/authoring.py.
const UI_READY = 60_000;
const sha256 = (value: string) => createHash('sha256').update(value).digest('hex');
test('SKILLS-E2E-013: preview one inventory packet, publish the reviewed set and its proposed context in one review', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-013', area: 'skills',
    userGoal: 'Trial a private library from one local-inventory packet: review every member once, publish the supported set and the proposed agent context without leaving the review, then issue the pilot credential for the exact version',
    steps: ['choose one inventory packet holding a versioned exchange and a private folder with a binary member; both sources are previewed into one reviewed set',
      'acknowledge the left-out member, confirm rights once and import and publish the three supported items in one decision',
      'preview the prefilled agent context: a private draft shows the exact pinned versions and compiled candidate, nothing linked yet',
      'accept that exact candidate and publish it, then issue a pilot credential bound to the returned version and revoke it'],
    backendChecks: ['both retained plans come from the packet, the folder members keep their immutable source identities and the blocked member gets no receipt',
      'before acceptance the context draft has no links; after publication version 1 links exactly the three skills pinned at v1 and its digest is the previewed candidate',
      'the pilot credential is consume-only and bound to that one context version'],
  }),
}, async ({ browser, scopeActors }, testInfo) => {
  test.setTimeout(900_000); // 6 UI stages × 60s, one 180s batch, boot/cleanup 240s.
  const actor = scopeActors.ownerB;
  const root = process.env.J1_PACKET_EVIDENCE_DIR || testInfo.outputPath('j1-packet');
  await mkdir(join(root, 'shots'), { recursive: true });
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 } });
  await context.addInitScript(authInitScript, { access: actor.tokens.access, refresh: actor.tokens.refresh, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await context.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), actor.organizationId);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const prefix = `j1-packet-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const skill = (name: string): File[] => [{ path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Use when reviewing the ${name} synthetic packet policy\n---\n\n# Policy\nKeep exact café text.\n` },
    { path: 'references/policy.md', content: 'Private reference\r\n' }];
  const exchangeNames = [`${prefix}-0`, `${prefix}-1`];
  const folderName = `${prefix}-2`;
  const binaryName = `${prefix}-binary`;
  const files = new Map<string, File[]>([...exchangeNames, folderName, binaryName].map(name => [name, skill(name)]));
  const exchange = { format: 'langfuse-skills-v1', origin: 'synthetic.invalid', project: prefix,
    skills: exchangeNames.map((name, index) => ({ resource: `resource-${index}`, revision: 'v1', digest: skillDigest(files.get(name)!), files: files.get(name)! })) };
  const resource = `private/${folderName}`;
  const identity = { origin: 'local:synthetic-packet', project: prefix, revision: 'v1', resource, digest: skillDigest(files.get(folderName)!) };
  const folderFiles = [...[folderName, binaryName].flatMap(name => files.get(name)!.map(file => ({ path: `private/${name}/${file.path}`, content_base64: Buffer.from(file.content).toString('base64') }))),
    { path: `private/${binaryName}/assets/example.bin`, content_base64: Buffer.from([0, 255, 0, 1]).toString('base64') }];
  const agents = '# Packet pilot\nUse the explicitly linked policies.\n';
  const packet = { schema: 1, kind: 'futureagi-migration-packet', source_member_count: 4,
    context: { name: `${prefix}-context`, description: 'Synthetic packet trial', agents_md: agents },
    sources: [{ kind: 'langfuse', export: exchange },
      { kind: 'folder', files: folderFiles, source_identities: { [sha256(JSON.stringify(['loose', resource]))]: identity } }] };
  const read = async <T>(path: string): Promise<T> => (await actor.api.get<Envelope<T>>(path)).result;
  try {
    await page.goto('/dashboard/skills');
    await page.getByRole('button', { name: /^(Import a folder or ZIP|Import)$/ }).click();
    await page.getByRole('tab', { name: 'From another tool', exact: true }).click();
    const previews: Response[] = [];
    page.on('response', response => { if (response.request().method() === 'POST' && /\/skills\/intake\/(langfuse\/)?preview\/$/.test(response.url())) previews.push(response); });
    await page.getByLabel('Inventory migration packet', { exact: true }).setInputFiles({ name: 'migration-packet.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(packet)) });
    await expect.poll(() => previews.length).toBe(2);
    const plans: Plan[] = [];
    for (const response of previews) {
      assert.equal(response.status(), 200);
      plans.push(await read<Plan>(`/model-hub/skills/intake/${((await response.json()).result as Plan).id}/`));
    }
    await expect(page.getByRole('heading', { name: 'Review all items', exact: true })).toBeVisible();
    await expect(page.getByRole('table', { name: 'Retained migration items' }).getByRole('row')).toHaveCount(5);
    // The folder member keeps the identity carried in the packet; nothing was typed.
    const folderPlan = plans.find(plan => plan.inventory.items.some(item => item.name === folderName))!;
    const folderItem = folderPlan.inventory.items.find(item => item.name === folderName) as Item & { source: { resource: string; revision: string } };
    assert.deepEqual([folderItem.source.resource, folderItem.source.revision], [resource, 'v1']);
    const blocked = folderPlan.inventory.items.find(item => item.blockers.length > 0)!;
    assert.ok(blocked);
    await page.screenshot({ path: join(root, 'shots', '01-packet-reviewed-set.png'), fullPage: true });

    const submit = page.getByRole('button', { name: 'Import and publish 3 items', exact: true });
    await page.getByRole('checkbox', { name: /^I confirm my rights to copy all 3 items/ }).check();
    await expect(submit).toBeDisabled();
    await page.getByRole('checkbox', { name: /^Leave out 1 skill/ }).check();
    await submit.click();
    await expect(page.getByText('3 versions published without labels. Production was not changed.', { exact: true })).toBeVisible({ timeout: 180_000 });
    const receipts: Receipt[] = [];
    for (const plan of plans) receipts.push(...(await read<{ results: Receipt[] }>(`/model-hub/skills/intake/${plan.id}/receipts/?page_size=100`)).results);
    assert.equal(receipts.length, 3);
    assert.equal(receipts.some(row => row.item_key === blocked.key), false);
    const leaves = receipts.map(row => row.result.resource_id);

    await expect(page.getByRole('heading', { name: 'Agent context from this migration', exact: true })).toBeVisible();
    await expect(page.getByLabel('Immutable name', { exact: true })).toHaveValue(`${prefix}-context`);
    await expect(page.getByLabel('AGENTS.md', { exact: true })).toHaveValue(agents);
    const created = page.waitForResponse(res => res.url().endsWith('/contexts/') && res.request().method() === 'POST');
    const previewed = page.waitForResponse(res => res.url().endsWith('/dependencies/preview/') && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Preview agent context', exact: true }).click();
    const createdBody = (await (await created).json()).result;
    const contextId: string = createdBody.context_id ?? createdBody.context?.id;
    assert.ok(contextId);
    const candidate = (await (await previewed).json()).result as { candidate_digest: string; resolved_lock: { links: Lock[] } };
    await expect(page.getByRole('table', { name: 'Pinned links' }).getByRole('row')).toHaveCount(4);
    const linkOracle = (links: Lock[]) => {
      assert.deepEqual(links.map(link => link.skill_id).sort(), [...leaves].sort());
      assert.ok(links.every(link => link.version === 1 && link.requested.version === 1));
    };
    linkOracle(candidate.resolved_lock.links);
    // Previewing links nothing: acceptance is still the owner's explicit decision.
    const draft = await read<{ resolved_lock: { links: Lock[] } }>(`/model-hub/skills/contexts/${contextId}/draft/`);
    assert.equal(draft.resolved_lock.links.length, 0);
    const publish = page.getByRole('button', { name: 'Publish agent context', exact: true });
    await expect(publish).toBeDisabled();
    await page.getByRole('checkbox', { name: 'Accept these 3 exact pinned versions and this compiled candidate.', exact: true }).check();
    const warning = page.getByRole('checkbox', { name: 'I acknowledge the static warnings for this exact candidate.', exact: true });
    if (await warning.count()) await warning.check();
    const published = page.waitForResponse(res => res.url().endsWith(`/contexts/${contextId}/draft/publish/`) && res.request().method() === 'POST');
    await publish.click();
    assert.equal((await published).status(), 201);
    await expect(page.getByText(/^Published .* v1 · sha256:[a-f0-9]{64}\. No labels moved; production is unchanged\.$/)).toBeVisible();
    await expect(page.getByText(/A released package and a verified host\/version profile are required/)).toBeVisible();
    const inspection = await read<{ manifest: { version_id: string; digest: string }; descriptor: { links: Lock[] } }>(`/model-hub/skills/contexts/${contextId}/versions/1/inspection/`);
    linkOracle(inspection.descriptor.links);
    assert.throws(() => linkOracle(inspection.descriptor.links.slice(1)));
    assert.equal(inspection.manifest.digest, candidate.candidate_digest);
    assert.deepEqual((await read<{ release_state: { labels: unknown[] } }>(`/model-hub/skills/contexts/${contextId}/`)).release_state.labels, []);

    const issued = page.waitForResponse(res => res.url().endsWith('/contexts/credentials/') && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Create key', exact: true }).click();
    const issuedResponse = await issued;
    assert.equal(issuedResponse.status(), 201);
    const credentialId: string = (await issuedResponse.json()).result.id;
    type Credential = { id: string; purpose: string; revoked_at: string | null; grants: { kind: string; resource_id: string; version_ids: string[] }[] };
    const grantOracle = (row: Credential | undefined) => {
      assert.ok(row);
      assert.equal(row.purpose, 'consume');
      assert.deepEqual(row.grants.map(grant => [grant.kind, grant.resource_id, grant.version_ids]), [['agent_context', contextId, [inspection.manifest.version_id]]]);
    };
    const listed = async () => (await read<{ results: Credential[] }>('/model-hub/skills/contexts/credentials/?page_size=100')).results.find(row => row.id === credentialId);
    grantOracle(await listed());
    assert.throws(() => grantOracle(undefined));
    await page.screenshot({ path: join(root, 'shots', '02-packet-context-credential.png'), fullPage: true });
    await page.getByRole('button', { name: `Actions for credential ${credentialId.slice(0, 8)}`, exact: true }).click();
    await page.getByRole('menuitem', { name: 'Revoke', exact: true }).click();
    const revoked = page.waitForResponse(res => res.url().endsWith(`/contexts/credentials/${credentialId}/`) && res.request().method() === 'DELETE');
    await page.getByRole('dialog').getByRole('button', { name: 'Revoke', exact: true }).click();
    assert.equal((await revoked).status(), 204);
    assert.ok((await listed())?.revoked_at);
    await writeFile(join(root, 'packet-context-observations.json'), JSON.stringify({ scope: 'J1 packet/context subflow only', plans: plans.map(plan => plan.id), receipts: receipts.length, contextId, credentialId, evidenceClass: 'synthetic fixture', completeJobQualified: false }, null, 2));
  } finally { await context.close(); }
});
