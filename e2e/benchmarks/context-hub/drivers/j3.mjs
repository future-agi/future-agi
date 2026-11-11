import { chromium, expect as strictExpect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { randomUUID, createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import pg from 'pg';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue, canonical } from '../../../lib/skills-r3/canonical.mjs';

// The 17-file diffs take several seconds to render in a headless CPU browser.
const expect = strictExpect.configure({ timeout: 30_000 });

// Public UI authoring/review + public SDK runtime application. Synthetic provider
// transport only; no prior reports, SQL writes, hidden admin authoring or toast oracles.
export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J3', 'Use the assigned Linux cloud VM');
  const inputPath = process.env.J3_DRIVER_INPUT;
  requireValue(inputPath && process.env.J3_PYTHON, 'Private cloud input and installed SDK required');
  const input = JSON.parse(await readFile(inputPath, 'utf8'));
  for (const name of ['appUrl', 'apiUrl']) requireValue(new URL(input[name]).hostname === 'localhost', 'Owned full stack only');
  requireValue(input.parents?.length === 3 && input.leaves?.length === 15, 'Complete three-context, fifteen-skill initial condition required');
  const output = resolve(input.outputDir, runId);
  await mkdir(resolve(output, 'shots'), { recursive: true });
  const observations = { accepted: [], saved: [], jobs: [], releases: [], initial: [], runtime: [] };
  const contexts = new Map();
  const pool = new pg.Pool({ connectionString: process.env.E2E_PG_URL ?? 'postgresql://futureagi:futureagi@localhost:25432/futureagi', max: 2 });
  let browser, providerId;
  const ownedRuntimes = [];
  const namespace = `e2e-skills-${randomUUID()}`;
  const fakeRoot = 'http://localhost:28082';
  const headers = (actor) => ({ Authorization: `Bearer ${actor.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId });
  const read = async (path, signal, actor = input.author) => {
    const response = await fetch(`${input.apiUrl}/model-hub/skills/${path}`, { headers: headers(actor), signal });
    if (!response.ok) {
      // Fetched artifacts carry only the HTTP status and the machine error code.
      const code = await response.json().then((body) => (typeof body?.code === 'string' && /^[a-z0-9_]{1,64}$/.test(body.code) ? body.code : 'unknown'), () => 'unknown');
      requireValue(false, `Independent API observation unavailable (${response.status} ${code}) ${path.slice(0, 80)}`);
    }
    return (await response.json()).result;
  };
  const fake = async (path, method = 'GET', body) => {
    const response = await fetch(fakeRoot + path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15_000) });
    requireValue(response.ok, `Synthetic transport control failed (${response.status})`);
    return response.status === 204 ? null : response.json();
  };
  await fake('/scenarios', 'POST', { namespace, rules: [], github: [] });
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    return digest(value);
  };
  const open = async (actorName) => {
    if (contexts.has(actorName)) return contexts.get(actorName).page;
    browser ||= await chromium.launch({ headless: true });
    const context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 } });
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await context.addInitScript((actor) => {
      localStorage.setItem('accessToken', actor.access); localStorage.setItem('refreshToken', actor.refresh); localStorage.setItem('rememberMe', 'true');
      sessionStorage.setItem('organizationId', actor.organizationId); sessionStorage.setItem('workspaceId', actor.workspaceId); sessionStorage.setItem('workspaceOrgId', actor.organizationId);
    }, input[actorName]);
    const page = await context.newPage(); page.setDefaultTimeout(60_000);
    contexts.set(actorName, { context, page });
    return page;
  };
  const followers = input.parents.slice(0, 2);
  const child = input.leaves[0];
  const compiled = (draft) => draft.resolved_lock.links.find((link) => link.skill_id === child.id)?.version;
  const receipt = async (stage, ledger, value, decision = null, source = 'api') => ledger.stage({ step: stage.id, phase: stage.phase, source, artifactHash: await record(stage.id, value), actionSequences: [], ...(decision ? { decision } : {}) });
  const runtimeArgs = (operation, index) => [resolve('e2e/harness/skills-r3/j3-runtime.py'), operation, inputPath, String(index)];
  const operationsArgs = (operation, value) => [resolve('e2e/harness/skills-r3/j3-operations.py'), operation, inputPath, String(value)];
  const snapshot = (name, page) => page.screenshot({ path: resolve(output, 'shots', `${name}.png`) });
  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      const actorName = stage.id === 'publish-leaf' ? 'owner' : 'author';
      const page = await open(actorName);
      const operator = new MeasuredOperator({ ledger, actor: actorName, step: stage.id, phase: stage.phase, signal });
      const watch = (predicate) => { const pending = page.waitForResponse(predicate); pending.catch(() => {}); return pending; };
      const click = (name, target, role = 'button') => operator.click(page.getByRole(role, { name, exact: true }), target);
      const fill = (name, value, target) => operator.fill(page.getByRole('textbox', { name, exact: true }), value, target);
      const navigate = (path, target) => operator.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: actorName, step: stage.id, phase: stage.phase, target }, () => page.goto(path)));
      const command = async (target, args) => {
        const result = await operator.command(target, process.env.J3_PYTHON, args, { cwd: process.cwd(), env: process.env, timeoutMs: 180_000 });
        return JSON.parse(result.stdout);
      };
      const edit = (content, target) => operator.configurationEdit(target, async () => {
        const editor = page.getByRole('textbox', { name: /Editor content/ });
        await editor.focus(); await editor.press('Control+a'); await page.evaluate(text => navigator.clipboard.writeText(text), content); await editor.press('Control+v');
      });
      const publishParent = async (parent, index, expected, decision = null) => {
        // A person sees the editor settle before opening the review; the guard reads
        // rendered state, so do not race the last acknowledged mutation.
        await expect(page.getByRole('status').filter({ hasText: /^Saved$/ })).toBeVisible();
        await click('Review changes', `parent-${index}-open-review`);
        const reviewed = page.getByRole('checkbox', { name: 'I reviewed the saved candidate against its content origin and current publication head.', exact: true });
        await expect(reviewed).toBeVisible({ timeout: 60_000 });
        await operator.review(`parent-${index}-content-review`, ['standing-root', ...input.leaves.map((_, n) => `linked-skill-${n}`), 'compiled-descriptor'], [], () => reviewed.check(), decision);
        const warning = page.getByRole('checkbox', { name: 'I acknowledge the static warnings for this exact candidate.', exact: true });
        if (await warning.count()) await operator.click(warning, `parent-${index}-warnings`);
        await snapshot(`parent-${index}-publication-review`, page);
        await click('Acknowledge reviewed head', `parent-${index}-acknowledge-head`);
        await expect(page.getByRole('button', { name: 'Publish version', exact: true })).toBeVisible();
        await operator.review(`parent-${index}-confirmed-head`, ['standing-root', 'accepted-lock', 'publication-head'], [], () => reviewed.check(), decision);
        if (await warning.count()) await operator.click(warning, `parent-${index}-confirmed-warnings`);
        await click('Publish version', `parent-${index}-publish`);
        await expect.poll(async () => (await read(`contexts/${parent.id}/`, signal)).context.latest_version, { timeout: 60_000 }).toBe(expected);
      };
      try {
        if (stage.id === 'configure-three-contexts') {
          requireValue(input.parents.every(parent => parent.id === null), 'Cold fixture must not pre-author the parents');
          for (const [index, parent] of input.parents.entries()) {
            await navigate('/dashboard/skills/contexts', `cold-parent-${index}-library`);
            await operator.click(page.getByRole('button', { name: /^(New agent context|Write AGENTS.md)$/ }).first(), `cold-parent-${index}-new`);
            await fill('Immutable name', parent.name, `cold-parent-${index}-name`);
            await fill('Purpose', 'Synthetic shared-policy context', `cold-parent-${index}-purpose`);
            await fill('AGENTS.md', '# Synthetic policy\nConsult approved linked skills.\n', `cold-parent-${index}-root`);
            const created = watch(res => res.url().endsWith('/skills/contexts/') && res.request().method() === 'POST');
            await click('Create draft', `cold-parent-${index}-create`);
            parent.id = (await (await created).json()).result.context_id;
            for (const [n, leaf] of input.leaves.entries()) {
              await click('Published leaf skill', `cold-parent-${index}-choose-${n}`, 'combobox');
              await click(leaf.name, `cold-parent-${index}-skill-${n}`, 'option');
              if (index === 2 && n <= 1) {
                await operator.click(page.getByRole('combobox', { name: /^Update policy/ }), `cold-parent-${index}-policy-${n}`);
                await click(n === 0 ? 'Pin version' : 'Follow label', `cold-parent-${index}-selector-${n}`, 'option');
                if (n === 0) await fill('Version', '7', 'cold-deliberate-v7-pin');
              }
              await click('Add to proposed links', `cold-parent-${index}-add-${n}`);
            }
            await click('Preview dependency changes', `cold-parent-${index}-preview`);
            await operator.review(`cold-parent-${index}-dependency-review`, input.leaves.map((_, n) => `linked-skill-${n}`), index === 2 ? ['linked-skill-0'] : [], () => expect(page.getByText(/Compiled candidate/)).toBeVisible());
            await click('Accept these exact resolved versions and selector policies.', `cold-parent-${index}-accept`, 'checkbox');
            await click('Apply selected updates to draft', `cold-parent-${index}-apply`);
            await expect.poll(async () => compiled(await read(`contexts/${parent.id}/draft/`, signal))).toBe(7);
            await publishParent(parent, index, 1);
          }
          await writeFile(inputPath, JSON.stringify(input), { mode: 0o600 });
          await receipt(stage, ledger, input.parents); return;
        }
        if (stage.id === 'publish-leaf') {
          const before = await read(`${child.id}/`, signal, input.owner);
          requireValue(before.latest_version === 7, 'Input must start at v7');
          // Runtime setup and pre-update checkpoint establishment are measured,
          // even in a configured run: none is seeded away by the fixture.
          for (const [index, parent] of input.parents.entries()) {
            observations.initial.push(await read(`contexts/${parent.id}/versions/1/inspection/`, signal));
            await command(`runtime-${index}-setup`, runtimeArgs('setup', index)); ownedRuntimes.push(index);
            await command(`runtime-${index}-initial-checkpoint`, runtimeArgs('initial', index));
          }
          await navigate(`/dashboard/skills/${child.id}/edit`, 'shared-policy-editor');
          const source = page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true });
          await expect(page.getByRole('textbox', { name: /Editor content/ }).or(source).first()).toBeVisible();
          if (await source.isVisible()) await operator.click(source, 'shared-policy-source');
          await edit(`---\nname: ${child.name}\ndescription: Synthetic shared purchase policy.\n---\nApproved synthetic policy revision 8.\n`, 'repair-shared-policy');
          await click('Publish', 'open-leaf-publication');
          const dialog = page.getByRole('dialog');
          await operator.review('leaf-publication-review', ['shared-policy-file'], [], () => expect(dialog.getByText('Publish skill', { exact: true })).toBeVisible());
          const production = dialog.getByRole('checkbox', { name: 'production', exact: true });
          if (!await production.isChecked()) await operator.click(production, 'leaf-production-selection');
          const warning = dialog.getByRole('checkbox', { name: 'I reviewed the warnings and want to publish this version.', exact: true });
          const publish = dialog.getByRole('button', { name: 'Publish', exact: true });
          await expect.poll(async () => await warning.isVisible() || await publish.isEnabled(), { timeout: 60_000 }).toBe(true);
          if (await warning.isVisible()) await operator.click(warning, 'acknowledge-leaf-static-warnings');
          await operator.click(publish, 'publish-leaf-v8');
          await expect.poll(async () => (await read(`${child.id}/`, signal, input.owner)).latest_version, { timeout: 60_000 }).toBe(8);
          await receipt(stage, ledger, await read(`${child.id}/`, signal, input.owner)); return;
        }
        if (stage.id === 'inspect-dependents') {
          await navigate(`/dashboard/skills/${child.id}?tab=dependents`, 'authorized-dependents');
          await expect(page.getByRole('table', { name: 'Published context dependents' })).toBeVisible();
          const active = await read(`${child.id}/context-dependents/`, signal);
          requireValue(active.count === 3 && active.results.length === 3, 'Incomplete dependent population');
          for (const [index, parent] of input.parents.entries()) {
            const row = active.results.find((item) => item.context_id === parent.id);
            requireValue(row && row.locked_version === 7 && row.update_state === (index === 2 ? 'pinned_as_intended' : 'update_available'), 'Wrong dependent lock or selector');
            await operator.review('inspect-parent-reference', [`parent-${index}`], index === 2 ? [`parent-${index}`] : [], () => expect(page.getByRole('link', { name: `Inspect ${parent.name} v1` })).toBeVisible());
          }
          await snapshot('dependent-inventory', page);
          await operator.click(page.getByRole('combobox', { name: /Reference scope/ }), 'choose-reference-scope');
          await click('All published history', 'inspect-history', 'option');
          observations.dependents = { active, history: await read(`${child.id}/context-dependents/?view=history`, signal) };
          await receipt(stage, ledger, observations.dependents); return;
        }
        if (stage.id === 'accept-followers') {
          for (const [index, parent] of followers.entries()) {
            await navigate(`/dashboard/skills/contexts/${parent.id}?version=1`, `parent-${index}-viewed-release`);
            await click('Edit viewed v1', `parent-${index}-edit`);
            await click('Start selected draft', `parent-${index}-start`);
            await click(`Review update for ${child.name}`, `parent-${index}-choose-child`, 'checkbox');
            await click('Preview dependency changes', `parent-${index}-preview`);
            await operator.review(`parent-${index}-dependency-review`, ['shared-policy-v8'], [], () => expect(page.getByText(/Compiled candidate/)).toBeVisible());
            if (index === 0) {
              await command('source-label-aba', operationsArgs('source-aba', '8'));
              ledger.fault('source-aba', 'source-production-eight-seven-eight');
              await click('Accept these exact resolved versions and selector policies.', 'stale-plan-accept', 'checkbox');
              const rejected = watch(res => res.url().endsWith('/dependencies/apply/') && res.request().method() === 'POST');
              await click('Apply selected updates to draft', 'stale-plan-apply');
              const response = await rejected;
              observations.aba = { status: response.status(), body: await response.json(), draft: await read(`contexts/${parent.id}/draft/`, signal) };
              requireValue(response.status() === 409 && compiled(observations.aba.draft) === 7, 'ABA plan was not rejected without mutation');
              await operator.recoveryDecision('refresh-rejected-dependency-plan', () => page.reload());
              await click(`Review update for ${child.name}`, 'renew-child-selection', 'checkbox');
              await click('Preview dependency changes', 'renew-dependency-preview');
              await operator.review('renew-dependency-review', ['shared-policy-v8'], [], () => expect(page.getByText(/Compiled candidate/)).toBeVisible());
            }
            await click('Accept these exact resolved versions and selector policies.', `parent-${index}-accept-v8`, 'checkbox');
            await click('Apply selected updates to draft', `parent-${index}-apply`);
            await expect.poll(async () => compiled(await read(`contexts/${parent.id}/draft/`, signal)), { timeout: 60_000 }).toBe(8);
            observations.accepted.push(await read(`contexts/${parent.id}/draft/`, signal));
          }
          await receipt(stage, ledger, observations.accepted); return;
        }
        if (stage.id === 'edit-while-upstream-moves') {
          for (const [index, parent] of followers.entries()) {
            await navigate(`/dashboard/skills/contexts/${parent.id}/edit`, `parent-${index}-root-editor`);
            let raced = false, raceError;
            const pattern = `**/contexts/${parent.id}/draft/`;
            if (index === 0) await page.route(pattern, async route => {
              if (route.request().method() !== 'PUT' || raced) return route.continue();
              raced = true;
              try {
                const pending = route.request().postDataJSON();
                const ownerPage = await open('owner');
                const ownerOp = new MeasuredOperator({ ledger, actor: 'owner', step: stage.id, phase: stage.phase, signal });
                await ownerOp.transition('concurrent-upstream-editor', () => ownerOp.configurationEdit('upstream-v9-editor', () => ownerPage.goto(`/dashboard/skills/${child.id}/edit`)));
                const source = ownerPage.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true });
                await expect(ownerPage.getByRole('textbox', { name: /Editor content/ }).or(source).first()).toBeVisible();
                if (await source.isVisible()) await ownerOp.click(source, 'upstream-source');
                await ownerOp.configurationEdit('concurrent-upstream-v9', async () => {
                  const editor = ownerPage.getByRole('textbox', { name: /Editor content/ });
                  await editor.focus(); await editor.press('Control+a');
                  await ownerPage.evaluate(text => navigator.clipboard.writeText(text), `---\nname: ${child.name}\ndescription: Synthetic shared purchase policy.\n---\nApproved synthetic policy revision 9.\n`);
                  await editor.press('Control+v');
                });
                await ownerOp.click(ownerPage.getByRole('button', { name: 'Publish', exact: true }), 'upstream-publish-review');
                const dialog = ownerPage.getByRole('dialog');
                await ownerOp.review('upstream-publication-review', ['shared-policy-v9'], [], () => expect(dialog).toBeVisible());
                const production = dialog.getByRole('checkbox', { name: 'production', exact: true });
                if (!await production.isChecked()) await ownerOp.click(production, 'upstream-production');
                const warning = dialog.getByRole('checkbox', { name: 'I reviewed the warnings and want to publish this version.', exact: true });
                await expect.poll(async () => await warning.isVisible() || await dialog.getByRole('button', { name: 'Publish', exact: true }).isEnabled()).toBe(true);
                if (await warning.isVisible()) await ownerOp.click(warning, 'upstream-warnings');
                await ownerOp.click(dialog.getByRole('button', { name: 'Publish', exact: true }), 'upstream-publish-v9');
                await expect.poll(async () => (await read(`${child.id}/`, signal)).latest_version).toBe(9);
                const still = await read(`contexts/${parent.id}/draft/`, signal);
                requireValue(still.revision === pending.revision && compiled(still) === 8, 'Pending save or accepted lock changed during race');
                observations.inflight = { pendingRevision: pending.revision, storedRevision: still.revision, child: await read(`${child.id}/`, signal) };
                ledger.fault('upstream-advances-during-review', 'held-real-autosave');
              } catch (error) { raceError = error; } finally { await route.continue(); }
            });
            const root = '# Reviewed synthetic policy\nUse explicitly accepted policy, never upstream latest.\n';
            await edit(root, `parent-${index}-root-edit`);
            await expect.poll(async () => (await read(`contexts/${parent.id}/draft/`, signal)).files.find(file => file.path === 'AGENTS.md').content, { timeout: 120_000 }).toBe(root);
            if (index === 0) { await page.unroute(pattern); if (raceError) throw raceError; requireValue(raced, 'Save race did not execute'); }
            const purpose = 'Reviewed purchasing purpose; retain the accepted shared policy.';
            await fill('Context purpose', purpose, `parent-${index}-purpose-edit`);
            await expect.poll(async () => (await read(`contexts/${parent.id}/draft/`, signal)).context_spec.description).toBe(purpose);
            const saved = await read(`contexts/${parent.id}/draft/`, signal);
            requireValue(compiled(saved) === 8 && saved.revision > observations.accepted[index].revision, 'Edited draft replaced accepted bytes');
            observations.saved.push(saved);
          }
          await receipt(stage, ledger, { saved: observations.saved, inflight: observations.inflight }); return;
        }
        if (stage.id === 'test-complete-contexts') {
          providerId = (await command('configure-synthetic-provider', operationsArgs('provider', namespace))).id;
          for (const [index, parent] of followers.entries()) {
            const prompt = `${runId}-parent-${index}: explain a purchasing request`;
            await navigate(`/dashboard/skills/contexts/${parent.id}?tab=tests&draft=1`, `parent-${index}-whole-context-tests`);
            await fill('Test request', prompt, `parent-${index}-test-request`);
            await fill('Expected answer contains', 'approved-response', `parent-${index}-expected-answer`);
            await operator.review(`parent-${index}-case-review`, ['request', 'expected-answer'], [], () => page.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.', exact: true }).check());
            await click('Task model', `parent-${index}-model`, 'combobox'); await click('gpt-4o-mini', `parent-${index}-model-choice`, 'option');
            await fill('Maximum estimated cost (USD)', '2', `parent-${index}-budget`);
            await fill('Historical reference version (optional)', '1', `parent-${index}-reference`);
            const previewing = watch(res => res.url().endsWith('/test-runs/preview/') && res.request().method() === 'POST');
            await click('Preview whole-context test', `parent-${index}-test-preview`);
            const preview = (await (await previewing).json()).result;
            const handle = `s-${createHash('sha256').update(JSON.stringify([preview.fingerprint, child.id])).digest('hex').slice(0, 16)}`;
            await fake(`/scenarios/${namespace}`, 'DELETE');
            await fake('/scenarios', 'POST', { namespace, rules: [
              { match: { request: prompt, turn: 0 }, response: { message: { role: 'assistant', content: null, tool_calls: [{ id: 'synthetic-turn-0', type: 'function', function: { name: 'load_skill', arguments: { skill_id: handle } } }] }, finish_reason: 'tool_calls' } },
              { match: { request: prompt, turn: 1 }, response: { message: { role: 'assistant', content: 'approved-response' }, finish_reason: 'stop' } },
            ], github: [] });
            // Fixture names contain 'benchmark': keep detector findings and count
            // every per-file classification rather than renaming to avoid work.
            const classifications = page.getByRole('combobox', { name: /^Classify / });
            for (let file = 0; file < await classifications.count(); file++) {
              await operator.review(`parent-${index}-flagged-file-${file}`, [`outbound-file-${file}`], [`outbound-file-${file}`], () => expect(classifications.nth(file)).toBeVisible());
              await operator.click(classifications.nth(file), `parent-${index}-classify-${file}`);
              await click('Reference material', `parent-${index}-reference-material-${file}`, 'option');
              await operator.fill(page.getByRole('textbox', { name: /^Classification reason for / }).nth(file), 'Synthetic purchase-policy reference. The benchmark name is not an answer key; no expected-answer text is bundled.', `parent-${index}-classification-reason-${file}`);
            }
            await click('I know of no bundled answer keys or evaluation material', `parent-${index}-no-answer-keys`, 'checkbox');
            await operator.review(`parent-${index}-outbound-inventory`, ['standing-root', ...input.leaves.map((_, n) => `linked-skill-${n}`), 'case', 'reference'], [], () => page.getByRole('checkbox', { name: 'I reviewed the entire inventory and outbound inputs', exact: true }).check());
            await click('I acknowledge provider processing of this reviewed context and test request.', `parent-${index}-provider-consent`, 'checkbox');
            await snapshot(`parent-${index}-test-preview`, page);
            const started = watch(res => res.url().endsWith('/test-runs/') && res.request().method() === 'POST');
            await click('Run whole-context test', `parent-${index}-run`);
            const response = await started;
            const admission = await response.json();
            if (response.status() !== 202) await record('admission-rejected', { status: response.status(), body: admission });
            requireValue(response.status() === 202, 'Context job was not admitted');
            const job = { parent: parent.id, id: admission.result.id }; observations.jobs.push(job);
            await expect.poll(async () => (await read(`contexts/${parent.id}/test-runs/${job.id}/`, signal)).status, { timeout: 180_000 }).toBe('completed');
            job.summary = await read(`contexts/${parent.id}/test-runs/${job.id}/`, signal);
            job.boundary = (await fake(`/scenarios/${namespace}`)).ledger;
            requireValue(job.summary.candidate.digest === observations.saved[index].digest, 'Test substituted the saved compiled context');
            const delivered = job.boundary.filter(row => row.turn === 1).flatMap(row => row.messages.filter(message => message.role === 'tool').map(message => JSON.parse(message.content).text));
            requireValue(delivered.some(text => text.includes('policy revision 8.')) && delivered.some(text => text.includes('policy revision 7.')) && !delivered.some(text => text.includes('policy revision 9.')), 'Whole-context model boundary substituted a child');
            await expect(page.getByText('Answer: pass', { exact: true })).toHaveCount(2);
            await snapshot(`parent-${index}-test-result`, page);
          }
          await receipt(stage, ledger, observations.jobs, null, 'model_boundary'); return;
        }
        if (stage.id === 'review-publish-parents') {
          const candidates = await Promise.all(followers.map(parent => read(`contexts/${parent.id}/draft/`, signal)));
          const decision = { packet_sha256: digest(candidates), preconditions_sha256: digest(await Promise.all(followers.map(parent => read(`contexts/${parent.id}/`, signal)))) };
          for (const [index, parent] of followers.entries()) {
            await navigate(`/dashboard/skills/contexts/${parent.id}/edit`, `parent-${index}-publication-editor`);
            await publishParent(parent, index, 2, decision);
            const detail = await read(`contexts/${parent.id}/`, signal);
            requireValue(detail.context.labels.length === 0, 'Publication silently moved a parent label');
            const inspection = await read(`contexts/${parent.id}/versions/2/inspection/`, signal);
            requireValue(inspection.manifest.digest === observations.saved[index].digest, 'Publication differs from tested bytes');
          }
          await receipt(stage, ledger, await Promise.all(followers.map(parent => read(`contexts/${parent.id}/versions/2/inspection/`, signal))), decision); return;
        }
        if (stage.id === 'authorize-label-moves') {
          // Submit and review separately for each parent; no batch authority or
          // repeated early approval can satisfy this stage's decision receipt.
          const packets = [];
          for (const [index, parent] of followers.entries()) {
            await navigate(`/dashboard/skills/contexts/${parent.id}?version=2&tab=versions`, `parent-${index}-label-request`);
            await operator.reconcile(`parent-${index}-reviewer-identity`, () => Promise.resolve(input.owner.userId));
            await fill('Independent reviewer user ID', input.owner.userId, `parent-${index}-reviewer`);
            await click('Preview parent label move', `parent-${index}-label-preview`);
            await click('Request independent review of this exact parent transition. Do not move its label yet.', `parent-${index}-request-confirmation`, 'checkbox');
            const submitted = watch(res => res.url().endsWith('/release-requests/') && res.request().method() === 'POST');
            await operator.handoff('owner', () => click('Send parent release request', `parent-${index}-send-request`));
            const response = await submitted; requireValue(response.status() === 201, 'Independent release request missing');
            packets.push((await response.json()).result);
          }
          const decision = { packet_sha256: digest(packets), preconditions_sha256: digest(await Promise.all(followers.map(parent => read(`contexts/${parent.id}/`, signal)))) };
          const reviewerPage = await open('owner');
          const reviewer = new MeasuredOperator({ ledger, actor: 'owner', step: stage.id, phase: stage.phase, signal });
          for (const [index, parent] of followers.entries()) {
            await reviewer.transition(`parent-${index}-reviewer-arrival`, () => reviewer.configurationEdit(`parent-${index}-request-link`, () => reviewerPage.goto(`/dashboard/skills/contexts/${parent.id}/review?request=${packets[index].id}`)));
            const reviewed = reviewerPage.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true });
            await expect(reviewed).toBeEnabled();
            await reviewer.review(`parent-${index}-release-review`, ['parent-candidate', 'target', 'evidence-absence', 'release-consequence'], [], () => reviewed.check(), decision);
            await reviewer.approve(`parent-${index}-approve-packet`, [`parent-${index}`], () => reviewerPage.getByRole('button', { name: 'Approve exact packet', exact: true }).click(), decision);
            await expect(reviewed).not.toBeChecked();
            await command(`parent-${index}-unrelated-heartbeat`, operationsArgs('heartbeat', index));
            ledger.fault('unrelated-heartbeat', `parent-${index}-observation-only`);
            await reviewer.review(`parent-${index}-execution-review`, ['exact-approved-packet', 'current-preconditions'], [], () => reviewed.check(), decision);
            await reviewer.approve(`parent-${index}-authorize-execution`, [`parent-${index}`], () => reviewerPage.getByRole('button', { name: 'Release approved packet', exact: true }).click(), decision);
            await reviewer.approve(`parent-${index}-move-production`, [`parent-${index}`], () => reviewerPage.getByRole('button', { name: 'Move production', exact: true }).click(), decision);
            await expect(reviewerPage.getByText('Durable release receipt', { exact: true })).toBeVisible();
            observations.releases.push((await pool.query('SELECT to_version, actor_id::text FROM model_hub_hubskilllabelevent WHERE skill_id = $1', [parent.id])).rows);
            requireValue(canonical(observations.releases[index]) === canonical([{ to_version: 2, actor_id: input.owner.userId }]), 'Wrong parent authorization actor or duplicate release');
            await expect(reviewerPage.getByRole('dialog')).toHaveCount(0);
            await snapshot(`parent-${index}-authorized`, reviewerPage);
          }
          await receipt(stage, ledger, observations.releases, decision, 'pg'); return;
        }
        if (stage.id === 'inspect-sessions') {
          for (const [index, parent] of input.parents.entries()) {
            if (index < 2) await command(`runtime-${index}-follow-approved-production`, runtimeArgs('follow', index));
            // The application resumes its existing thread, then starts a clean one;
            // each run reports only what its native model input actually contained.
            await command(`runtime-${index}-resume`, runtimeArgs('resume', index));
            await command(`runtime-${index}-fresh`, runtimeArgs('fresh', index));
            const boundaries = {};
            for (const mode of ['initial', 'fresh', 'resume']) boundaries[mode] = JSON.parse(await readFile(resolve(input.outputDir, 'runtime', `${index}-${mode}.json`), 'utf8'));
            requireValue(boundaries.resume.had_checkpoint && !boundaries.fresh.had_checkpoint, 'Fresh/resumed checkpoints differ from requested modes');
            observations.runtime.push(boundaries);
            // The operator inspects delivery through the product, not driver JSON.
            await navigate(`/dashboard/skills/contexts/${parent.id}?tab=consumers`, `parent-${index}-consumers`);
            const row = page.getByRole('table', { name: 'Context delivery observations' }).getByRole('row').filter({ hasText: `${parent.name}-runtime` });
            const expected = index < 2 ? ['Old thread retained', 'v2'] : ['Pinned as intended', 'v1'];
            await operator.review(`runtime-${index}-delivery-inspection`, index < 2 ? ['fresh-session-delivery', 'resumed-thread-retention'] : ['fresh-session-delivery', 'resumed-thread-retention', 'deliberate-pin'], index < 2 ? [] : ['deliberate-pin'], async () => {
              for (const text of expected) await expect(row.getByText(text, { exact: true }).first()).toBeVisible();
            });
            await snapshot(`parent-${index}-sessions`, page);
          }
          await receipt(stage, ledger, observations.runtime, null, 'model_boundary'); return;
        }
        throw new Error(`Unknown stage: ${stage.id}`);
      } catch (error) {
        // Private diagnostics stay beside credentials, outside fetched artifacts.
        if (error.cmd) await writeFile(resolve(inputPath, '..', `sdk-diagnostic-${runId}.txt`), error.stderr || '', { mode: 0o600 });
        await record('blocked-stage', { stage: stage.id, reason: error.cmd ? 'Public SDK application command failed; inspect cloud-local diagnostics' : error.message, completeJobQualified: false });
        await snapshot('blocked', page).catch(() => {});
        throw error;
      }
    },
    async observe(check, { signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      let source = 'api', value, evidence;
      if (check.id === 'dependents-and-pins') {
        evidence = await read(`${child.id}/context-dependents/`, signal);
        value = evidence.results.map(row => `${input.parents.find(parent => parent.id === row.context_id)?.name}:${row.update_state === 'pinned_as_intended' ? 'pin-v7' : 'follower'}`);
      } else if (check.id === 'accepted-v8-not-v9' || check.id === 'descriptor-projection') {
        evidence = await Promise.all(input.parents.map((parent, index) => read(`contexts/${parent.id}/versions/${index === 2 ? 1 : 2}/inspection/`, signal)));
        if (check.id === 'accepted-v8-not-v9') value = evidence.map(row => row.descriptor.links.find(link => link.skill_id === child.id)?.version);
        else {
          source = 'pg';
          const projections = await Promise.all(evidence.map(row => pool.query('SELECT child_skill_id::text AS skill_id, child_version_id::text AS version_id FROM model_hub_hubskillcontextdependency WHERE parent_version_id = $1 ORDER BY child_skill_id', [row.manifest.version_id])));
          value = evidence.every((row, index) => row.descriptor.links.length === 15 && canonical(projections[index].rows) === canonical(row.descriptor.links.map(link => ({ skill_id: link.skill_id, version_id: link.version_id })).sort((a, b) => a.skill_id.localeCompare(b.skill_id))));
          evidence = { inspections: evidence, projections: projections.map(result => result.rows) };
        }
      } else if (check.id === 'monotonic-draft-revisions') {
        source = 'pg';
        evidence = { accepted: observations.accepted, saved: observations.saved, durable: (await pool.query('SELECT skill_id::text, revision FROM model_hub_hubskilldraft WHERE skill_id = ANY($1::uuid[])', [followers.map(parent => parent.id)])).rows };
        value = evidence.durable.length === 2 && followers.every((parent, index) => observations.saved[index].revision > observations.accepted[index].revision && evidence.durable.find(row => row.skill_id === parent.id).revision >= observations.saved[index].revision);
      } else if (check.id === 'fresh-resumed-identities') {
        source = 'model_boundary'; evidence = observations.runtime;
        const delivered = (runtime, mode) => {
          const observed = runtime[mode];
          const latestBoundary = observed.boundary.at(-1);
          const text = (content) => typeof content === 'string' ? content : (content ?? []).map(block => typeof block === 'string' ? block : block?.text ?? '').join('');
          const system = latestBoundary.filter(message => message.type === 'system').map(message => text(message.content)).join('\n');
          const reviewedRoot = system.includes('# Reviewed synthetic policy\nUse explicitly accepted policy, never upstream latest.\n');
          const initialRoot = system.includes('# Synthetic policy\nConsult approved linked skills.\n');
          requireValue(reviewedRoot !== initialRoot, 'Exactly one complete standing root must reach the native model boundary');
          const lastHuman = latestBoundary.findLastIndex(message => message.type === 'human');
          const toolText = latestBoundary.slice(lastHuman + 1).filter(message => message.type === 'tool').map(message => JSON.stringify(message.content)).join('\n');
          const match = toolText.match(/policy revision (\d+)\./);
          requireValue(match, 'Linked policy bytes absent at native model boundary');
          // Root and child must come from one immutable parent: reviewed root with v8,
          // initial root with v7. A mixed pair is a hidden substitution.
          requireValue(reviewedRoot === (Number(match[1]) === 8), 'Standing root and linked child come from different parent releases');
          return Number(match[1]);
        };
        value = { fresh: evidence.map(row => delivered(row, 'fresh')), resumed: evidence.map(row => delivered(row, 'resume')) };
      } else if (check.id === 'source-aba-rejected') {
        evidence = observations.aba;
        value = evidence.status === 409 && compiled(evidence.draft) === 7;
      } else throw new Error('No independent oracle for check');
      if (process.env.J3_ORACLE_FAULT === check.id) value = typeof value === 'boolean' ? !value : ['one-sided-wrong-observation'];
      return { source, value, artifact_sha256: await record(`oracle-${check.id}`, evidence) };
    },
    async teardown() {
      let qualified = true;
      for (const job of observations.jobs) {
        const state = await read(`contexts/${job.parent}/test-runs/${job.id}/`);
        if (!['completed', 'failed', 'cancelled'].includes(state.status)) {
          await fetch(`${input.apiUrl}/model-hub/skills/contexts/${job.parent}/test-runs/${job.id}/cancel/`, { method: 'POST', headers: { ...headers(input.author), 'Content-Type': 'application/json' }, body: '{}' });
          qualified = false;
        }
      }
      const cleaned = await Promise.allSettled(ownedRuntimes.map(index => promisify(execFile)(process.env.J3_PYTHON, runtimeArgs('cleanup', index), { timeout: 15_000, maxBuffer: 1024 * 1024 })));
      if (cleaned.some(result => result.status !== 'fulfilled')) qualified = false;
      if (providerId) {
        const response = await fetch(`${input.apiUrl}/model-hub/api-keys/${providerId}/`, { method: 'DELETE', headers: headers(input.owner) });
        qualified &&= response.ok;
      }
      await fake(`/scenarios/${namespace}`, 'DELETE').catch(() => { qualified = false; });
      await Promise.all([...contexts.values()].map(({ context }) => context.close()));
      await browser?.close(); await pool.end();
      return { qualified: qualified && (!browser || !browser.isConnected()) };
    },
  };
}
