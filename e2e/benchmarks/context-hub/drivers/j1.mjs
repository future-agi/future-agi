import assert from 'node:assert/strict';
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises';
import { relative, resolve } from 'node:path';
import { unzipSync } from 'fflate';
import { chromium, expect as playwrightExpect } from '@playwright/test';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue, sha256 } from '../../../lib/skills-r3/canonical.mjs';
import { filesDigest, performUpdate } from './j1-update.mjs';
import { deriveDelivery, observeCodexDelivery } from './j1-native.mjs';

const expect = playwrightExpect.configure({ timeout: 60_000 });
const canonicalEqual = (a, b) => JSON.stringify(a) === JSON.stringify(b);

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
  let credentialId;
  let secret;
  let credentialBefore;
  let credentialRevoked = false;
  let sdkEnv;
  let install;
  let boundaryArtifact;
  // Working context of the single operator. Opening a product screen is already
  // an S transition (go); returning from the browser to the terminal is another
  // working-context transition and is counted, never assumed free.
  let place = null;
  const terminal = async operator => {
    if (place === 'browser') await operator.transition('switch-to-terminal', async () => {});
    place = 'terminal';
  };
  const plans = [];
  const imported = [];
  const attempts = [];
  const update = {};
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
    context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] });
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
  // One inventory command also writes the private migration packet the browser
  // review consumes, so no source is chosen twice and no identity is copied.
  // It holds private source bytes, so it stays beside the private fixture, never
  // in retained evidence; the report keeps only its digest.
  const packetPath = resolve(input.folder, '..', `migration-packet-${runId}.json`);
  const local = async operator => {
    await terminal(operator);
    const result = await operator.command('local-library-inventory', 'bash', [resolve(input.productRoot, 'bin/skills-inventory'), ...baseArgs,
      '--packet', packetPath], {
      cwd: input.productRoot, env: { PATH: process.env.PATH, SKILLS_INVENTORY_PYTHON: input.python },
    });
    const report = JSON.parse(result.stdout);
    requireValue(report.migration_packet?.sha256 === sha256(await readFile(packetPath)), 'The packet must be the one this inventory wrote');
    return report;
  };
  const supportedSubset = report => {
    const proposed = report.proposed_supported_subset;
    assert.equal(proposed.compiled_closure.fits, true);
    assert.equal(proposed.excluded.length, 1);
    assert.deepEqual([...proposed.selected].sort(), [...input.selected].sort());
    return proposed;
  };
  const supportedNames = () => input.names.slice(0, 14);
  const sourceHash = async () => {
    const tree = async root => (await Promise.all((await readdir(root, { withFileTypes: true })).map(async entry => {
      const path = resolve(root, entry.name);
      return entry.isDirectory() ? tree(path) : [[relative(input.folder, path), sha256(await readFile(path))]];
    }))).flat();
    return digest({ exchange: sha256(await readFile(input.exchange)), update: sha256(await readFile(input.update.exchange)), folder: (await tree(input.folder)).sort() });
  };
  const sourceAtStart = await sourceHash();
  // SDK CLI as a developer runs it: the credential travels only in the environment.
  const sdk = async (operator, target, args) => (await terminal(operator), operator.command(target, input.sdkPython, ['-I', '-m', 'fi.context', ...args],
    { cwd: input.project, env: sdkEnv, timeoutMs: 180_000 }));
  const walk = async (root, base = root) => (await Promise.all((await readdir(root, { withFileTypes: true })).map(async entry => {
    const path = resolve(root, entry.name);
    return entry.isDirectory() ? walk(path, base) : [relative(base, path)];
  }))).flat().sort();
  const projectFiles = async () => Promise.all((await walk(input.project)).map(async path => ({ path, content: await readFile(resolve(input.project, path), 'utf8') })));
  // Plans and diffs carry private text and authorization leases; retain only hashes.
  const redactPlan = value => ({ sha256: digest(value), keys: Object.keys(value ?? {}).sort() });
  const resolveWithCredential = async signal => {
    const response = await fetch(`${input.apiUrl}/model-hub/skills/contexts/resolve/?name=${encodeURIComponent(input.contextName)}&version=1`,
      { headers: { Authorization: `Context ${secret}` }, signal, redirect: 'error' });
    const body = await response.json().catch(() => ({}));
    return { status: response.status, code: body?.error?.code ?? body?.code ?? body?.result?.code ?? null, version_id: body?.result?.version_id ?? body?.result?.manifest?.version_id ?? null };
  };
  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      const operator = new MeasuredOperator({ ledger, actor: 'owner', step: stage.id, phase: stage.phase, signal });
      const click = (page, role, name) => operator.click(page.getByRole(role, { name, exact: true }), name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-$/, ''));
      const go = (page, path, target) => { place = 'browser'; return operator.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: 'owner', step: stage.id, phase: stage.phase, target }, () => page.goto(path))); };
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
          // The same report already projects the supported subset's compiled
          // closure; the owner reviews it with the blocked member as the exception.
          const decision = { packet_sha256: digest(inventory), preconditions_sha256: digest(inventory.members.map(member => member.manifest_digest)) };
          await operator.review('supported-subset', inventory.members.map((member, i) => member.blockers.length ? 'binary-member' : `member-${i}`), ['binary-member'], async () => {
            assert.equal(supportedSubset(inventory).selected.length, 14);
          }, decision);
          await receipt('filesystem', inventory, decision);
          return;
        }
        if (stage.id === 'migration-preview') {
          if (!inventory) {
            // Configured runs still measure any fresh preflight; a prior report
            // cannot be silently treated as an action-free current inventory.
            inventory = await local(operator);
            supportedSubset(inventory);
            ledger.fault('unsupported-member', 'binary-member');
          }
          // Both retained sources come from ONE packet chosen in ONE import session
          // and are reviewed together. Identities travel inside the packet.
          const page = await newPage();
          await go(page, '/dashboard/skills', 'migration-library');
          await operator.click(page.getByRole('button', { name: /^(Import a folder or ZIP|Import)$/ }), 'open-import');
          await click(page, 'tab', 'From another tool');
          const previews = [];
          const onPreview = response => {
            const url = response.url();
            if (response.request().method() === 'POST' && url.includes('/intake/') && url.endsWith('/preview/')) previews.push(response);
          };
          page.on('response', onPreview);
          await ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'owner', step: stage.id, phase: stage.phase, target: 'select-inventory-packet' },
            () => page.getByLabel('Inventory migration packet', { exact: true }).setInputFiles(packetPath));
          await expect.poll(() => previews.length).toBe(2);
          page.off('response', onPreview);
          for (const response of previews) {
            assert.equal(response.status(), 200);
            const result = (await response.json()).result;
            const plan = await read(`intake/${result.id}/`, signal);
            assert.equal(plan.id, result.id);
            plans.push({ kind: response.url().endsWith('/langfuse/preview/') ? 'exchange' : 'folder', page, plan });
          }
          assert.deepEqual(plans.map(({ kind }) => kind), ['exchange', 'folder']);
          await expect(page.getByRole('heading', { name: 'Review all items', exact: true })).toBeVisible();
          await expect(page.getByRole('table', { name: 'Retained migration items' }).getByRole('row')).toHaveCount(16);
          await page.screenshot({ path: resolve(output, 'shots', 'preview-reviewed-set.png'), fullPage: true });
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
          // One reviewed decision over the whole set: the blocked member is an
          // explicitly acknowledged exception, rights are confirmed once for the
          // 14 listed items, and each imported draft is published without labels.
          const page = plans[0].page;
          lastPage = page;
          let dropped = false;
          await page.route('**/skills/intake/*/accept/', async route => {
            if (!dropped || attempts.length === 1) attempts.push({ bodyHash: digest(route.request().postData()), key: route.request().headers()['idempotency-key'] });
            if (!dropped) {
              const response = await route.fetch();
              assert.equal(response.status(), 200);
              dropped = true;
              ledger.fault('lost-import-response', 'response-lost-after-commit');
              await route.abort('failed');
            } else await route.continue();
          });
          await operator.click(page.getByRole('checkbox', { name: /^Leave out 1 skill/ }), 'exclude-unsupported');
          await operator.click(page.getByRole('checkbox', { name: /^I confirm my rights to copy all 14 items/ }), 'confirm-source-rights');
          const packet = plans.map(({ plan }) => plan);
          const decision = { packet_sha256: digest(packet), preconditions_sha256: digest(packet.map(plan => plan.fingerprint)) };
          await ledger.measure({ dimension: 'A', primitive: 'approve', actor: 'owner', step: stage.id, phase: stage.phase, target: 'import-and-publish-reviewed-set',
            items: input.names.map((_, i) => i === 14 ? 'binary-member' : `member-${i}`), exceptionItems: ['binary-member'], decision }, async () => {
            await page.getByRole('button', { name: 'Import and publish 14 items', exact: true }).click();
            await expect(page.getByText('14 versions published without labels. Production was not changed.', { exact: true })).toBeVisible({ timeout: 300_000 });
          });
          await page.unroute('**/skills/intake/*/accept/');
          // The product, not the operator, settled the lost response by replaying
          // the exact body under the original operation key.
          assert.equal(attempts.length, 2);
          assert.deepEqual(attempts[0], attempts[1]);
          await expect(page.getByText(/lost response settled by exact replay/)).toHaveCount(1);
          for (const { plan } of plans) {
            const receipts = await read(`intake/${plan.id}/receipts/?page_size=100`, signal);
            assert.equal(receipts.count, plan.inventory.items.filter(item => !item.blockers.length).length);
            assert.equal(new Set(receipts.results.map(row => row.item_key)).size, receipts.count);
            for (const row of receipts.results) imported.push({ ...row, name: plan.inventory.items.find(item => item.key === row.item_key).name });
          }
          assert.equal(imported.length, 14);
          for (const row of imported) {
            const versions = await read(`${row.result.resource_id}/versions/`, signal);
            assert.equal(versions.count, 1);
            assert.equal(versions.results[0].digest, filesDigest(input.files[row.name]));
            assert.equal((await read(`${row.result.resource_id}/labels/`, signal)).length, 0);
          }
          await page.screenshot({ path: resolve(output, 'shots', 'published-reviewed-set.png'), fullPage: true });
          await receipt('api', { imported, attempts });
          return;
        }
        if (stage.id === 'assemble-context') {
          // The packet's proposed context (name, purpose, AGENTS.md from the local
          // inventory) is assembled in the same review over the published set.
          const page = plans[0].page;
          lastPage = page;
          await expect(page.getByRole('heading', { name: 'Agent context from this migration', exact: true })).toBeVisible();
          // Prefilled values are only free if they are what the owner wants.
          assert.equal(await page.getByLabel('Immutable name', { exact: true }).inputValue(), input.contextName);
          assert.equal(await page.getByLabel('Purpose', { exact: true }).inputValue(), input.description);
          assert.equal(await page.getByLabel('AGENTS.md', { exact: true }).inputValue(), await readFile(input.agents, 'utf8'));
          const created = page.waitForResponse(res => res.url().endsWith('/contexts/') && res.request().method() === 'POST');
          const previewed = page.waitForResponse(res => res.url().endsWith('/dependencies/preview/') && res.request().method() === 'POST');
          await click(page, 'button', 'Preview agent context');
          const createdResponse = await created;
          assert.equal(createdResponse.status(), 201);
          const createdReceipt = (await createdResponse.json()).result;
          contextId = createdReceipt.context_id ?? createdReceipt.context?.id;
          requireValue(contextId, 'Context creation must retain an identity');
          const previewResponse = await previewed;
          assert.equal(previewResponse.status(), 200);
          const preview = (await previewResponse.json()).result;
          await expect(page.getByRole('table', { name: 'Pinned links' }).getByRole('row')).toHaveCount(15);
          // Nothing is linked yet: the draft holds only AGENTS.md until acceptance.
          const draft = await read(`contexts/${contextId}/draft/`, signal);
          assert.equal(draft.resolved_lock.links.length, 0);
          assert.deepEqual(preview.resolved_lock.links.map(link => link.skill_id).sort(), imported.map(row => row.result.resource_id).sort());
          const decision = { packet_sha256: digest(preview), preconditions_sha256: digest({ revision: draft.revision, files: draft.files }) };
          await operator.review('agent-context-candidate', preview.resolved_lock.links.map(link => link.mount), [], async () => {
            assert.ok(preview.resolved_lock.links.every(link => link.version === 1 && link.requested.version === 1));
            await expect(page.getByText(`Compiled candidate ${preview.candidate_digest} · 14 pinned links · private draft of ${input.contextName}`, { exact: true })).toBeVisible();
          }, decision);
          await operator.click(page.getByRole('checkbox', { name: 'Accept these 14 exact pinned versions and this compiled candidate.', exact: true }), 'accept-pinned-candidate');
          const warning = page.getByRole('checkbox', { name: "I've read the check warnings for this version.", exact: true });
          if (await warning.count()) await operator.click(warning, 'acknowledge-context-warnings');
          const published = page.waitForResponse(res => res.url().endsWith(`/contexts/${contextId}/draft/publish/`) && res.request().method() === 'POST');
          await ledger.measure({ dimension: 'A', primitive: 'approve', actor: 'owner', step: stage.id, phase: stage.phase, target: 'publish-agent-context',
            items: preview.resolved_lock.links.map(link => link.mount), exceptionItems: [], decision }, () => page.getByRole('button', { name: 'Publish agent context', exact: true }).click());
          assert.equal((await published).status(), 201);
          await expect(page.getByText(/^Published .* v1 · sha256:[a-f0-9]{64}\. No labels moved; production is unchanged\.$/)).toBeVisible();
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
          // The published closure is exactly the local projection of the reviewed supported subset.
          const projected = supportedSubset(inventory).compiled_closure;
          assert.equal(manifest.length, projected.file_count);
          assert.deepEqual(manifest.map(file => file.path).sort(), [...projected.paths].sort());
          assert.deepEqual((await read(`contexts/${contextId}/`, signal)).release_state.labels, []);
          assert.equal(candidate.manifest.digest, preview.candidate_digest, 'The published context must be the reviewed candidate');
          await page.screenshot({ path: resolve(output, 'shots', 'published-agent-context.png'), fullPage: true });
          await receipt('api', { ...candidate, complete_manifest: manifest }, decision);
          return;
        }
        if (stage.id === 'credential-and-install') {
          requireValue(candidate && contextId, 'Published immutable context must precede installation');
          // The returned immutable version's pilot credential is issued where it
          // was published. No released package or verified profile is claimed, so
          // the operator still uses the SDK CLI's explicit preview opt-in.
          const page = plans[0].page;
          lastPage = page;
          await expect(page.getByText(/A released package and a verified host\/version profile are required/)).toBeVisible();
          const issued = page.waitForResponse(res => res.url().endsWith('/contexts/credentials/') && res.request().method() === 'POST');
          await click(page, 'button', 'Create key');
          const issuedResponse = await issued;
          assert.equal(issuedResponse.status(), 201);
          credentialId = (await issuedResponse.json()).result.id;
          await click(page, 'button', 'Copy key secret');
          await expect(page.getByText('Copied. Store it in your secret manager.', { exact: true })).toBeVisible();
          // The one-time secret travels by clipboard from the browser into the
          // developer's terminal environment: a manual information transfer (H).
          secret = await operator.handoff('credential-secret-to-sdk-environment', () => page.evaluate(() => navigator.clipboard.readText()));
          requireValue(/^[0-9a-f-]{36}\.[A-Za-z0-9_-]{40,}$/.test(secret), 'One-time credential secret required');
          await page.evaluate(() => navigator.clipboard.writeText(''));
          await expect(page.getByRole('row').filter({ has: page.getByRole('button', { name: `Actions for credential ${credentialId.slice(0, 8)}`, exact: true }) })).toBeVisible();
          await page.screenshot({ path: resolve(output, 'shots', 'pilot-credential.png') });
          const listed = (await read('contexts/credentials/?page_size=100', signal)).results.find(row => row.id === credentialId);
          assert.deepEqual(listed.grants, [{ kind: 'agent_context', resource_id: contextId, labels: [], version_ids: [candidate.manifest.version_id] }]);
          assert.equal(listed.purpose, 'consume');
          credentialBefore = await resolveWithCredential(signal);
          assert.equal(credentialBefore.status, 200);
          await page.close();
          await operator.configurationEdit('sdk-credential-environment', async () => {
            sdkEnv = { PATH: process.env.PATH, HOME: process.env.HOME, FI_CONTEXT_ORIGIN: input.apiUrl, FI_CONTEXT_ORGANIZATION_ID: input.actor.organizationId,
              FI_CONTEXT_WORKSPACE_ID: input.actor.workspaceId, FI_CONTEXT_CREDENTIAL: secret };
          });
          const plan = resolve(output, 'install-plan.json');
          await sdk(operator, 'sdk-plan-install', ['plan', '--name', input.contextName, '--version', '1', '--client', 'codex', '--project', input.project,
            '--adopt-local', '--allow-preview', '--out', plan]);
          const inspected = JSON.parse((await sdk(operator, 'sdk-inspect-install', ['inspect', '--plan', plan, '--diff'])).stdout);
          const planBytes = await readFile(plan);
          const decision = { packet_sha256: sha256(planBytes), preconditions_sha256: digest(await projectFiles()) };
          await operator.review('install-plan', inspected.changes?.map((_, i) => `change-${i}`) ?? ['install-plan'], [], async () => {
            requireValue(JSON.stringify(inspected).includes('Preview profile'), 'Reviewed plan must state that installation is not delivery evidence');
          }, decision);
          await sdk(operator, 'sdk-apply-install', ['apply', '--plan', plan, '--quiescent']);
          install = { plan_sha256: sha256(planBytes), inspected: redactPlan(inspected) };
          await receipt('filesystem', { credential: { id: credentialId, grants: listed.grants, expires_at: listed.expires_at }, install });
          return;
        }
        if (stage.id === 'verify-delivery') {
          requireValue(install && sdkEnv, 'Reviewed install must precede delivery verification');
          const verified = JSON.parse((await sdk(operator, 'sdk-verify-bytes', ['verify', '--project', input.project])).stdout);
          // Bytes: compare the installed tree with the immutable API manifest, not the SDK's own verdict.
          const lock = JSON.parse(await readFile(resolve(input.project, '.futureagi-context.lock.json'), 'utf8'));
          assert.equal(lock.version_id, candidate.manifest.version_id);
          assert.equal(lock.digest, candidate.manifest.digest);
          const skills = [];
          for (const name of supportedNames()) {
            const path = `.agents/skills/${name}/SKILL.md`;
            const content = await readFile(resolve(input.project, path), 'utf8');
            assert.equal(content, input.files[name][0].content);
            assert.equal(await readFile(resolve(input.project, `.agents/skills/${name}/references/policy.md`), 'utf8'), input.files[name][1].content);
            skills.push({ name, path, content });
          }
          // One host session in the developer's project; the CLI run is the action.
          const boundary = await ledger.measure({ dimension: 'A', primitive: 'command', actor: 'owner', step: stage.id, phase: stage.phase, target: 'native-host-load' },
            () => observeCodexDelivery({ executable: input.codex.executable, executableSha256: input.codex.sha256, project: input.project, skills, signal }));
          boundaryArtifact = resolve(output, 'native-boundary.json');
          await writeFile(boundaryArtifact, JSON.stringify(boundary), { mode: 0o600, flag: 'wx' });
          const derived = deriveDelivery(boundary.requests, { names: skills.map(skill => skill.name) });
          assert.equal(derived.discovered.length, 14);
          await operator.review('delivery-evidence', ['bytes', 'discovery', 'delivery'], [], async () => {
            assert.ok(verified);
          });
          // The developer keeps working locally during the pilot: an unowned note
          // inside a managed tree and one edited managed file. Counted, not hidden.
          for (const [key, [path, content]] of Object.entries(input.localWork)) {
            await ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'owner', step: stage.id, phase: stage.phase, target: `local-work-${key}` },
              () => writeFile(resolve(input.project, path), content));
          }
          await receipt('model_boundary', { verified, derived, boundary_sha256: sha256(await readFile(boundaryArtifact)) });
          return;
        }
        if (stage.id === 'remove-and-revoke') {
          requireValue(update.exports?.length === 14 && sdkEnv && credentialId, 'Export and an installed credential must precede exit');
          const plan = resolve(output, 'removal-plan.json');
          await sdk(operator, 'sdk-plan-removal', ['remove', '--project', input.project, '--out', plan]);
          const inspected = JSON.parse((await sdk(operator, 'sdk-inspect-removal', ['inspect', '--plan', plan, '--diff'])).stdout);
          const planBytes = await readFile(plan);
          const decision = { packet_sha256: sha256(planBytes), preconditions_sha256: digest(await projectFiles()) };
          await operator.review('removal-plan', ['managed-files', 'managed-root-segment', 'retained-local-work'], ['retained-local-work'], async () => {
            const text = JSON.stringify(inspected);
            for (const [path] of Object.values(input.localWork)) requireValue(text.includes(path), 'Removal review must name every retained local path');
          }, decision);
          await sdk(operator, 'sdk-apply-removal', ['apply', '--plan', plan, '--quiescent']);
          // The exact context's Use tab is still open from the export; returning
          // from the terminal to that browser tab is a counted context switch.
          const page = update.exitPage;
          requireValue(page && !page.isClosed(), 'Export view must remain open for revocation');
          lastPage = page;
          await operator.transition('switch-to-browser', async () => { await page.bringToFront(); });
          place = 'browser';
          await operator.click(page.getByRole('button', { name: `Actions for credential ${credentialId.slice(0, 8)}`, exact: true }), 'credential-actions');
          await operator.click(page.getByRole('menuitem', { name: 'Revoke', exact: true }), 'choose-revoke');
          const revoked = page.waitForResponse(res => res.url().endsWith(`/contexts/credentials/${credentialId}/`) && res.request().method() === 'DELETE');
          await operator.approve('revoke-pilot-credential', [`credential-${credentialId.slice(0, 8)}`], () => page.getByRole('dialog').getByRole('button', { name: 'Revoke', exact: true }).click());
          assert.equal((await revoked).status(), 204);
          credentialRevoked = true;
          await expect(page.getByRole('row').filter({ has: page.getByRole('button', { name: `Actions for credential ${credentialId.slice(0, 8)}`, exact: true }) }).getByText('Revoked', { exact: true })).toBeVisible();
          await page.screenshot({ path: resolve(output, 'shots', 'credential-revoked.png') });
          await page.close();
          await receipt('filesystem', { removal_plan_sha256: sha256(planBytes), inspected: redactPlan(inspected), credential: credentialId }, decision);
          return;
        }
        if (await performUpdate(stage, { input, imported, plans, output, operator, ledger, signal, expect, read, record, receipt, newPage, click, go, contextId: () => contextId }, update)) return;
        throw new Error(`No completed public-interface stage evidence: ${stage.id}`);
      } catch (error) {
        await record('blocked-stage', { stage: stage.id, errorType: error.name, message: String(error.message).slice(0, 4000), stack: String(error.stack).split('\n').slice(0, 8), qualified: false });
        if (lastPage && !lastPage.isClosed()) await lastPage.screenshot({ path: resolve(output, 'shots', 'blocked.png') }).catch(() => {});
        throw error;
      } finally { signal.removeEventListener('abort', abort); }
    },
    async observe(check, { signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      // Every observation re-reads state after the final stage; stage-local
      // receipts and product toasts are never promoted to whole-job evidence.
      const observed = async (source, value, raw = value) => ({ source, value, artifact_sha256: await record(`observe-${check.id}`, { source, value, raw }) });
      const initial = await Promise.all(plans.map(({ plan }) => read(`intake/${plan.id}/`, signal)));
      requireValue(initial.length === 2 && update.fresh, 'Complete-job intake plans are required');
      const second = await read(`intake/${update.fresh.id}/`, signal);
      const receiptsOf = async id => (await read(`intake/${id}/receipts/?page_size=100`, signal)).results;
      if (check.id === 'complete-inventory') {
        requireValue(inventory, 'Local inventory report required');
        const apiNames = initial.flatMap(plan => plan.inventory.items.map(item => item.name)).sort();
        const localNames = inventory.members.map(member => member.name).sort();
        // Local preflight and the reviewed intake must list the same whole collection.
        return observed('api', canonicalEqual(apiNames, localNames) ? apiNames : { local: localNames, api: apiNames }, { inventory: digest(inventory), apiNames });
      }
      if (check.id === 'migration-mappings') {
        const rows = [];
        for (const plan of initial) {
          const receipts = await receiptsOf(plan.id);
          for (const row of receipts) {
            const item = plan.inventory.items.find(candidate => candidate.key === row.item_key);
            rows.push({ source: `${item.source.origin}|${item.source.project}|${item.source.resource}`, destination: row.result.resource_id });
          }
        }
        const updates = await receiptsOf(second.id);
        const same = updates.filter(row => {
          const item = second.inventory.items.find(candidate => candidate.key === row.item_key);
          const key = `${item.source.origin}|${item.source.project}|${item.source.resource}`;
          return rows.some(entry => entry.source === key && entry.destination === row.result.resource_id && entry.destination === item.destination_id);
        }).length;
        return observed('api', { members: rows.length, sources: rows.map(row => row.source).sort(),
          destinations_distinct: new Set(rows.map(row => row.destination)).size, second_import_same_destinations: same }, rows);
      }
      const leaves = imported.map(row => ({ id: row.result.resource_id, name: row.name }));
      if (check.id === 'source-production-unchanged') {
        let labels = 0;
        for (const leaf of leaves) labels += (await read(`${leaf.id}/labels/`, signal)).length;
        labels += (await read(`contexts/${contextId}/`, signal)).release_state.labels.length;
        const mappings = await Promise.all(update.exports.map(item => item.mapping));
        const sourceModified = mappings.some(mapping => mapping.exit.source_modified !== false);
        // The retained sources themselves must be byte-identical to the job's start.
        assert.equal(await sourceHash(), sourceAtStart, 'retained_source_changed');
        return observed('api', { resources: leaves.length + 1, labels, source_modified: sourceModified });
      }
      if (check.id === 'no-duplicate-import') {
        const library = await read('?page_size=100', signal);
        const ours = library.results.filter(row => input.names.includes(row.name));
        const versions = {};
        for (const row of ours) versions[row.name] = (await read(`${row.id}/versions/`, signal)).count;
        const initialReceipts = (await Promise.all(initial.map(plan => receiptsOf(plan.id)))).flat();
        return observed('api', { skills: ours.length, versions, initial_receipts: initialReceipts.length, update_receipts: (await receiptsOf(second.id)).length },
          { library_count: library.count, ids: ours.map(row => row.id) });
      }
      if (check.id === 'roundtrip-bytes') {
        const files = [];
        for (let index = 0; index < update.exports.length; index++) {
          const entries = unzipSync(await readFile(resolve(output, `export-${index}.zip`)));
          for (const [path, bytes] of Object.entries(entries)) if (path !== '.futureagi-skills.lock.json') files.push({ path, content: Buffer.from(bytes).toString('utf8') });
        }
        return observed('filesystem', files.sort((a, b) => (a.path < b.path ? -1 : 1)));
      }
      if (check.id === 'local-work-preserved') return observed('filesystem', await projectFiles());
      if (check.id === 'delivered-identity') {
        requireValue(boundaryArtifact, 'Native boundary artifact required');
        const boundary = JSON.parse(await readFile(boundaryArtifact, 'utf8'));
        return observed('model_boundary', { host: boundary.host, ...deriveDelivery(boundary.requests, { names: supportedNames() }) }, { boundary_sha256: sha256(await readFile(boundaryArtifact)) });
      }
      if (check.id === 'credential-revoked') {
        requireValue(credentialBefore && secret, 'Credential use before revocation must be observed');
        const after = await resolveWithCredential(signal);
        const listed = (await read('contexts/credentials/?page_size=100', signal)).results.find(row => row.id === credentialId);
        return observed('api', { before_status: credentialBefore.status, after_status: after.status, after_code: after.code, listed_revoked: Boolean(listed?.revoked_at) });
      }
      throw new Error(`Unknown J1 check: ${check.id}`);
    },
    async teardown() {
      // Failure paths still revoke the pilot credential; the secret never leaves memory.
      let revokedOrNone = !credentialId || credentialRevoked;
      if (!revokedOrNone) {
        const response = await fetch(`${input.apiUrl}/model-hub/skills/contexts/credentials/${credentialId}/`, { method: 'DELETE', headers, redirect: 'error' }).catch(() => null);
        revokedOrNone = response?.status === 204;
      }
      secret = undefined;
      sdkEnv = undefined;
      await context?.close();
      await browser?.close();
      return { qualified: revokedOrNone && (!browser || !browser.isConnected()) };
    },
  };
}
