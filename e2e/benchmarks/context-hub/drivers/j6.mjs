import { chromium, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { canonical, digest, requireValue, sha256 } from '../../../lib/skills-r3/canonical.mjs';

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

// Public forensic APIs are observations, not a substitute for missing operator
// controls. Never use a manifest URL returned by the server as a fetch target.
export async function readCompleteClosure(read, contextId, number, signal) {
  requireValue(UUID.test(contextId) && Number.isSafeInteger(number) && number > 0, 'Exact recovery identity required');
  const prefix = `contexts/${contextId}/versions/${number}/inspection/`;
  let first;
  const rows = [];
  let offset = 0;
  for (let page = 0; page < 400; page++) {
    const value = await read(`${prefix}?file_offset=${offset}&file_limit=25`, signal);
    requireValue(value?.distribution_blocked === false, 'Recovery release is unavailable or quarantined');
    const manifest = value.manifest;
    requireValue(manifest?.resource_id === contextId && manifest.version === number && manifest.file_offset === offset, 'Inspection identity or offset differs');
    if (!first) first = value;
    requireValue(manifest.version_id === first.manifest.version_id && manifest.digest === first.manifest.digest
      && manifest.file_count === first.manifest.file_count && canonical(value.descriptor) === canonical(first.descriptor), 'Closure changed during inspection');
    requireValue(Array.isArray(manifest.files) && manifest.files.length > 0, 'Incomplete closure page');
    rows.push(...manifest.files);
    requireValue(rows.length <= 10000 && new Set(rows.map(row => row.path)).size === rows.length, 'Invalid complete closure');
    if (manifest.next_file_offset === null) break;
    requireValue(manifest.next_file_offset === rows.length && manifest.next_file_offset > offset, 'Manifest pagination did not advance');
    offset = manifest.next_file_offset;
  }
  requireValue(rows.length === first.manifest.file_count, 'Complete closure was not read');
  const files = [];
  for (const file of rows) {
    let content = '';
    let charOffset = 0;
    let totalChars;
    let finished = false;
    for (let chunk = 0; chunk < 1024; chunk++) {
      const value = await read(`${prefix}files/?path=${encodeURIComponent(file.path)}&offset=${charOffset}&max_bytes=32768`, signal);
      requireValue(value.version_id === first.manifest.version_id && value.digest === first.manifest.digest
        && value.path === file.path && value.sha256 === file.sha256 && value.offset === charOffset && typeof value.text === 'string', 'File chunk identity differs');
      totalChars ??= value.total_chars;
      requireValue(totalChars === value.total_chars, 'File size changed');
      content += value.text;
      charOffset += [...value.text].length; // Server offsets count Unicode code points, not UTF-16 units.
      if (value.eof === true) { finished = true; break; }
      requireValue(value.next_offset === charOffset && value.text.length > 0, 'Chunk pagination did not advance');
    }
    requireValue(finished && charOffset === totalChars && sha256(Buffer.from(content, 'utf8')) === file.sha256, 'Incomplete or changed recovery file');
    files.push({ path: file.path, content });
  }
  return { inspection: first, files };
}

/**
 * Configured recovery qualification attempt on the owned cloud full stack.
 * Input is a private J6_DRIVER_INPUT file containing appUrl, apiUrl, contextId,
 * targetVersion, bindingIds, author tokens/scope, provenance and outputDir.
 * This driver remains incomplete until complete release/configuration choice,
 * locked restore and hosted Simulation recovery are integrated here. The separate
 * SKILLS-E2E-062 proves public reviewed pointer rollback, not whole-job recovery.
 * This driver must never turn the
 * SKILLS-E2E-061 host subprocess integration into a guest/native observation.
 */
export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J6', 'J6 runs on the assigned cloud Linux VM');
  requireValue(process.env.J6_DRIVER_INPUT, 'Private J6 driver input is required');
  const input = JSON.parse(await readFile(process.env.J6_DRIVER_INPUT, 'utf8'));
  for (const key of ['appUrl', 'apiUrl']) {
    const url = new URL(input[key]);
    requireValue(['localhost', '127.0.0.1'].includes(url.hostname) && ['http:', 'https:'].includes(url.protocol)
      && !url.username && !url.password && !url.search && !url.hash && url.pathname === '/', 'Use the owned full-stack origin');
  }
  requireValue(UUID.test(input.contextId) && Array.isArray(input.bindingIds) && input.bindingIds.length > 0
    && input.bindingIds.every(id => UUID.test(id)), 'Declare exact recovery context and cohort');
  const output = resolve(input.outputDir, runId);
  await mkdir(output, { recursive: true });
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), JSON.stringify(value, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    return digest(value);
  };
  const headers = { Authorization: `Bearer ${input.author.access}`, 'X-Organization-Id': input.author.organizationId, 'X-Workspace-Id': input.author.workspaceId };
  const read = async (path, signal) => {
    const response = await fetch(`${input.apiUrl.replace(/\/$/, '')}/model-hub/skills/${path}`, { headers, signal, redirect: 'error' });
    requireValue(response.ok, 'Independent public recovery observation unavailable');
    return (await response.json()).result;
  };
  let browser;
  let context;
  let page;
  let closure;
  let cohort;
  const open = async () => {
    if (page) return;
    browser = await chromium.launch({ headless: true });
    context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 } });
    await context.addInitScript(actor => {
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
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      if (signal.aborted) throw new Error('Journey cancelled');
      requireValue(registration.cohort === 'configured', 'Cold application qualification is not implemented');
      await open();
      const cancel = () => { void context.close().catch(() => {}); };
      signal.addEventListener('abort', cancel, { once: true });
      const operator = new MeasuredOperator({ ledger, actor: 'recovery-operator', step: stage.id, phase: stage.phase, signal });
      try {
        if (stage.id === 'detect-incident') {
          await operator.transition('recovery-cohort', () => ledger.measure({ dimension: 'A', primitive: 'click', actor: 'recovery-operator', step: stage.id, phase: stage.phase, target: 'open-declared-context' },
            () => page.goto(`/dashboard/skills/contexts/${input.contextId}?version=${input.targetVersion}`)));
          await operator.transition('consumer-observations', () => operator.click(page.getByRole('tab', { name: 'Consumers', exact: true }), 'inspect-declared-consumers'));
          const results = [];
          for (let pageNumber = 1; pageNumber <= 100; pageNumber++) {
            const value = await read(`contexts/${input.contextId}/consumers/?page=${pageNumber}&page_size=100`, signal);
            results.push(...value.results);
            if (pageNumber >= value.total_pages) break;
            requireValue(pageNumber < 100, 'Incomplete consumer inventory');
          }
          cohort = results.filter(row => input.bindingIds.includes(row.binding.id));
          requireValue(cohort.length === new Set(input.bindingIds).size, 'Declared cohort is incomplete');
          requireValue(cohort.some(row => row.states.includes('unknown')), 'Retained unknown incident was not observed');
          await expect(page.getByRole('table', { name: 'Context delivery observations' })).toBeVisible();
          const hash = await record('incident-cohort', { evidence_class: 'synthetic fixture', cohort });
          ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash, actionSequences: [] });
          await page.screenshot({ path: resolve(output, 'incident-cohort.png') });
          return;
        }
        if (stage.id === 'choose-eligible-release') {
          closure = await readCompleteClosure(read, input.contextId, input.targetVersion, signal);
          await operator.transition('immutable-release', () => operator.click(page.getByRole('tab', { name: 'Context', exact: true }), 'inspect-exact-release'));
          const decision = { packet_sha256: digest(closure), preconditions_sha256: digest({ cohort, version: closure.inspection.manifest.version_id }) };
          await operator.review('complete-recovery-closure', closure.files.map((_, i) => `file-${i}`), [], async () => {
            for (const file of closure.files) {
              // Every file navigation is a primitive, not hidden in one review.
              const tree = page.getByRole('treeitem');
              const index = await tree.evaluateAll((nodes, target) => {
                const parts = [];
                return nodes.findIndex(node => {
                  const level = Number(node.getAttribute('aria-level'));
                  parts.length = level - 1;
                  parts.push(node.getAttribute('aria-label'));
                  return parts.join('/') === target;
                });
              }, file.path);
              requireValue(index >= 0, 'A retained file is not reachable in the public tree');
              await operator.click(tree.nth(index), 'inspect-retained-file');
              const source = page.getByLabel(`${file.path} read-only source`);
              await expect(source).toHaveText(file.content);
              // The flat tree can scroll its sibling source out of the viewport.
              // Visibility in the DOM alone must not be reported as exposure.
              await source.scrollIntoViewIfNeeded();
              await expect(source).toBeInViewport();
            }
          }, decision);
          await record('eligible-closure', closure);
          await page.screenshot({ path: resolve(output, 'eligible-release.png') });
          // Inspecting one eligible candidate is not a complete recovery choice:
          // retired-child/quarantined alternatives and application configuration
          // must be reviewed in the same public recovery lifecycle. Do not mark
          // this stage complete just because its file review finished.
          await record('blocked-stage', { stage: stage.id, complete_job_qualified: false,
            reason: 'Candidate closure inspected. Public reviewed pointer rollback is separately covered by SKILLS-E2E-062, but this driver has not integrated alternative/configuration choice, locked local restore or hosted application recovery. No release or recovery action was attempted in this measured run.' });
          throw new Error('Complete recovery choice remains unintegrated in the registered driver');
        }
        // Missing public operations are not replaced with test-only POSTs,
        // pregraded receipts, a host subprocess labelled guest, or automatic approval.
        await record('blocked-stage', { stage: stage.id, complete_job_qualified: false,
          reason: 'Locked application restore and hosted Simulation recovery are not integrated in this driver. Native topology additionally requires real committed refs and authenticated launch/ingestion. Passing pointer rollback and host CLI prerequisites are not native or complete-job evidence.' });
        await page.screenshot({ path: resolve(output, 'blocked-recovery.png') });
        throw new Error(`Unqualified public recovery stage: ${stage.id}`);
      } finally { signal.removeEventListener('abort', cancel); }
    },
    async observe(check, { signal }) {
      if (signal.aborted) throw new Error('Journey cancelled');
      if (check.id === 'eligible-complete-closure') {
        const actual = await readCompleteClosure(read, input.contextId, input.targetVersion, signal);
        return { source: 'api', value: actual.files, artifact_sha256: await record('closure-oracle', actual) };
      }
      throw new Error(`Independent complete-job observation unavailable: ${check.id}`);
    },
    async teardown() {
      await context?.close();
      await browser?.close();
      return { qualified: !browser || !browser.isConnected() };
    },
  };
}
