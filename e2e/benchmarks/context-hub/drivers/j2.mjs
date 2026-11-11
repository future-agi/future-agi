import { chromium, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue } from '../../../lib/skills-r3/canonical.mjs';

/** Live synthetic-fixture attempt. No artifact replay, private database writes or
 * test-only product operations. Authentication/source provisioning are declared
 * configured prerequisites; cold application integration is NOT credited as done.
 * The current product has no hosted native launch/ingestion UI. Stop there, retain
 * the actual partial ledger, and never count a captured text case as reproduction.
 * J2_DRIVER_INPUT is a private VM-only JSON file with provenance, owned localhost
 * stack URLs, actor tokens, contextId/version, projectName/projectId/traceId/spanId.
 */
export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J2', 'J2 requires the approved cloud Linux host');
  requireValue(process.env.J2_DRIVER_INPUT, 'A private cloud-local J2 input is required');
  const input = JSON.parse(await readFile(process.env.J2_DRIVER_INPUT, 'utf8'));
  requireValue(registration.evidence_scope === 'synthetic', 'This driver currently qualifies synthetic fixture mechanics only');
  for (const key of ['appUrl', 'apiUrl']) {
    const url = new URL(input[key]);
    requireValue(url.hostname === 'localhost' && !url.username && !url.password, 'Only the owned localhost stack is supported');
  }
  requireValue(/^[a-f0-9-]{36}$/.test(input.contextId), 'An exact context fixture is required');
  const output = resolve(process.env.J2_DRIVER_OUTPUT || 'proof/j2-driver', runId);
  await mkdir(output, { recursive: true });
  const headers = { Authorization: `Bearer ${input.actor.access}`, 'X-Organization-Id': input.actor.organizationId, 'X-Workspace-Id': input.actor.workspaceId };
  const root = `/model-hub/skills/contexts/${input.contextId}/`;
  const read = async (path, signal) => {
    const response = await fetch(`${input.apiUrl}${path}`, { headers, signal });
    requireValue(response.ok, 'Independent scoped API observation failed');
    return (await response.json()).result;
  };
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    return digest(value);
  };
  let browser;
  let context;
  let page;
  let source;
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
    }, input.actor);
    page = await context.newPage();
    page.setDefaultTimeout(60_000);
  };
  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      await open();
      const cancel = () => { void context.close().catch(() => {}); };
      signal.addEventListener('abort', cancel, { once: true });
      const operator = new MeasuredOperator({ ledger, actor: 'incident-responder', step: stage.id, phase: stage.phase, signal });
      const navigate = (target, url) => operator.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: 'incident-responder', step: stage.id, phase: stage.phase, target }, () => page.goto(url)));
      try {
        if (stage.id === 'qualify-application' || stage.id === 'reproduce-historical') {
          await navigate('inspect-context-tests', `/dashboard/skills/contexts/${input.contextId}?version=${input.version}`);
          await operator.transition('context-tests', () => operator.click(page.getByRole('tab', { name: 'Tests', exact: true }), 'open-context-tests'));
          const [version, suite] = await Promise.all([read(`${root}versions/${input.version}/inspection/`, signal), read(`${root}tests/`, signal)]);
          const nativeNotice = page.getByText(/Application verification unavailable/);
          await expect(nativeNotice).toBeVisible();
          await record('native-blocker', { evidenceClass: 'synthetic fixture', stage: stage.id, version, suiteRevision: suite.revision,
            reason: 'The shared application adapter is not connected to an authenticated hosted native launch and result ingestion lifecycle. A text capture is not a native baseline.',
            historicalReproduced: false, nativeRunId: null, releaseReady: false });
          await page.screenshot({ path: resolve(output, 'native-unavailable.png'), animations: 'disabled' });
          throw new Error('Native application execution unavailable; complete historical reproduction is not qualified');
        }
        if (stage.id === 'external-failure') {
          // The source is a declared synthetic incident delivered through the real
          // OTLP/Observe path, not real retained-Langfuse conformance.
          await navigate('synthetic-retained-incident', '/dashboard/observe');
          await operator.transition('incident-project', () => operator.click(page.getByText(input.projectName, { exact: true }), 'open-incident-project'));
          await operator.transition('physical-source-span', () => operator.click(page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Synthetic refund failure' }), 'open-failed-trace'));
          const trace = await read(`/tracer/trace/${input.traceId}/?project_id=${input.projectId}`, signal);
          const hash = await record('synthetic-source-observation', { evidenceClass: 'synthetic fixture', trace, retainedSystemConformance: false });
          ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash, actionSequences: [] });
          return;
        }
        if (stage.id === 'capture-and-redact') {
          const before = await read(`${root}tests/`, signal);
          const detail = await read(root, signal);
          const sourceResponse = page.waitForResponse(response => response.url().endsWith(`${root}captures/source/`) && response.request().method() === 'POST');
          await operator.click(page.getByRole('button', { name: 'Create context regression case', exact: true }), 'capture-selected-span');
          await operator.click(page.getByLabel('Regression context', { exact: true }), 'choose-regression-context');
          await operator.click(page.getByRole('option', { name: detail.context.name, exact: true }), 'select-context');
          await operator.click(page.getByRole('button', { name: 'Review selected span', exact: true }), 'load-source-review');
          const response = await sourceResponse;
          requireValue(response.ok(), 'Selected source was not authorized');
          source = (await response.json()).result;
          requireValue(source.trace_ref.project_id === input.projectId && source.trace_ref.trace_id === input.traceId && source.trace_ref.span_id === input.spanId, 'Captured source identity differs');
          const dialog = page.getByRole('dialog');
          const choices = dialog.getByRole('checkbox');
          await expect(choices).toHaveCount(source.segments.length);
          for (let index = 0; index < source.segments.length; index++) await operator.click(choices.nth(index), `select-segment-${index}`);
          await operator.transition('redaction-review', () => operator.click(dialog.getByRole('button', { name: 'Preview selected text', exact: true }), 'review-selected-text'));
          const prompt = source.segments.filter(segment => segment.kind === 'request').map(segment => segment.text).join('\n\n');
          await expect(dialog.getByRole('textbox', { name: 'Redacted request', exact: true })).toHaveValue(prompt);
          await operator.fill(dialog.getByRole('textbox', { name: 'Example title', exact: true }), `Synthetic refund ${runId}`, 'case-title');
          await operator.fill(dialog.getByRole('textbox', { name: 'Expected answer must contain', exact: true }), 'receipt', 'expected-behavior');
          const decision = { packet_sha256: digest({ source, expected: 'receipt' }), preconditions_sha256: digest({ suiteRevision: before.revision, contextId: input.contextId }) };
          await operator.review('review-captured-case', source.segments.map((_, i) => `segment-${i}`), [], async () => {
            await operator.click(dialog.getByRole('checkbox', { name: /confirmed the expected behavior/ }), 'confirm-independent-expectation');
            await operator.click(dialog.getByRole('checkbox', { name: /workspace/ }), 'confirm-workspace-sharing');
          }, decision);
          const savedResponse = page.waitForResponse(result => result.url().includes('/captures/') && result.url().endsWith('/accept/') && result.request().method() === 'POST');
          await operator.click(dialog.getByRole('button', { name: 'Save reviewed example', exact: true }), 'save-reviewed-case');
          requireValue((await savedResponse).ok(), 'Capture acceptance failed');
          const suite = await read(`${root}tests/`, signal);
          requireValue(suite.revision === before.revision + 1, 'Unexpected suite revision');
          const example = suite.cases.find(item => item.title === `Synthetic refund ${runId}`);
          requireValue(example?.prompt === prompt && example.reviewed && example.source_group_id === `trace:${source.source_fingerprint}`, 'Independent captured-case observation differs');
          const hash = await record('capture-observation', { source, suite, decision, evidenceClass: 'synthetic fixture' });
          ledger.stage({ step: stage.id, phase: stage.phase, source: 'api', artifactHash: hash, actionSequences: [], decision });
          return;
        }
        throw new Error(`Complete native-dependent stage is unavailable: ${stage.id}`);
      } finally { signal.removeEventListener('abort', cancel); }
    },
    async observe(check, { signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      // No API toast, fixture self-grade or earlier report substitutes for the
      // independent guest/model-boundary/retained-pipeline checks of this job.
      throw new Error(`Whole-job independent evidence has not been observed: ${check.id}`);
    },
    async teardown() {
      await context?.close();
      await browser?.close();
      return { qualified: !browser || !browser.isConnected() };
    },
  };
}
