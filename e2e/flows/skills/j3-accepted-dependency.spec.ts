import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { authInitScript } from '../../lib/auth';
import type { ScopeActor } from '../../lib/scope-actors';

const API = '/model-hub/skills/';
const UI = '/dashboard/skills/contexts';
const UI_READY = 60_000;

test('SKILLS-E2E-030: retain accepted child versions while upstream advances during parent review', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-030', area: 'skills',
    userGoal: 'An author explicitly accepts a shared policy update into two followers while a third context keeps its deliberate pin, then publishes immutable parents without silently promoting them',
    steps: [
      'prepare three published contexts over fifteen synthetic skills, two followers and one deliberate v7 pin',
      'publish shared policy v8 and open each follower from its exact viewed release',
      'preview and explicitly accept the selected child update in the real context editor',
      'advance upstream to v9, edit the standing root, and verify the accepted v8 lock survives autosave',
      'review and publish each parent independently without moving production',
      'inspect both immutable parent descriptors and the unchanged pinned context',
    ],
    backendChecks: [
      'the initial three immutable descriptors contain the exact fifteen-member set with shared policy v7',
      'each browser dependency acceptance advances its durable draft revision and retains shared policy v8',
      'root autosave advances revision while retaining accepted v8 after upstream reaches v9',
      'published descriptors and dependency projections agree on all fifteen identities, both followers retain v8 and the deliberate pin retains v7',
      'parent publication creates no label events or production assignment',
    ],
  }),
}, async ({ browser, request, scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(1_440_000); // two followers × 11 UI stages × 60s + 120s fixture setup.
  const evidence = process.env.J3_EVIDENCE_DIR;
  const owner = scopeActors.ownerA;
  const author = scopeActors.member;
  const prefix = `e2e-j3-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const events: unknown[] = [];
  const req = async (actor: ScopeActor, method: string, path: string, data?: unknown): Promise<any> => {
    const response = await request.fetch(`${E2E.apiUrl}${API}${path}`, {
      method, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() }, data,
    });
    const body = await response.json();
    expect(response.ok(), JSON.stringify({ method, path, status: response.status(), body })).toBeTruthy();
    return body.result;
  };
  const leaf = async (name: string, version: number) => req(owner, 'POST', 'publish/', {
    files: [{ path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Synthetic shared purchase policy.\n---\nApproved synthetic policy revision ${version}.\n` }],
    labels: ['production'], expected_release_revision: version - 1, acknowledge_warnings: true,
  });
  const publishFixture = async (id: string) => {
    const draft = await req(author, 'GET', `contexts/${id}/draft/`);
    const review = await req(author, 'POST', `contexts/${id}/draft/head-review/`, { revision: draft.revision });
    const ack = { revision: draft.revision, expected_digest: review.candidate_digest,
      expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint };
    const saved = await req(author, 'POST', `contexts/${id}/draft/acknowledge-head/`, ack);
    return req(author, 'POST', `contexts/${id}/draft/publish/`, { ...ack, revision: saved.revision,
      review_fingerprint: saved.review_fingerprint, base_version: saved.base_version,
      note: 'Synthetic initial condition', acknowledge_warnings: true });
  };
  const context = await browser.newContext({ baseURL: E2E.appUrl,
    viewport: { width: 1440, height: 900 },
    ...(process.env.E2E_RECORD_VIDEO === '1' ? { recordVideo: { dir: testInfo.outputPath('video'), size: { width: 1440, height: 900 } } } : {}),
  });
  await context.addInitScript(authInitScript, { access: author.tokens.access, refresh: author.tokens.refresh,
    organizationId: author.organizationId, workspaceId: author.workspaceId });
  await context.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), author.organizationId);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = await context.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });
  page.setDefaultTimeout(UI_READY);
  const shot = async (name: string) => {
    const image = await page.screenshot();
    await testInfo.attach(name, { body: image, contentType: 'image/png' });
    if (evidence) { await mkdir(`${evidence}/shots`, { recursive: true }); await writeFile(`${evidence}/shots/${name}.png`, image); }
  };
  // One-sided controls change only the expected observation, never the fixture.
  const wrong = process.env.J3_ORACLE_FAULT;
  try {
    const leaves = [];
    for (let i = 0; i < 15; i++) leaves.push(await leaf(`${prefix}-leaf-${i}`, 1));
    for (let v = 2; v <= 7; v++) leaves[0] = await leaf(`${prefix}-leaf-0`, v);
    const childId = leaves[0].skill.id;
    const parents: { id: string; name: string; before?: any }[] = [];
    for (let i = 0; i < 3; i++) {
      const name = `${prefix}-parent-${i}`;
      const draft = await req(author, 'POST', 'contexts/', { name, description: 'Synthetic shared-policy context', tags: [],
        files: [{ path: 'AGENTS.md', content: '# Synthetic policy\nConsult approved linked skills.\n' }] });
      const links = leaves.map((item, j) => ({ skill_id: item.skill.id, selector: i === 2 && j === 0 ? { version: 7 } : { label: 'production' } }));
      const input = { revision: draft.revision, links, refresh_skill_ids: [] };
      const preview = await req(author, 'POST', `contexts/${draft.context_id}/dependencies/preview/`, input);
      await req(author, 'POST', `contexts/${draft.context_id}/dependencies/apply/`, { ...input, expected_fingerprint: preview.fingerprint });
      await publishFixture(draft.context_id);
      parents.push({ id: draft.context_id, name });
      const inspection = await req(author, 'GET', `contexts/${draft.context_id}/versions/1/inspection/`);
      expect(inspection.descriptor.links.map((l: any) => l.skill_id).sort()).toEqual(leaves.map(l => l.skill.id).sort());
      expect(inspection.descriptor.links.find((l: any) => l.skill_id === childId).version).toBe(wrong === 'initial' ? 99 : 7);
    }
    events.push({ stage: 'initial', childId, parents, leafIds: leaves.map(l => l.skill.id) });
    await testInfo.attach('j3-initial-identities', { body: JSON.stringify(events), contentType: 'application/json' });
    await leaf(`${prefix}-leaf-0`, 8);
    for (const parent of parents.slice(0, 2)) {
      await page.goto(`${UI}/${parent.id}?version=1`);
      await page.getByRole('button', { name: 'Edit viewed v1', exact: true }).click();
      await page.getByRole('button', { name: 'Start selected draft', exact: true }).click();
      await page.getByRole('checkbox', { name: `Review update for ${prefix}-leaf-0`, exact: true }).check();
      const before = await req(author, 'GET', `contexts/${parent.id}/draft/`);
      await page.getByRole('button', { name: 'Preview dependency changes', exact: true }).click();
      await page.getByRole('checkbox', { name: 'Accept these exact resolved versions and selector policies.', exact: true }).check();
      await page.getByRole('button', { name: 'Apply selected updates to draft', exact: true }).click();
      await expect.poll(async () => (await req(author, 'GET', `contexts/${parent.id}/draft/`)).resolved_lock.links.find((l: any) => l.skill_id === childId).version, { timeout: UI_READY }).toBe(8);
      parent.before = await req(author, 'GET', `contexts/${parent.id}/draft/`);
      expect(parent.before.revision).toBeGreaterThan(before.revision);
      expect(parent.before.resolved_lock.links.find((l: any) => l.skill_id === childId).version).toBe(wrong === 'accepted' ? 99 : 8);
      await shot(`accepted-${parent.name}`);
    }
    await leaf(`${prefix}-leaf-0`, 9);
    for (const parent of parents.slice(0, 2)) {
      await page.goto(`${UI}/${parent.id}/edit`);
      const input = page.getByRole('textbox', { name: /Editor content/ });
      await expect(input).toBeVisible({ timeout: UI_READY });
      const root = '# Reviewed synthetic policy\nUse explicitly accepted policy, never upstream latest.\n';
      await input.focus(); await input.press('Control+a');
      await page.evaluate(text => navigator.clipboard.writeText(text), root);
      await input.press('Control+v');
      await expect.poll(async () => (await req(author, 'GET', `contexts/${parent.id}/draft/`)).files.find((f: any) => f.path === 'AGENTS.md').content, { timeout: UI_READY }).toBe(root);
      const saved = await req(author, 'GET', `contexts/${parent.id}/draft/`);
      expect(saved.revision).toBeGreaterThan(parent.before.revision);
      expect(saved.resolved_lock.links.find((l: any) => l.skill_id === childId).version).toBe(wrong === 'autosave' ? 99 : 8);
      events.push({ stage: 'autosave', parent: parent.id, revision: saved.revision, digest: saved.digest });
      await page.getByRole('button', { name: 'Review changes', exact: true }).click();
      const reviewed = page.getByRole('checkbox', { name: 'I reviewed the saved candidate against its content origin and current publication head.', exact: true });
      await reviewed.check();
      const warning = page.getByRole('checkbox', { name: 'I acknowledge the static warnings for this exact candidate.', exact: true });
      if (await warning.count()) await warning.check();
      await shot(`review-${parent.name}`);
      await page.getByRole('button', { name: 'Acknowledge reviewed head', exact: true }).click();
      // Acknowledgment invalidates the draft and remounts the digest-bound
      // review. Do not tick the old controls while that refresh is in flight.
      await expect(page.getByRole('button', { name: 'Publish version', exact: true })).toBeVisible({ timeout: UI_READY });
      await reviewed.check();
      if (await warning.count()) await warning.check();
      await page.getByRole('button', { name: 'Publish version', exact: true }).click();
      await expect.poll(async () => (await req(author, 'GET', `contexts/${parent.id}/`)).context.latest_version, { timeout: UI_READY }).toBe(2);
    }
    const observed = [];
    for (let i = 0; i < parents.length; i++) {
      const parent = parents[i];
      const version = i === 2 ? 1 : 2;
      const inspection = await req(author, 'GET', `contexts/${parent.id}/versions/${version}/inspection/`);
      expect(inspection.descriptor.links.map((l: any) => l.skill_id).sort()).toEqual(leaves.map(l => l.skill.id).sort());
      expect(inspection.descriptor.links.find((l: any) => l.skill_id === childId).version).toBe(wrong === 'projection' ? 99 : i === 2 ? 7 : 8);
      const projection = await scopeProbe.pg<{ child_id: string; child_version_id: string }>(
        'SELECT child_skill_id AS child_id, child_version_id FROM model_hub_hubskillcontextdependency WHERE parent_version_id = $1 ORDER BY child_skill_id', [inspection.manifest.version_id]);
      expect(projection).toEqual(inspection.descriptor.links.map((l: any) => ({ child_id: l.skill_id, child_version_id: l.version_id })).sort((a: any, b: any) => a.child_id.localeCompare(b.child_id)));
      const labels = await scopeProbe.pg('SELECT name FROM model_hub_hubskilllabel WHERE skill_id = $1', [parent.id]);
      const labelEvents = await scopeProbe.pg('SELECT id FROM model_hub_hubskilllabelevent WHERE skill_id = $1', [parent.id]);
      expect(labels).toEqual(wrong === 'labels' ? [{ name: 'production' }] : []);
      expect(labelEvents).toEqual([]);
      observed.push({ id: parent.id, inspection, projection });
    }
    events.push({ stage: 'publication', observed });
    await page.goto(`${UI}/${parents[0].id}?version=2`);
    await page.getByRole('tab', { name: 'Linked skills', exact: true }).click();
    await shot('published-v8-with-upstream-v9');
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) await shot('failure');
    await testInfo.attach('j3-observations', { body: JSON.stringify(events), contentType: 'application/json' });
    if (evidence) { await mkdir(evidence, { recursive: true }); await writeFile(`${evidence}/observations.json`, JSON.stringify(events, null, 2)); }
    await context.close();
    if (evidence && page.video()) await page.video()!.saveAs(`${evidence}/journey.webm`);
  }
});
