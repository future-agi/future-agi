import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue } from '../../../lib/skills-r3/canonical.mjs';

/** Live public-UI driver. Input is private, cloud-local, and contains only an
 * initial v7 library/three v1 parents plus actor credentials and real provenance.
 * Never consume previous success reports as observations. No API writes stand
 * in for a user's authoring/review work. Missing stages fail the unchanged
 * complete-job runner, preserving real partial counts without a ratio claim.
 */
export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J3', 'Use the assigned Linux cloud VM');
  requireValue(process.env.J3_DRIVER_INPUT, 'Supply a private cloud-local input');
  const input = JSON.parse(await readFile(process.env.J3_DRIVER_INPUT, 'utf8'));
  for (const name of ['appUrl', 'apiUrl']) requireValue(new URL(input[name]).hostname === 'localhost', 'Owned full stack only');
  requireValue(input.parents?.length === 3 && input.leaves?.length === 15, 'Complete three-context, fifteen-skill initial condition required');
  const output = resolve(input.outputDir, runId);
  await mkdir(output, { recursive: true });
  const observations = {};
  const contexts = new Map();
  let browser;
  const headers = (actor) => ({ Authorization: `Bearer ${actor.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId });
  const read = async (path, signal, actor = input.author) => {
    const response = await fetch(`${input.apiUrl}/model-hub/skills/${path}`, { headers: headers(actor), signal });
    requireValue(response.ok, 'Independent authorized API observation unavailable');
    return (await response.json()).result;
  };
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
  const stageReceipt = async (stage, ledger, value, decision = null) => ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: await record(stage.id, value), actionSequences: [], ...(decision ? { decision } : {}) });
  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      const page = await open(stage.id === 'publish-leaf' ? 'owner' : 'author');
      const actorName = stage.id === 'publish-leaf' ? 'owner' : 'author';
      const operator = new MeasuredOperator({ ledger, actor: actorName, step: stage.id, phase: stage.phase, signal });
      const click = (name, target, role = 'button') => operator.click(page.getByRole(role, { name, exact: true }), target);
      const navigate = (path, target) => operator.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: actorName, step: stage.id, phase: stage.phase, target }, () => page.goto(path)));
      const edit = (content, target) => operator.configurationEdit(target, async () => {
        const editor = page.getByRole('textbox', { name: /Editor content/ });
        await editor.focus(); await editor.press('Control+a'); await page.evaluate(text => navigator.clipboard.writeText(text), content); await editor.press('Control+v');
      });
      const cancel = () => { for (const resource of contexts.values()) void resource.context.close().catch(() => {}); };
      signal.addEventListener('abort', cancel, { once: true });
      try {
        if (stage.id === 'configure-three-contexts') {
          // A provisioned parent is not measured cold setup. Do not silently
          // skip the configured 3x15 initial authoring/selector decisions.
          await navigate('/dashboard/skills/contexts', 'cold-context-library');
          throw new Error('Cold three-context authoring is not implemented by this driver; provisioned parents cannot satisfy cold setup');
        }
        requireValue(registration.cohort === 'configured', 'Only configured stage execution is implemented');
        if (stage.id === 'publish-leaf') {
          const before = await read(`${child.id}/`, signal, input.owner);
          requireValue(before.latest_version === 7, 'Input must begin at v7, not a previously executed job');
          await navigate(`/dashboard/skills/${child.id}/edit`, 'shared-policy-editor');
          const source = page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true });
          await expect(page.getByRole('textbox', { name: /Editor content/ }).or(source).first()).toBeVisible();
          if (await source.isVisible()) await operator.click(source, 'shared-policy-source');
          await edit(`---\nname: ${child.name}\ndescription: Synthetic shared purchase policy.\n---\nApproved synthetic policy revision 8.\n`, 'repair-shared-policy');
          await click('Publish', 'open-leaf-publication');
          const dialog = page.getByRole('dialog');
          await operator.review('leaf-publication-review', ['shared-policy-file'], [], async () => { await expect(dialog.getByText('Publish skill', { exact: true })).toBeVisible(); });
          const production = dialog.getByRole('checkbox', { name: 'production', exact: true });
          if (!await production.isChecked()) await operator.click(production, 'leaf-production-selection');
          const warning = dialog.getByRole('checkbox', { name: 'I reviewed the warnings and want to publish this version.', exact: true });
          const publish = dialog.getByRole('button', { name: 'Publish', exact: true });
          await expect.poll(async () => await warning.isVisible() || await publish.isEnabled(), { timeout: 60_000 }).toBe(true);
          if (await warning.isVisible()) await operator.click(warning, 'acknowledge-leaf-static-warnings');
          await operator.click(publish, 'publish-leaf-v8');
          await expect.poll(async () => (await read(`${child.id}/`, signal, input.owner)).latest_version, { timeout: 60_000 }).toBe(8);
          const observed = await read(`${child.id}/`, signal, input.owner);
          requireValue(observed.labels.some((label) => label.name === 'production' && label.version === 8), 'The repair was not made available to followers');
          await stageReceipt(stage, ledger, observed); return;
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
          await operator.click(page.getByRole('combobox', { name: /Reference scope/ }), 'choose-reference-scope');
          await click('All published history', 'inspect-history', 'option');
          const history = await read(`${child.id}/context-dependents/?view=history`, signal);
          observations.dependents = { active, history };
          await stageReceipt(stage, ledger, observations.dependents); return;
        }
        if (stage.id === 'accept-followers') {
          observations.accepted = [];
          for (const [index, parent] of followers.entries()) {
            await navigate(`/dashboard/skills/contexts/${parent.id}?version=1`, `parent-${index}-viewed-release`);
            await click('Edit viewed v1', `parent-${index}-edit`);
            await click('Start selected draft', `parent-${index}-start`);
            await click(`Review update for ${child.name}`, `parent-${index}-choose-child`, 'checkbox');
            await click('Preview dependency changes', `parent-${index}-preview`);
            await operator.review(`parent-${index}-dependency-review`, ['shared-policy-v8'], [], () => expect(page.getByText(/Compiled candidate/)).toBeVisible());
            await click('Accept these exact resolved versions and selector policies.', `parent-${index}-accept-v8`, 'checkbox');
            await click('Apply selected updates to draft', `parent-${index}-apply`);
            await expect.poll(async () => compiled(await read(`contexts/${parent.id}/draft/`, signal)), { timeout: 60_000 }).toBe(8);
            observations.accepted.push(await read(`contexts/${parent.id}/draft/`, signal));
          }
          await stageReceipt(stage, ledger, observations.accepted); return;
        }
        // These are separate complete-job stages, not permission to treat the
        // retained prerequisite flow as a finished benchmark. Until concurrent
        // v9, whole-context UI test admission, independent per-parent release,
        // and actual fresh/resumed model boundaries are instrumented here,
        // every run remains failed with its real partial ledger.
        throw new Error(`Live stage instrumentation is still required: ${stage.id}`);
      } catch (error) {
        await record('blocked-stage', { stage: stage.id, reason: error.message, completeJobQualified: false });
        await page.screenshot({ path: resolve(output, 'blocked.png') }).catch(() => {});
        throw error;
      } finally { signal.removeEventListener('abort', cancel); }
    },
    async observe(check, { signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      // No literal expected value or old report may become observation evidence.
      throw new Error(`Whole-job independent oracle not established: ${check.id}`);
    },
    async teardown() {
      await Promise.all([...contexts.values()].map(({ context }) => context.close()));
      await browser?.close();
      // No paid job is created by implemented stages. Published versions are
      // durable user records, not disposable VM resources.
      return { qualified: !browser || !browser.isConnected() };
    },
  };
}
