import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue } from '../../../lib/skills-r3/canonical.mjs';

export async function readSkillResult({ apiUrl, headers, path, signal }) {
  const response = await fetch(`${apiUrl}/model-hub/skills/${path}`, { headers, signal });
  requireValue(response.ok, 'Independent public observation unavailable');
  return (await response.json()).result;
}

/**
 * Live configured-J4 qualification attempt, never a replay of test artifacts.
 * J4_DRIVER_INPUT is a private local JSON file on the approved Linux VM:
 * { provenance, appUrl, apiUrl, skillId, baselineVersion, author: {
 *   access, refresh, organizationId, workspaceId }, outputDir }.
 * The input contains an existing authored draft, not a published candidate or
 * submitted review. Authentication is a configured prerequisite, not cold setup.
 *
 * Deliberately fails at unavailable product surfaces. In particular, submitting
 * a packet with test-only HTTP calls must NOT turn the missing author UI into a
 * measured success. Downstream stages remain unqualified until public controls
 * and their independent observations are implemented. The unchanged runner
 * retains partial A/S/H/R and marks the whole job failed, never 10x-qualified.
 */
export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J4', 'J4 runs only on the approved cloud Linux VM');
  requireValue(process.env.J4_DRIVER_INPUT, 'Provide a private cloud-local driver input');
  const input = JSON.parse(await readFile(process.env.J4_DRIVER_INPUT, 'utf8'));
  for (const key of ['appUrl', 'apiUrl']) requireValue(new URL(input[key]).hostname === 'localhost', 'Use the owned, forwarded full stack only');
  requireValue(/^[a-f0-9-]{36}$/.test(input.skillId), 'An existing draft identity is required');
  const output = resolve(input.outputDir, runId);
  await mkdir(output, { recursive: true });
  let browser;
  let context;
  let page;
  let published;
  const headers = { Authorization: `Bearer ${input.author.access}`, 'X-Organization-Id': input.author.organizationId, 'X-Workspace-Id': input.author.workspaceId };
  const read = (path, signal) => readSkillResult({ apiUrl: input.apiUrl, headers, path, signal });
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    return digest(value);
  };
  const open = async () => {
    if (page) return;
    browser = await chromium.launch({ headless: true });
    context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 }, recordVideo: { dir: resolve(output, 'video'), size: { width: 1440, height: 900 } } });
    await context.addInitScript((actor) => {
      localStorage.setItem('accessToken', actor.access);
      localStorage.setItem('refreshToken', actor.refresh);
      localStorage.setItem('rememberMe', 'true');
      sessionStorage.setItem('organizationId', actor.organizationId);
      sessionStorage.setItem('workspaceId', actor.workspaceId);
      sessionStorage.setItem('workspaceOrgId', actor.organizationId);
    }, input.author);
    page = await context.newPage();
    page.setDefaultTimeout(30_000);
  };
  return {
    mode: 'product',
    provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      if (signal.aborted) throw new Error('Journey cancelled');
      if (stage.id === 'configure-review-authority') {
        // Do not recast API fixture provisioning as measured operator setup.
        await record('blocked-stage', { stage: stage.id, reason: 'Cold authority configuration is not implemented by this driver. Configured authentication cannot satisfy cold setup.' });
        throw new Error('Cold review-authority setup is not qualified');
      }
      requireValue(registration.cohort === 'configured', 'Configured prerequisite required');
      await open();
      const cancel = () => { void context?.close().catch(() => {}); };
      signal.addEventListener('abort', cancel, { once: true });
      const operator = new MeasuredOperator({ ledger, actor: 'author', step: stage.id, phase: stage.phase, signal });
      const navigate = (target, url) => operator.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: 'author', step: stage.id, phase: stage.phase, target }, () => page.goto(url)));
      try {
        if (stage.id === 'publish-candidate') {
          await navigate('candidate-editor', `/dashboard/skills/${input.skillId}/edit`);
          await operator.transition('publication-review', () => operator.click(page.getByRole('button', { name: 'Publish', exact: true }), 'open-publication-review'));
          const dialog = page.getByRole('dialog');
          await expect(dialog.getByText('Publish skill', { exact: true })).toBeVisible();
          const draft = await read(`${input.skillId}/draft/`, signal);
          await operator.review('candidate-diff', draft.files.map((_, i) => `file-${i}`), [], async () => {
            for (const file of draft.files) {
              const item = dialog.getByRole('button', { name: new RegExp(file.path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) });
              // Every file selection is counted, not hidden inside a grouped review.
              if (await item.count()) await operator.click(item.first(), 'inspect-candidate-file');
            }
            await expect(dialog.getByRole('button', { name: 'Publish', exact: true })).toBeEnabled();
          });
          const response = page.waitForResponse(res => res.url().endsWith(`/${input.skillId}/draft/publish/`) && res.request().method() === 'POST');
          await operator.click(dialog.getByRole('button', { name: 'Publish', exact: true }), 'publish-immutable-candidate');
          const result = await response;
          requireValue(result.status() === 201, 'Candidate publication failed');
          published = (await result.json()).result.version;
          const [version, labels] = await Promise.all([read(`${input.skillId}/versions/${published.number}/`, signal), read(`${input.skillId}/labels/`, signal)]);
          requireValue(version.id === published.id && version.digest === published.digest, 'Published identity differs');
          requireValue(labels.find((label) => label.name === 'production')?.version === input.baselineVersion, 'Publication moved production');
          const hash = await record('publication-observation', { version, labels });
          // The registration requires PG for the production-unchanged check:
          // this API observation is stage evidence only, not a PG oracle.
          ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash, actionSequences: [] });
          await page.screenshot({ path: resolve(output, 'published-candidate.png') });
          return;
        }
        if (stage.id === 'share-and-submit') {
          requireValue(published, 'Publish the candidate in this measured run');
          await navigate('author-reviews', '/dashboard/skills/reviews');
          await expect(page.getByRole('tab', { name: 'Reviews', exact: true })).toBeVisible();
          await page.screenshot({ path: resolve(output, 'author-arrival.png') });
          // This fails on the current product: the author composer is not wired.
          // Never substitute direct HTTP submission or count a reviewer-only flow.
          await operator.click(page.getByRole('button', { name: 'Send review request', exact: true }), 'submit-exact-review-packet');
          throw new Error('A submit click alone is insufficient: shared evidence, recipient and packet observations are required');
        }
        throw new Error(`Independent complete-stage evidence is not implemented: ${stage.id}`);
      } catch (error) {
        await record('blocked-stage', { stage: stage.id, reason: stage.id === 'share-and-submit' ? 'No reachable author share-and-submit composer; no packet was submitted through a public user interface.' : 'Stage did not finish; see independent observations and browser recording.', completeJobQualified: false });
        await page.screenshot({ path: resolve(output, 'blocked.png') }).catch(() => {});
        throw error;
      } finally {
        signal.removeEventListener('abort', cancel);
      }
    },
    async observe(check, { signal }) {
      if (signal.aborted) throw new Error('Journey cancelled');
      throw new Error(`Independent whole-job oracle is not observed: ${check.id}`);
    },
    async teardown() {
      await context?.close();
      await browser?.close();
      // The driver owns browser resources only; durable user releases are retained
      // and must never be deleted merely to make teardown look successful.
      return { qualified: !browser || !browser.isConnected() };
    },
  };
}
