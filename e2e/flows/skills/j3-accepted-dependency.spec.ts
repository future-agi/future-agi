import { randomUUID, createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { authInitScript } from '../../lib/auth';
import { SkillsFixtures, answerRule, toolRule } from '../../lib/skills-fixtures';
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
      'publish shared policy v8 and inspect authorized dependents and deliberate pins in light and dark UI',
      'open each follower from its exact viewed release, preview and explicitly accept the selected child update',
      'hold an actual root autosave request in flight, advance upstream to v9, release that unchanged request, and verify accepted v8 and later purpose edits survive',
      'test each complete saved draft against its historical parent and inspect actual model-boundary child delivery',
      'review and publish each parent independently without moving production',
      'inspect both immutable parent descriptors and the unchanged pinned context',
      'request and independently approve each exact parent label move through the UI',
      'resume each pre-move runtime thread and start a clean one, then inspect delivered releases in the Consumers tab',
    ],
    backendChecks: [
      'the initial three immutable descriptors contain the exact fifteen-member set with shared policy v7',
      'each browser dependency acceptance advances its durable draft revision and retains shared policy v8',
      'root autosave advances revision while retaining accepted v8 after upstream reaches v9',
      'published descriptors and dependency projections agree on all fifteen identities, both followers retain v8 and the deliberate pin retains v7',
      'parent publication creates no label events or production assignment',
      'dependent inventory contains exactly the three authorized parent identities and identifies the deliberate pin',
      'whole-context jobs preserve the saved compiled digest, deliver v8 to candidate and v7 to reference at the model boundary, and never deliver v9',
      'each label move requires an independently approved parent request and records the authorized reviewer in PostgreSQL',
      'a resumed runtime thread keeps its original session and v1 identity after production moves, a clean follower thread receives v2, and the pinned binding stays on v1',
    ],
  }),
}, async ({ browser, request, scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(1_440_000); // two followers × 11 UI stages × 60s + 120s fixture setup.
  const evidence = process.env.J3_EVIDENCE_DIR;
  const owner = scopeActors.ownerA;
  const author = scopeActors.member;
  const prefix = `e2e-j3-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const events: unknown[] = [];
  const fake = new SkillsFixtures();
  const prompt = `${prefix}: explain a purchasing request`;
  await fake.register([answerRule(prompt, 0, 'approved-response')]);
  const connectionId = await fake.connect(owner.api);
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
    await page.goto(`/dashboard/skills/${childId}?tab=dependents`);
    await expect(page.getByRole('table', { name: 'Published context dependents' })).toBeVisible();
    await expect(page.getByRole('table', { name: 'Published context dependents' }).getByText('Pinned on purpose', { exact: true })).toBeVisible();
    const inventory = await req(author, 'GET', `${childId}/context-dependents/`);
    expect(inventory.count).toBe(wrong === 'dependents' ? 99 : 3);
    expect(inventory.results.map((row: any) => row.context_id).sort()).toEqual(parents.map(row => row.id).sort());
    await shot('dependent-inventory-light');
    await page.evaluate(() => { const settings = JSON.parse(localStorage.getItem('settings') || '{}'); localStorage.setItem('settings', JSON.stringify({ ...settings, themeMode: 'dark' })); });
    await page.reload();
    await expect(page.getByRole('table', { name: 'Published context dependents' })).toBeVisible();
    await shot('dependent-inventory-dark');
    await page.evaluate(() => { const settings = JSON.parse(localStorage.getItem('settings') || '{}'); localStorage.setItem('settings', JSON.stringify({ ...settings, themeMode: 'light' })); });
    for (const parent of parents.slice(0, 2)) {
      await page.goto(`${UI}/${parent.id}?version=1`);
      await page.getByRole('button', { name: 'Edit latest v1', exact: true }).click();
      await page.getByRole('button', { name: 'Start selected draft', exact: true }).click();
      await page.getByRole('checkbox', { name: `Check for update: ${prefix}-leaf-0`, exact: true }).check();
      const before = await req(author, 'GET', `contexts/${parent.id}/draft/`);
      await page.getByRole('button', { name: 'Preview dependency changes', exact: true }).click();
      await page.getByRole('checkbox', { name: 'Accept these skill versions and update rules into 1 context draft.', exact: true }).check();
      await page.getByRole('button', { name: 'Apply selected updates to draft', exact: true }).click();
      await expect.poll(async () => (await req(author, 'GET', `contexts/${parent.id}/draft/`)).resolved_lock.links.find((l: any) => l.skill_id === childId).version, { timeout: UI_READY }).toBe(8);
      parent.before = await req(author, 'GET', `contexts/${parent.id}/draft/`);
      expect(parent.before.revision).toBeGreaterThan(before.revision);
      expect(parent.before.resolved_lock.links.find((l: any) => l.skill_id === childId).version).toBe(wrong === 'accepted' ? 99 : 8);
      await page.getByText(`${prefix}-leaf-0`, { exact: true }).scrollIntoViewIfNeeded();
      await shot(`accepted-${parent.name}`);
    }
    for (const [index, parent] of parents.slice(0, 2).entries()) {
      await page.goto(`${UI}/${parent.id}/edit`);
      // Hold only the first real PUT, without changing its body or response.
      // The upstream publication completes before that same save is forwarded.
      const savePattern = `**/contexts/${parent.id}/draft/`;
      let raced = false;
      let raceError: unknown;
      if (index === 0) await page.route(savePattern, async (route) => {
        if (route.request().method() !== 'PUT' || raced) return route.continue();
        raced = true;
        try {
          const sent = route.request().postDataJSON();
          expect(sent.revision).toBe(parent.before.revision);
          const upstream = await leaf(`${prefix}-leaf-0`, 9);
          const stillSaved = await req(author, 'GET', `contexts/${parent.id}/draft/`);
          expect(stillSaved.revision).toBe(parent.before.revision);
          expect(stillSaved.resolved_lock.links.find((link: any) => link.skill_id === childId).version).toBe(8);
          events.push({ stage: 'upstream-during-inflight-save', parent: parent.id, pendingRevision: sent.revision, upstreamVersion: upstream.version.number });
        } catch (error) { raceError = error; }
        finally { await route.continue(); }
      });
      const input = page.getByRole('textbox', { name: /Editor content/ });
      await expect(input).toBeVisible({ timeout: UI_READY });
      const root = '# Reviewed synthetic policy\nUse explicitly accepted policy, never upstream latest.\n';
      await input.focus(); await input.press('Control+a');
      await page.evaluate(text => navigator.clipboard.writeText(text), root);
      await input.press('Control+v');
      await expect.poll(async () => (await req(author, 'GET', `contexts/${parent.id}/draft/`)).files.find((f: any) => f.path === 'AGENTS.md').content, { timeout: UI_READY }).toBe(root);
      if (index === 0) {
        await page.unroute(savePattern);
        if (raceError) throw raceError;
        expect(raced).toBe(wrong !== 'inflight-save');
        expect((await req(author, 'GET', `${childId}/`)).latest_version).toBe(9);
      }
      await page.getByRole('textbox', { name: 'Context purpose', exact: true }).fill('Reviewed purchasing purpose; retain the accepted shared policy.');
      await expect.poll(async () => (await req(author, 'GET', `contexts/${parent.id}/draft/`)).context_spec.description, { timeout: UI_READY }).toBe('Reviewed purchasing purpose; retain the accepted shared policy.');
      const saved = await req(author, 'GET', `contexts/${parent.id}/draft/`);
      expect(saved.revision).toBeGreaterThan(parent.before.revision);
      expect(saved.resolved_lock.links.find((l: any) => l.skill_id === childId).version).toBe(wrong === 'autosave' ? 99 : 8);
      events.push({ stage: 'autosave', parent: parent.id, revision: saved.revision, digest: saved.digest });
      await page.getByRole('tab', { name: 'Tests', exact: true }).click();
      await page.getByRole('button', { name: 'Save and open answer check', exact: true }).click();
      await page.getByRole('textbox', { name: 'Test request', exact: true }).fill(prompt);
      await page.getByRole('textbox', { name: 'Expected answer contains', exact: true }).fill('approved-response');
      await page.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.', exact: true }).check();
      await page.getByRole('combobox', { name: /^Task model/ }).click();
      await page.getByRole('option', { name: 'gpt-4o-mini', exact: true }).click();
      await page.getByRole('textbox', { name: 'Maximum estimated cost (USD)', exact: true }).fill('2');
      await page.getByRole('combobox', { name: /^Compare with/ }).click();
      await page.getByRole('option', { name: /^v1( \(production\))?$/ }).click();
      const previewResponse = page.waitForResponse(res => res.url().endsWith(`/contexts/${parent.id}/test-runs/preview/`) && res.request().method() === 'POST');
      await page.getByRole('button', { name: 'Preview answer check', exact: true }).click();
      const testPreview = (await (await previewResponse).json()).result;
      const handle = `s-${createHash('sha256').update(JSON.stringify([testPreview.fingerprint, childId])).digest('hex').slice(0, 16)}`;
      // Fixture-only transport configuration. The real worker chooses and reads
      // the accepted child from its immutable context snapshot, never a DB seed.
      await fake.dispose();
      await fake.register([toolRule(prompt, 0, 'load_skill', { skill_id: handle }), answerRule(prompt, 1, 'approved-response')]);
      await page.getByRole('checkbox', { name: 'The files sent to the model contain no answer keys or grading material.', exact: true }).check();
      await page.getByRole('checkbox', { name: 'I reviewed every file and input that will be sent to the model.', exact: true }).check();
      await page.getByRole('checkbox', { name: 'I acknowledge provider processing of this reviewed context and test request.', exact: true }).check();
      await expect(page.getByRole('button', { name: 'Run answer check', exact: true })).toBeEnabled();
      await shot(`whole-context-preview-${parent.name}`);
      const started = page.waitForResponse(res => res.url().endsWith(`/contexts/${parent.id}/test-runs/`) && res.request().method() === 'POST');
      await page.getByRole('button', { name: 'Run answer check', exact: true }).click();
      const startResponse = await started;
      expect(startResponse.status()).toBe(202);
      const runId = (await startResponse.json()).result.id;
      await expect.poll(async () => (await req(author, 'GET', `contexts/${parent.id}/test-runs/${runId}/`)).status, { timeout: 180_000 }).toBe('completed');
      const summary = await req(author, 'GET', `contexts/${parent.id}/test-runs/${runId}/`);
      expect(summary.candidate.digest).toBe(wrong === 'tested-digest' ? 'not-the-reviewed-digest' : saved.digest);
      expect(summary.reference.version).toBe(1);
      const attempts = await scopeProbe.pg('SELECT variant, root_fully_delivered, answer_verdict FROM model_hub_hubskilltestattempt WHERE run_id = $1 ORDER BY variant', [runId]);
      expect(attempts).toEqual(['candidate', 'reference'].map(variant => ({ variant, root_fully_delivered: true, answer_verdict: 'pass' })));
      const modelLedger = await fake.ledger();
      const delivered = modelLedger.filter(row => row.turn === 1).flatMap(row => (row.messages || []).filter(message => message.role === 'tool').map(message => JSON.parse(message.content!).text));
      expect(delivered).toHaveLength(2);
      expect(delivered.some(text => text.includes('policy revision 8.'))).toBe(true);
      expect(delivered.some(text => text.includes('policy revision 7.'))).toBe(true);
      expect(delivered.some(text => text.includes('policy revision 9.'))).toBe(wrong === 'model-child');
      events.push({ stage: 'whole-context-test', parent: parent.id, summary, attempts, modelLedger });
      await expect(page.getByText('Answer passed', { exact: true })).toHaveCount(2, { timeout: UI_READY });
      await expect(page.getByRole('button', { name: 'Cancel context test', exact: true })).not.toBeVisible();
      await page.getByRole('region', { name: 'Latest result' }).scrollIntoViewIfNeeded();
      await shot(`whole-context-result-${parent.name}`);
      await page.goto(`${UI}/${parent.id}/edit`);
      await page.getByRole('button', { name: 'Continue to publish', exact: true }).click();
      const reviewed = page.getByRole('checkbox', { name: /^I've checked the (?:changes|\d+ changed files?)(?: against v\d+)?(?: and the \d+ check warnings?)?$/ });
      await reviewed.check();
      const warning = page.getByRole('checkbox', { name: "I've read the check warnings for this version.", exact: true });
      if (await warning.count()) await warning.check();
      await shot(`review-${parent.name}`);
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
    // Runtime sessions over the public consumption API, opened before any label
    // moves. This checks server session identity; native model delivery is
    // measured by the registered J3 driver, not by this flow.
    const initialRoot = '# Synthetic policy\nConsult approved linked skills.\n';
    const reviewedRoot = '# Reviewed synthetic policy\nUse explicitly accepted policy, never upstream latest.\n';
    const versionId = async (id: string, n: number) => (await req(author, 'GET', `contexts/${id}/versions/${n}/inspection/`)).manifest.version_id;
    const expiresAt = new Date(Date.now() + 3_600_000).toISOString();
    const consume = async (credential: string, method: string, path: string, data?: unknown): Promise<any> => {
      const response = await request.fetch(`${E2E.apiUrl}${API}${path}`, { method, headers: { Authorization: `Context ${credential}`, 'Idempotency-Key': randomUUID() }, data });
      const body = await response.json();
      expect(response.ok(), JSON.stringify({ path, status: response.status(), body })).toBeTruthy();
      return body.result;
    };
    const bindingBody = (binding: any, selector: unknown) => ({ name: binding.name, kind: binding.kind, selector, update_mode: binding.update_mode,
      profile: binding.profile, profile_version: binding.profile_version, repository: binding.repository, expected_report_interval_seconds: binding.expected_report_interval_seconds });
    const runtimes: any[] = [];
    for (const parent of parents) {
      const binding = await req(owner, 'POST', `contexts/${parent.id}/bindings/`, { name: `${parent.name}-runtime`, kind: 'runtime', selector: { version: 1 },
        update_mode: 'new_thread', profile: 'deepagents', profile_version: '0.4.11', repository: null, expected_report_interval_seconds: 3600 });
      const v1 = await versionId(parent.id, 1);
      const consumer = await req(owner, 'POST', 'contexts/credentials/', { purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: parent.id, labels: ['production'], version_ids: [v1] }], binding_ids: [], expires_at: expiresAt });
      const reporter = await req(owner, 'POST', 'contexts/credentials/', { purpose: 'report', grants: [], binding_ids: [binding.id], expires_at: expiresAt });
      const thread = { client_session_key: randomUUID(), incarnation_id: randomUUID(), resume_session_id: null, adapter_version: 'e2e-j3', runtime_version: 'e2e', runtime_config_digest: `sha256:${'0'.repeat(64)}` };
      const initial = await consume(consumer.secret, 'POST', `contexts/bindings/${binding.id}/sessions/`, thread);
      expect([initial.history_mode, initial.version_id]).toEqual(['fresh', v1]);
      runtimes.push({ binding, consumer, reporter, thread, initial, v1, sequence: 0 });
    }
    const deliver = (runtime: any, session: any, incarnation: string, content: string) => consume(runtime.reporter.secret, 'POST', `contexts/bindings/${runtime.binding.id}/receipts/`, {
      event_id: randomUUID(), session_id: session.session_id, instance_id: incarnation, sequence: ++runtime.sequence, stage: 'instructions_delivered',
      version_id: session.version_id, digest: session.digest, materialization_digest: null, observed_at: new Date().toISOString(),
      details: { path: 'AGENTS.md', sha256: createHash('sha256').update(content).digest('hex'), ranges: [[0, content.length]], runtime_version: 'e2e', adapter_version: 'e2e-j3', diagnostic_codes: [], trace_ref: null } });
    for (const runtime of runtimes) await deliver(runtime, runtime.initial, runtime.thread.incarnation_id, initialRoot);
    const reviewerContext = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 } });
    await reviewerContext.addInitScript(authInitScript, { access: owner.tokens.access, refresh: owner.tokens.refresh, organizationId: owner.organizationId, workspaceId: owner.workspaceId });
    await reviewerContext.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), owner.organizationId);
    const reviewerPage = await reviewerContext.newPage();
    reviewerPage.setDefaultTimeout(UI_READY);
    try {
      for (const parent of parents.slice(0, 2)) {
        await page.goto(`${UI}/${parent.id}?version=2`);
        await page.getByRole('tab', { name: 'Versions', exact: true }).click();
        await page.getByRole('button', { name: 'Request release for v2', exact: true }).click();
        await page.getByLabel('Assigned reviewer').click();
        await page.getByRole('option', { name: `E2E ${scopeActors.prefix}-a`, exact: true }).click();
        await page.getByRole('button', { name: 'Continue', exact: true }).click();
        await page.getByRole('checkbox', { name: "I've checked this version, where it's going and the evidence I'm attaching.", exact: true }).check();
        const submitted = page.waitForResponse(res => res.url().endsWith('/release-requests/') && res.request().method() === 'POST');
        await page.getByRole('button', { name: 'Send request', exact: true }).click();
        const response = await submitted;
        expect(response.status()).toBe(201);
        const release = (await response.json()).result;
        expect((await req(author, 'GET', `contexts/${parent.id}/`)).context.labels).toEqual([]);
        await reviewerPage.goto(`${UI}/${parent.id}/review?request=${release.id}`);
        const reviewed = reviewerPage.getByRole('checkbox', { name: /^I've checked the (?:changes|\d+ changed files?), the evidence and what this release will affect\.$/ });
        await expect(reviewed).toBeEnabled();
        await reviewed.check();
        await reviewerPage.getByRole('button', { name: 'Approve release', exact: true }).click();
        // The acknowledgement is keyed to the exact packet revision and fingerprint, so it carries
        // across pending -> approved for the unchanged revision; any renewal clears it (J4).
        await expect(reviewed).toBeChecked();
        await reviewerPage.getByRole('button', { name: 'Release to production', exact: true }).click();
        await reviewerPage.getByRole('button', { name: 'Move production', exact: true }).click();
        await expect(reviewerPage.getByText('Release receipt', { exact: true })).toBeVisible();
        const labels = await scopeProbe.pg('SELECT to_version, actor_id::text FROM model_hub_hubskilllabelevent WHERE skill_id = $1', [parent.id]);
        expect(labels).toEqual([{ to_version: wrong === 'authorized-label' ? 99 : 2, actor_id: owner.userId }]);
        events.push({ stage: 'independent-parent-release', parent: parent.id, requestId: release.id, labels });
        await expect(reviewerPage.getByRole('dialog')).toHaveCount(0);
        await reviewerPage.getByText('Release receipt', { exact: true }).scrollIntoViewIfNeeded();
        const image = await reviewerPage.screenshot();
        if (evidence) await writeFile(`${evidence}/shots/authorized-${parent.name}.png`, image);
      }
    } finally { await reviewerContext.close(); }
    try {
      for (const [i, parent] of parents.entries()) {
        const runtime = runtimes[i];
        if (i < 2) runtime.binding = await req(owner, 'PATCH', `contexts/bindings/${runtime.binding.id}/`, { revision: runtime.binding.revision, binding: bindingBody(runtime.binding, { label: 'production' }) });
        const resumed = await consume(runtime.consumer.secret, 'POST', `contexts/bindings/${runtime.binding.id}/sessions/`, { ...runtime.thread, resume_session_id: runtime.initial.session_id });
        expect([resumed.history_mode, resumed.session_id, resumed.version_id, resumed.digest]).toEqual(['resumed', runtime.initial.session_id, wrong === 'resumed-identity' ? 'substituted' : runtime.v1, runtime.initial.digest]);
        const clean = { ...runtime.thread, client_session_key: randomUUID(), incarnation_id: randomUUID() };
        const fresh = await consume(runtime.consumer.secret, 'POST', `contexts/bindings/${runtime.binding.id}/sessions/`, clean);
        expect([fresh.history_mode, fresh.version_id]).toEqual(['fresh', i < 2 ? await versionId(parent.id, 2) : runtime.v1]);
        await deliver(runtime, fresh, clean.incarnation_id, i < 2 ? reviewedRoot : initialRoot);
        await page.goto(`${UI}/${parent.id}`);
        await page.getByRole('tab', { name: 'Connected apps', exact: true }).click();
        const row = page.getByRole('table', { name: 'Context delivery observations' }).getByRole('row').filter({ hasText: `${parent.name}-runtime` });
        const state = wrong === 'session-ui' ? 'Matches target' : i < 2 ? 'Old thread retained' : 'Pinned as intended';
        await expect(row.getByText(state, { exact: true })).toBeVisible();
        await expect(row.getByText(i < 2 ? 'v2' : 'v1', { exact: true }).first()).toBeVisible();
        events.push({ stage: 'runtime-sessions', parent: parent.id, initial: runtime.initial.version_id, resumed: resumed.version_id, fresh: fresh.version_id });
        await shot(`sessions-${parent.name}`);
      }
    } finally {
      for (const runtime of runtimes) {
        const remove = async (path: string, data?: unknown) => {
          const response = await request.fetch(`${E2E.apiUrl}${API}${path}`, { method: 'DELETE', headers: scopeActors.headers(owner), data });
          expect(response.ok(), path).toBeTruthy();
        };
        for (const credential of [runtime.consumer, runtime.reporter]) await remove(`contexts/credentials/${credential.id}/`);
        await remove(`contexts/bindings/${runtime.binding.id}/`, { revision: runtime.binding.revision });
      }
    }
    events.push({ stage: 'model-boundary-text-tests-only', ledger: await fake.ledger(), limitation: 'Session identity here is server-side; native fresh/resumed model delivery is measured by the registered J3 driver.' });
  } finally {
    if (testInfo.status !== testInfo.expectedStatus) await shot('failure');
    await testInfo.attach('j3-observations', { body: JSON.stringify(events), contentType: 'application/json' });
    if (evidence) { await mkdir(evidence, { recursive: true }); await writeFile(`${evidence}/observations.json`, JSON.stringify(events, null, 2)); }
    await context.close();
    await owner.api.delete(`/model-hub/api-keys/${connectionId}/`);
    await fake.dispose();
    if (evidence && page.video()) await page.video()!.saveAs(`${evidence}/journey.webm`);
  }
});
