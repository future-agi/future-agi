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
  let browser, providerId, credentialsExported = false;
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
  const snapshot = (name, page) => page.screenshot({ path: resolve(output, 'shots', `${name}.png`), animations: 'disabled' });
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
      // One-command public SDK equivalents for work that is the same across a set of contexts. Each command
      // keeps every context's own review identity; the operator's decisions stay explicit flags and reviews.
      const authorEnv = () => ({ ...process.env, FI_API_KEY: input.authorSdk.apiKey, FI_SECRET_KEY: input.authorSdk.secretKey,
        FI_BASE_URL: input.apiUrl, FI_ORGANIZATION_ID: input.authorSdk.organizationId, FI_WORKSPACE_ID: input.authorSdk.workspaceId });
      const cli = async (target, module, args) => {
        if (!credentialsExported) {
          credentialsExported = true;
          // The person exports their own API credentials once before any SDK command; it is work, so it is counted.
          await operator.configurationEdit('export-author-api-credentials', async () => {});
        }
        const result = await operator.command(target, process.env.J3_PYTHON, ['-m', module, ...args], { cwd: process.cwd(), env: authorEnv(), timeoutMs: 280_000 });
        return JSON.parse(result.stdout);
      };
      const planPath = (name) => resolve(inputPath, '..', `${runId}-${name}.json`);
      const parentItems = (index) => [`parent-${index}-standing-root`, ...input.leaves.map((_, n) => `parent-${index}-linked-skill-${n}`),
        `parent-${index}-compiled-descriptor`, `parent-${index}-accepted-lock`, `parent-${index}-publication-head`];
      // One reviewed decision over the printed comparison of every listed draft; each is acknowledged and published separately.
      const publishSet = async (tag, list, expected, decision = null) => {
        const file = planPath(`${tag}-publish-plan`);
        const plan = await cli(`${tag}-plan-publish`, 'fi.context.release', ['plan-publish', ...list.map((parent) => parent.id), '--out', file]);
        const drafts = await Promise.all(list.map((parent) => read(`contexts/${parent.id}/draft/`, signal)));
        const warned = plan.contexts.map((row, position) => (row.warnings > 0 ? list[position] : null)).filter(Boolean);
        const items = list.flatMap((parent) => [...parentItems(input.parents.indexOf(parent)), ...(warned.includes(parent) ? [`parent-${input.parents.indexOf(parent)}-static-warnings`] : [])]);
        const exceptions = [...(tag === 'cold' ? ['parent-2-linked-skill-0'] : []), ...warned.map((parent) => `parent-${input.parents.indexOf(parent)}-static-warnings`)];
        await operator.review(`${tag}-publish-set-review`, items, exceptions, async () => {
          requireValue(plan.contexts.length === list.length && plan.contexts.every((row, position) => row.context_id === list[position].id && row.errors === 0
            && row.candidate_digest === drafts[position].digest && row.revision === drafts[position].revision
            && row.accepted_lock.length === input.leaves.length && row.changed_from_origin.length > 0), 'The printed plan does not describe the saved drafts');
        }, decision);
        await cli(`${tag}-publish-set`, 'fi.context.release', ['publish-set', '--plan', file, '--reviewed-plan', plan.plan_digest, '--reviewed-changes',
          ...(warned.length ? ['--acknowledge-warnings'] : []), '--note', 'Reviewed set publication']);
        for (const parent of list) await expect.poll(async () => (await read(`contexts/${parent.id}/`, signal)).context.latest_version, { timeout: 60_000 }).toBe(expected);
      };
      try {
        if (stage.id === 'configure-three-contexts') {
          requireValue(input.parents.every(parent => parent.id === null), 'Cold fixture must not pre-author the parents');
          // One shared plan over every parent: the shared standing instructions are written once, every published skill follows
          // its default label, and the deliberate exact pin is the one per-context exception shown to the reviewer.
          await operator.configurationEdit('write-shared-standing-instructions', async () => {});
          const file = planPath('create-plan');
          const pin = `${input.parents[2].name}:${child.name}=7`;
          const plan = await cli('plan-create-contexts', 'fi.context.authoring', ['plan-create', ...input.parents.map(parent => parent.name), '--description', 'Synthetic shared-policy context',
            '--root', '# Synthetic policy\nConsult approved linked skills.\n', '--pin', pin, '--out', file]);
          const items = input.parents.flatMap((_, index) => input.leaves.map((_, n) => `parent-${index}-linked-skill-${n}`));
          await operator.review('create-set-dependency-review', items, ['parent-2-linked-skill-0'], async () => {
            requireValue(plan.contexts.length === 3 && plan.contexts.every((row, index) => row.name === input.parents[index].name && row.errors === 0 && row.resolved.length === input.leaves.length
              && row.exceptions.length === (index === 2 ? 1 : 0)), 'The printed plan does not describe the three contexts');
          });
          await cli('apply-create-contexts', 'fi.context.authoring', ['apply-create', '--plan', file, '--reviewed-plan', plan.plan_digest, '--accept-resolved-versions']);
          for (const [index, parent] of input.parents.entries()) {
            parent.id = plan.contexts[index].context_id;
            await expect.poll(async () => compiled(await read(`contexts/${parent.id}/draft/`, signal))).toBe(7);
          }
          await publishSet('cold', input.parents, 1);
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
          const publish = dialog.getByRole('button', { name: 'Publish and move production', exact: true });
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
          }
          const historyDisclosure = page.getByRole('button', { name: 'Show superseded releases', exact: true });
          const hasSuperseded = await historyDisclosure.isVisible();
          if (hasSuperseded) await operator.click(historyDisclosure, 'show-superseded-context-releases');
          // Identical active/history rows are not duplicated; superseded releases remain reviewable.
          await operator.review('inspect-dependent-inventory', [...input.parents.map((_, index) => `parent-${index}`), 'historical-references'], ['parent-2'], async () => {
            for (const [index, parent] of input.parents.entries()) await expect(page.getByRole('link', { name: `Inspect ${parent.name} v1` }).first()).toBeVisible();
            for (const parent of followers) await expect(page.getByRole('checkbox', { name: `Select ${parent.name} v1 for update` })).toBeChecked();
            await expect(page.getByRole('checkbox', { name: `Select ${input.parents[2].name} v1 for update` })).toBeDisabled();
            if (hasSuperseded) await expect(page.getByRole('table', { name: 'Historical context references' })).toBeVisible();
          });
          await snapshot('dependent-inventory', page);
          observations.dependents = { active, history: await read(`${child.id}/context-dependents/?view=history`, signal) };
          await receipt(stage, ledger, observations.dependents); return;
        }
        if (stage.id === 'accept-followers') {
          // One reviewed update over the preselected followers, started from the dependents inventory the person is already viewing.
          await click(`Prepare draft updates for ${followers.length} contexts`, 'followers-preview');
          await expect(page.getByRole('button', { name: `Apply selected updates to ${followers.length} context drafts`, exact: true })).toBeVisible();
          const reviewPlans = (target, exceptions = []) => operator.review(target, followers.map((_, index) => `parent-${index}-shared-policy-v8`), exceptions, () => expect(page.getByRole('button', { name: 'Technical details', exact: true })).toHaveCount(followers.length));
          await reviewPlans('followers-dependency-review');
          await command('source-label-aba', operationsArgs('source-aba', '8'));
          ledger.fault('source-aba', 'source-production-eight-seven-eight');
          await click(`Accept these skill versions and update rules into ${followers.length} context drafts.`, 'stale-plan-accept', 'checkbox');
          const rejected = [];
          const onResponse = (res) => { if (res.url().endsWith('/dependencies/apply/') && res.request().method() === 'POST') rejected.push(res); };
          page.on('response', onResponse);
          await click(`Apply selected updates to ${followers.length} context drafts`, 'stale-plan-apply');
          await expect.poll(() => rejected.length, { timeout: 60_000 }).toBe(followers.length);
          page.off('response', onResponse);
          const statuses = rejected.map(res => res.status());
          observations.aba = { status: statuses[0], statuses, body: await rejected[0].json(), draft: await read(`contexts/${followers[0].id}/draft/`, signal) };
          requireValue(statuses.every(status => status === 409) && compiled(observations.aba.draft) === 7, 'ABA plan was not rejected without mutation');
          await expect(page.getByRole('button', { name: `Preview again for ${followers.length} contexts that did not update`, exact: true })).toBeVisible();
          await operator.recoveryDecision('preview-rejected-plans-again', () => page.getByRole('button', { name: `Preview again for ${followers.length} contexts that did not update`, exact: true }).click());
          await expect(page.getByRole('button', { name: `Apply selected updates to ${followers.length} context drafts`, exact: true })).toBeVisible();
          await reviewPlans('renew-dependency-review');
          await click(`Accept these skill versions and update rules into ${followers.length} context drafts.`, 'followers-accept-v8', 'checkbox');
          await click(`Apply selected updates to ${followers.length} context drafts`, 'followers-apply');
          for (const parent of followers) {
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
                await expect.poll(async () => await warning.isVisible() || await dialog.getByRole('button', { name: 'Publish and move production', exact: true }).isEnabled()).toBe(true);
                if (await warning.isVisible()) await ownerOp.click(warning, 'upstream-warnings');
                await ownerOp.click(dialog.getByRole('button', { name: 'Publish and move production', exact: true }), 'upstream-publish-v9');
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
          const prompt = `${runId}: explain a purchasing request`;
          // Synthetic provider transport only: the model asks for the shared policy skill by name, so one rule serves every context.
          await fake(`/scenarios/${namespace}`, 'DELETE');
          await fake('/scenarios', 'POST', { namespace, rules: [
            { match: { request: prompt, turn: 0 }, response: { message: { role: 'assistant', content: null, tool_calls: [{ id: 'synthetic-turn-0', type: 'function', function: { name: 'load_skill', arguments: { skill_id: { $skill_named: child.name } } } }] }, finish_reason: 'tool_calls' } },
            { match: { request: prompt, turn: 1 }, response: { message: { role: 'assistant', content: 'approved-response' }, finish_reason: 'stop' } },
          ], github: [] });
          // The request and expected answer are typed once; the set is previewed in one command against each saved draft.
          const file = planPath('test-plan');
          const plan = await cli('plan-whole-context-tests', 'fi.context.testing', ['plan-set', ...followers.map(parent => parent.id), '--request', prompt,
            '--expect', 'approved-response', '--reviewed-case', '--reference', '1', '--limit', '2', '--out', file]);
          requireValue(plan.contexts.length === followers.length && plan.contexts.every(row => row.flagged.length > 0), 'Fixture produced no flagged files to classify');
          // Fixture names contain 'benchmark': every flagged file of every context is shown as an exception and classified with one reviewed reason.
          const flagged = plan.contexts.flatMap((row, index) => row.flagged.map((_, file) => `parent-${index}-outbound-file-${file}`));
          const reason = 'Synthetic purchase-policy reference. The benchmark name is not an answer key; no expected-answer text is bundled.';
          await operator.review('whole-context-plan-review', ['request', 'expected-answer', ...followers.flatMap((_, index) => [`parent-${index}-candidate`, `parent-${index}-outbound-inventory`]), ...flagged], flagged, async () => {
            requireValue(plan.contexts.every((row, index) => row.context_id === followers[index].id && row.files > 0), 'The printed plan does not describe the followers');
          });
          const outcome = await cli('run-whole-context-tests', 'fi.context.testing', ['run-set', '--plan', file, '--reviewed-plan', plan.plan_digest, '--reviewed-inventory',
            '--no-known-answer-keys', '--classify-flagged', 'reference_material', '--reason', reason, '--acknowledge-provider-processing', '--wait-seconds', '240']);
          const ledgerRows = (await fake(`/scenarios/${namespace}`)).ledger;
          requireValue(outcome.runs.length === followers.length, 'Context jobs were not admitted');
          for (const [index, parent] of followers.entries()) {
            const run = outcome.runs[index];
            requireValue(run.context_id === parent.id && run.status === 'completed', 'Context job did not complete');
            const job = { parent: parent.id, id: run.run_id };
            observations.jobs.push(job);
            job.summary = await read(`contexts/${parent.id}/test-runs/${job.id}/`, signal);
            // Each run's model-boundary rows are the ones whose skill inventory carries this context's own opaque handles.
            const handle = `s-${createHash('sha256').update(JSON.stringify([plan.contexts[index].fingerprint, child.id])).digest('hex').slice(0, 16)}`;
            job.boundary = ledgerRows.filter(row => row.messages.some(message => message.role === 'system' && typeof message.content === 'string' && message.content.includes(`"id":"${handle}"`)));
            requireValue(job.summary.candidate.digest === observations.saved[index].digest, 'Test substituted the saved compiled context');
            const delivered = job.boundary.filter(row => row.turn === 1).flatMap(row => row.messages.filter(message => message.role === 'tool').map(message => JSON.parse(message.content).text));
            requireValue(delivered.some(text => text.includes('policy revision 8.')) && delivered.some(text => text.includes('policy revision 7.')) && !delivered.some(text => text.includes('policy revision 9.')), 'Whole-context model boundary substituted a child');
            requireValue(run.verdicts.length === 2 && run.verdicts.every(item => item.answer === 'pass'), 'Whole-context answers did not pass');
          }
          await operator.review('whole-context-result-review', followers.map((_, index) => `parent-${index}-answer-verdicts`), [], async () => {});
          await receipt(stage, ledger, observations.jobs, null, 'model_boundary'); return;
        }
        if (stage.id === 'review-publish-parents') {
          const candidates = await Promise.all(followers.map(parent => read(`contexts/${parent.id}/draft/`, signal)));
          const decision = { packet_sha256: digest(candidates), preconditions_sha256: digest(await Promise.all(followers.map(parent => read(`contexts/${parent.id}/`, signal)))) };
          await publishSet('recurring', followers, 2, decision);
          for (const [index, parent] of followers.entries()) {
            const detail = await read(`contexts/${parent.id}/`, signal);
            requireValue(detail.context.labels.length === 0, 'Publication silently moved a parent label');
            const inspection = await read(`contexts/${parent.id}/versions/2/inspection/`, signal);
            requireValue(inspection.manifest.digest === observations.saved[index].digest, 'Publication differs from tested bytes');
          }
          await receipt(stage, ledger, await Promise.all(followers.map(parent => read(`contexts/${parent.id}/versions/2/inspection/`, signal))), decision); return;
        }
        if (stage.id === 'authorize-label-moves') {
          // One reviewed request over the exact transitions; each parent still gets its own signed packet,
          // its own independent reviewer decision and its own release. No batch authority exists.
          await operator.reconcile('reviewer-identity', () => Promise.resolve(input.owner.userId));
          const file = planPath('release-plan');
          const plan = await cli('plan-release-set', 'fi.context.release', ['plan-release', ...followers.map(parent => parent.id), '--version', '2', '--out', file]);
          await operator.review('release-request-review', followers.flatMap((_, index) => [`parent-${index}-transition`, `parent-${index}-candidate`]), [], async () => {
            requireValue(plan.contexts.length === followers.length && plan.contexts.every((row, position) => row.context_id === followers[position].id
              && row.to_version === 2 && row.from_version === null && row.label === 'production'), 'The printed transitions do not match the published parents');
          });
          const sent = await operator.handoff('owner', () => cli('request-release-set', 'fi.context.release', ['request-set', '--plan', file, '--reviewed-plan', plan.plan_digest,
            '--reviewer', input.owner.userId, '--confirm-request-only']));
          requireValue(sent.requests.length === followers.length, 'Independent release request missing');
          const packets = sent.requests.map(row => ({ id: row.request_id, status: row.status }));
          const decision = { packet_sha256: digest(packets), preconditions_sha256: digest(await Promise.all(followers.map(parent => read(`contexts/${parent.id}/`, signal)))) };
          const reviewerPage = await open('owner');
          const reviewer = new MeasuredOperator({ ledger, actor: 'owner', step: stage.id, phase: stage.phase, signal });
          for (const [index, parent] of followers.entries()) {
            await reviewer.transition(`parent-${index}-reviewer-arrival`, () => reviewer.configurationEdit(`parent-${index}-request-link`, () => reviewerPage.goto(`/dashboard/skills/contexts/${parent.id}/review?request=${packets[index].id}`)));
            const reviewed = reviewerPage.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true });
            await expect(reviewed).toBeEnabled();
            await reviewer.review(`parent-${index}-release-review`, ['parent-candidate', 'target', 'evidence-absence', 'release-consequence'], [], () => reviewed.check(), decision);
            await reviewer.approve(`parent-${index}-approve-packet`, [`parent-${index}`], () => reviewerPage.getByRole('button', { name: 'Approve release', exact: true }).click(), decision);
            await command(`parent-${index}-unrelated-heartbeat`, operationsArgs('heartbeat', index));
            ledger.fault('unrelated-heartbeat', `parent-${index}-observation-only`);
            // The acknowledgement is keyed to the exact packet revision and fingerprint and carries across
            // pending -> approved; only a cleared acknowledgement (a new revision) is a new review action.
            if (!(await reviewed.isChecked())) await reviewer.review(`parent-${index}-execution-review`, ['exact-approved-packet', 'current-preconditions'], [], () => reviewed.check(), decision);
            await expect(reviewed).toBeChecked();
            await reviewer.approve(`parent-${index}-authorize-execution`, [`parent-${index}`], () => reviewerPage.getByRole('button', { name: 'Release to production', exact: true }).click(), decision);
            await reviewer.approve(`parent-${index}-move-production`, [`parent-${index}`], () => reviewerPage.getByRole('button', { name: 'Move production', exact: true }).click(), decision);
            await expect(reviewerPage.getByText('Release receipt', { exact: true })).toBeVisible();
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
