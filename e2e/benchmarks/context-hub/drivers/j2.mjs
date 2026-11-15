import { chromium, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import pg from 'pg';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue } from '../../../lib/skills-r3/canonical.mjs';
import { firstTurns, runOperations, sha256 } from '../../../lib/skills-r3/j2-oracle.mjs';

/** J2 on the public UI/API of a live standalone stack with the hosted native container
 * runtime. Evidence class: synthetic fixture (synthetic incident span, deterministic model
 * fixture, synthetic business ledger). Source/authentication provisioning are declared
 * configured prerequisites made by SKILLS-E2E-022's prepare mode; every operator step below
 * is measured. Fault-injection controls (API) are acceptance-matrix checks, not operator work.
 * J2_DRIVER_INPUT is a private VM-only JSON file (see j2-prepare in the repo flow). */
const TITLE = { refund: 'Original refund failure', chargeback: 'Working chargeback case', shipping: 'Working shipping case' };
const ARMS = ['candidate', 'reference', 'production'];

export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J2', 'J2 requires the approved cloud Linux host');
  requireValue(process.env.J2_DRIVER_INPUT, 'A private cloud-local J2 input is required');
  const input = JSON.parse(await readFile(process.env.J2_DRIVER_INPUT, 'utf8'));
  requireValue(registration.evidence_scope === 'synthetic', 'This driver qualifies synthetic fixture mechanics only');
  for (const key of ['appUrl', 'apiUrl']) {
    const url = new URL(input[key]);
    requireValue(url.hostname === 'localhost' && !url.username && !url.password, 'Only the owned localhost stack is supported');
  }
  requireValue(/^[a-f0-9-]{36}$/.test(input.contextId), 'An exact context fixture is required');
  const output = resolve(process.env.J2_DRIVER_OUTPUT || 'proof/j2-driver', runId);
  await mkdir(resolve(output, 'shots'), { recursive: true });
  const headers = { Authorization: `Bearer ${input.actor.access}`, 'X-Organization-Id': input.actor.organizationId, 'X-Workspace-Id': input.actor.workspaceId };
  const skills = `${input.apiUrl}/model-hub/skills/`;
  const root = `contexts/${input.contextId}/`;
  const api = async (method, path, data, { signal } = {}) => {
    const response = await fetch(`${skills}${path}`, { method, signal, headers: { ...headers, 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: data === undefined ? undefined : JSON.stringify(data) });
    const body = await response.json().catch(() => ({}));
    requireValue(response.ok, `Independent API observation failed: ${method} ${path} ${response.status}`);
    return body.result;
  };
  const pool = new pg.Pool({ connectionString: input.pgUrl, max: 2 });
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    return digest(value);
  };
  const waitRun = async (evidenceId, signal) => {
    const deadline = Date.now() + 300_000;
    for (;;) {
      requireValue(!signal?.aborted && Date.now() < deadline, 'Native run did not finish');
      const detail = await api('GET', `${root}native/runs/${evidenceId}/`, undefined, { signal });
      if (['completed', 'failed', 'canceled'].includes(detail.status)) return detail;
      await new Promise((r) => setTimeout(r, 2000));
    }
  };
  const fixtureLedger = async () => (await (await fetch(`${input.fixtureOrigin}/scenarios/${input.providerNamespace}`)).json()).ledger;
  const attemptOf = async (jobIdValue) => (await pool.query('SELECT id FROM simulate_hosted_harness_attempt WHERE job_id=$1', [jobIdValue])).rows[0].id;
  const state = { ledger: null, runs: {}, controls: {}, repaired: null, source: null, capture: null, identityEdits: 0 };
  let browser; let context; let page;
  const open = async () => {
    if (page) return;
    browser = await chromium.launch({ headless: true });
    context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 } });
    await context.addInitScript((actor) => {
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
  const shot = (name) => page.screenshot({ path: resolve(output, 'shots', `${name}.png`), animations: 'disabled' });

  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      state.ledger = ledger;
      await open();
      const cancel = () => { void context.close().catch(() => {}); };
      signal.addEventListener('abort', cancel, { once: true });
      const op = new MeasuredOperator({ ledger, actor: 'incident-responder', step: stage.id, phase: stage.phase, signal });
      const go = (target, url) => op.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: 'incident-responder', step: stage.id, phase: stage.phase, target }, () => page.goto(url)));
      const edit = (target, effect) => ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'incident-responder', step: stage.id, phase: stage.phase, target }, effect);
      const receipt = (source, hash, decision = null) => ledger.stage({ step: stage.id, phase: stage.phase, source, artifactHash: hash, actionSequences: [], ...(decision ? { decision } : {}) });
      const RUN_BUTTON = { name: 'Run application test', exact: true };
      const CEILING = /I accept up to/;
      const group = () => page.getByRole('group', { name: 'Examples to run' });
      /** The plan is preselected from stored data. Each default is asserted visibly (never assumed) before the
       * reviewer decides; the single counted gesture is the button that names the paid ceiling. */
      const expectPlan = async ({ candidate, reference, titles }) => {
        const ready = { timeout: 60_000 };
        await expect(page.getByLabel('Test version', { exact: true })).toContainText(`v${candidate}`, ready);
        await expect(page.getByLabel('Was failing on', { exact: true })).toContainText(`v${reference}`, ready);
        for (const title of titles) await expect(group().getByRole('checkbox', { name: title, exact: true })).toBeChecked(ready);
        await expect(group().getByRole('checkbox', { name: /^Was failing on v/ }).last()).toBeChecked(ready);
        await expect(page.getByRole('checkbox', { name: CEILING })).toBeEnabled({ timeout: 120_000 });
      };
      /** Accept the paid ceiling named on the button and start the run through the public Tests UI. Returns the evidence id. */
      const startRun = async (tag) => {
        const created = page.waitForResponse((res) => res.url().endsWith('/native/runs/') && res.request().method() === 'POST');
        // The reviewer accepts the named paid ceiling themselves; the plan above was made for them.
        await op.click(page.getByRole('checkbox', { name: CEILING }), `${tag}-accept-ceiling`);
        await op.click(page.getByRole('button', RUN_BUTTON), `${tag}-run`);
        const response = await created;
        requireValue(response.ok(), 'Native run was refused');
        return (await response.json()).result.evidence_id;
      };
      try {
        if (stage.id === 'qualify-application') {
          // Cold provisioning the customer performs with the packaged adapter, counted as operator commands:
          // build the application runtime from the packaged wheels, then measure its pinned configuration from
          // the freshly built image with the application source mounted read-only.
          requireValue(process.env.J2_LANE_ROOT, 'J2_LANE_ROOT names the cloud lane root');
          const laneRoot = process.env.J2_LANE_ROOT;
          const commit = execFileSync('docker', ['exec', input.runtimeContainer, 'printenv', 'CTXHUB_APP_COMMIT'], { encoding: 'utf8' }).trim();
          const image = `ctxhub-j2-cold-${runId}`.toLowerCase().replace(/[^a-z0-9_.-]/g, '-');
          state.provisionImage = image;
          await op.transition('provision-application-runtime', async () => {
            await op.command('build-application-runtime', 'docker', ['build', '--quiet', '-t', image, '--build-arg', `CTXHUB_APP_COMMIT=${commit}`, `${laneRoot}/e2e/stack/native-runtime`], { timeoutMs: 280_000 });
            const fresh = await op.command('measure-provisioned-configuration', 'docker', ['run', '--rm', '--read-only', '--network', 'none', '-e', `CTXHUB_APP_COMMIT=${commit}`,
              '-v', `${laneRoot}/e2e/fixtures/skills-r3/support-application:/fi_app:ro`, '--entrypoint', 'python', image, '/opt/ctxhub/measure.py', 'config']);
            state.provisioned = JSON.parse(fresh.stdout.trim().split('\n').at(-1));
          });
          // The context link opens on its application Tests tab (the tab is part of the link).
          await go('inspect-context-tests', `/dashboard/skills/contexts/${input.contextId}?version=${input.versions.recorded}&tab=tests`);
          // The application's production binding already exists; a deployment without one registers it here.
          if (await page.getByRole('button', { name: 'Add application target', exact: true }).count()) await op.click(page.getByRole('button', { name: 'Add application target', exact: true }), 'add-application-target');
          await op.click(page.getByRole('button', { name: 'Show app configuration', exact: true }), 'review-runtime-configuration');
          await expect(page.getByText('Pin the application configuration')).toBeVisible();
          await expect(page.getByText('Source commit')).toBeVisible();
          await shot('01-pin-configuration');
          await op.click(page.getByRole('button', { name: 'Pin this configuration', exact: true }), 'pin-application-configuration');
          await expect(page.getByText('Configuration pinned')).toBeVisible();
          const targets = await api('GET', `${root}native/targets/`);
          const measured = JSON.parse(execFileSync('docker', ['exec', input.runtimeContainer, 'python', '/opt/ctxhub/measure.py', 'config'], { encoding: 'utf8' }));
          requireValue(targets.targets[0].qualified && digest(targets.targets[0].configuration) === digest(measured), 'Pinned configuration differs from the runtime measurement');
          requireValue(digest(state.provisioned) === digest(measured), 'The runtime the customer built does not reproduce the pinned configuration');
          receipt('api', await record('target-qualified', { evidenceClass: 'synthetic fixture', target: targets.targets[0], measuredDigest: digest(measured), provisionedDigest: digest(state.provisioned), provisionedFromBuild: true }));
          return;
        }
        if (stage.id === 'external-failure') {
          // The retained incident is a synthetic OTLP project in this product's tracing, so opening it
          // is not a cross-system handoff. A real retained-Langfuse source would add a counted handoff.
          await go('synthetic-retained-incident', '/dashboard/observe');
          await op.click(page.getByText(input.projectName, { exact: true }), 'open-incident-project');
          await op.transition('physical-source-span', () => op.click(page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Synthetic refund failure' }), 'open-failed-trace'));
          const trace = await api('GET', `../../tracer/trace/${input.traceId}/?project_id=${input.projectId}`, undefined, { signal }).catch(() => null);
          receipt('api', await record('synthetic-source-observation', { evidenceClass: 'synthetic fixture', traceId: input.traceId, visible: Boolean(trace), retainedSystemConformance: false }));
          return;
        }
        if (stage.id === 'capture-and-redact') {
          const before = await api('GET', `${root}tests/`);
          const sourceResponse = page.waitForResponse((res) => res.url().endsWith(`${root}captures/source/`) && res.request().method() === 'POST');
          // The span names the context it was delivered from, so the review opens in it without choosing one.
          await op.click(page.getByRole('button', { name: 'Save as test case', exact: true }), 'capture-selected-span');
          const response = await sourceResponse;
          requireValue(response.ok(), 'Selected source was not authorized');
          state.source = (await response.json()).result;
          requireValue(state.source.trace_ref.project_id === input.projectId && state.source.trace_ref.trace_id === input.traceId && state.source.trace_ref.span_id === input.spanId, 'Captured source identity differs');
          const dialog = page.getByRole('dialog');
          const boxes = dialog.getByRole('checkbox');
          await expect(boxes).toHaveCount(state.source.segments.length);
          // Every supported segment starts selected; the reviewer deselects exceptions.
          for (let index = 0; index < state.source.segments.length; index++) await expect(boxes.nth(index)).toBeChecked();
          await shot('02a-segments-preselected');
          await op.transition('redaction-review', () => op.click(dialog.getByRole('button', { name: 'Preview selected text', exact: true }), 'review-selected-text'));
          await edit('case-title', () => dialog.getByRole('textbox', { name: 'Case name', exact: true }).fill(TITLE.refund));
          await edit('expected-answer', () => dialog.getByRole('textbox', { name: 'Expected answer must contain', exact: true }).fill('receipt'));
          // The reviewer adds which skill and which business action the case requires, in the same review.
          // Chosen from the context's own skills and the application's actions; nothing is typed.
          await op.click(dialog.getByLabel('Required skill', { exact: true }), 'open-required-skill');
          await op.click(page.getByRole('option', { name: /refund-policy$/ }), 'choose-required-skill');
          await op.click(dialog.getByLabel('Required business action', { exact: true }), 'open-required-action');
          await op.click(page.getByRole('option', { name: 'Issue a refund', exact: true }), 'choose-required-action');
          await op.click(dialog.getByRole('checkbox', { name: /confirmed the expected behavior/ }), 'confirm-expected-behavior');
          await op.click(dialog.getByRole('checkbox', { name: /workspace/ }), 'acknowledge-workspace-sharing');
          const decision = { packet_sha256: digest({ source: state.source, expected: 'receipt' }), preconditions_sha256: digest({ suiteRevision: before.revision, contextId: input.contextId }) };
          await dialog.getByText('Required application behavior').scrollIntoViewIfNeeded();
          await op.review('review-captured-case', state.source.segments.map((_, i) => `segment-${i}`), [], async () => { await shot('02-captured-case-review'); }, decision);
          const saved = page.waitForResponse((res) => res.url().includes('/captures/') && res.url().endsWith('/accept/') && res.request().method() === 'POST');
          await op.click(dialog.getByRole('button', { name: 'Save test case', exact: true }), 'save-reviewed-case');
          requireValue((await saved).ok(), 'Capture acceptance failed');
          await expect(dialog.getByRole('button', { name: 'Set up application test', exact: true })).toBeVisible();
          const suite = await api('GET', `${root}tests/`);
          const captured = suite.cases.find((item) => item.title === TITLE.refund);
          requireValue(captured?.reviewed && captured.source_group_id === `trace:${state.source.source_fingerprint}` && captured.fixtures.some((f) => f.name === 'fi.application.expectations')
            && captured.fixtures.some((f) => f.text.includes('support-order')), 'Independent captured-case observation differs');
          const behavior = JSON.parse(captured.fixtures.find((f) => f.name === 'fi.application.expectations').text);
          requireValue(behavior.skill_id === input.skills.refund && behavior.action === 'issue_refund', 'The required behavior was not saved with the reviewed case');
          const rows = await pool.query('SELECT trace_ref, source_hashes, accepted_receipt, recorded_version_id FROM model_hub_hubskillcontextcapture WHERE context_id=$1 AND workspace_id=$2', [input.contextId, input.actor.workspaceId]);
          requireValue(rows.rows.length === 1, 'Exactly one retained capture expected');
          state.capture = { caseId: captured.id, row: rows.rows[0] };
          const hash = await record('capture-observation', { evidenceClass: 'synthetic fixture', suite, capture: rows.rows[0], decision });
          receipt('api', hash, decision);
          return;
        }
        if (stage.id === 'reproduce-historical') {
          // The saved example hands off to its reproduction: the recorded v4 reference, the head candidate and the
          // historical flag are carried from stored data, so the only decision here is the paid run.
          await op.transition('reproduction-plan', () => op.click(page.getByRole('button', { name: 'Set up application test', exact: true }), 'open-reproduction'));
          await expect(page).toHaveURL(new RegExp(`reproduce=${state.capture.caseId}`));
          await expectPlan({ candidate: input.versions.head, reference: input.versions.recorded, titles: [TITLE.refund] });
          await shot('03a-reproduction-plan');
          const evidenceId = await startRun('reproduce');
          const run = await waitRun(evidenceId, signal);
          requireValue(run.status === 'completed' && run.result.reproduction === 'reproduced', `Historical failure was not reproduced (${run.result?.reproduction ?? run.failure})`);
          state.runs.reproduction = run;
          await op.transition('reproduction-result', () => expect(page.getByText(new RegExp(`the recorded failure reproduced on v${input.versions.recorded}`))).toBeVisible({ timeout: 120_000 }));
          await shot('03-reproduced');
          receipt('api', await record('reproduction-run', { evidenceClass: 'synthetic fixture', run }));
          return;
        }
        if (stage.id === 'preserve-and-start-hotfix') {
          // A reproduced failure offers the repair from its recorded version; the preservation review follows.
          await op.transition('historical-context', () => op.click(page.getByRole('button', { name: `Repair from the failing v${input.versions.recorded}`, exact: true }), 'repair-from-recorded'));
          await op.click(page.getByRole('checkbox', { name: 'Save a recovery checkpoint and suspend my current draft.', exact: true }), 'confirm-preservation');
          await op.click(page.getByRole('button', { name: 'Preserve and suspend draft', exact: true }), 'preserve-and-suspend');
          // The exact-origin start follows the explicit preservation by itself.
          await expect(page.getByRole('button', { name: 'Review against current latest', exact: true })).toBeVisible();
          const checkpoints = await pool.query('SELECT digest, payload FROM model_hub_hubskilldraftcheckpoint WHERE skill_id=$1 AND workspace_id=$2', [input.contextId, input.actor.workspaceId]);
          const draft = await api('GET', `${root}draft/`);
          requireValue(checkpoints.rows.length === 1 && draft.base_version === input.versions.recorded && draft.files.find((f) => f.path === 'AGENTS.md').content === input.recordedRoot, 'Preserved origin differs');
          state.preserved = { checkpoints: checkpoints.rows.length, base: draft.base_version };
          receipt('pg', await record('preserved-origin', { evidenceClass: 'synthetic fixture', checkpoint: checkpoints.rows[0].digest, base: draft.base_version }));
          return;
        }
        if (stage.id === 'repair-and-flush') {
          await op.click(page.getByText('POLICY-REFUND: full-amount'), 'focus-refund-clause');
          await edit('replace-refund-clause', async () => {
            await page.keyboard.press('Home'); await page.keyboard.press('Shift+End');
            await page.keyboard.type('POLICY-REFUND: capped-25');
          });
          await expect(page.getByText('POLICY-REFUND: capped-25')).toBeVisible();
          await expect(page.getByText('Saved', { exact: true })).toBeVisible();
          await op.transition('review-candidate', () => op.click(page.getByRole('button', { name: 'Review changes' }), 'review-changes'));
          await edit('version-note', () => page.getByLabel('Version note').fill('Cap refunds at the order total'));
          await op.click(page.getByRole('checkbox', { name: /I reviewed these changes against the current published version/ }), 'review-candidate-against-head');
          await op.click(page.getByRole('checkbox', { name: /I acknowledge the static warnings/ }), 'acknowledge-warnings');
          await op.click(page.getByRole('button', { name: 'Confirm review', exact: true }), 'acknowledge-head');
          // The acknowledged head reloads the review for the same candidate bytes. The reviewer's two acknowledgements
          // and version note carry across that step (asserted, not assumed); any change would start the review over.
          await expect(page.getByRole('button', { name: 'Publish version', exact: true })).toBeEnabled();
          await expect(page.getByRole('checkbox', { name: /I reviewed these changes against the current published version/ })).toBeChecked();
          await expect(page.getByRole('checkbox', { name: /I acknowledge the static warnings/ })).toBeChecked();
          await expect(page.getByLabel('Version note')).toHaveValue('Cap refunds at the order total');
          const published = page.waitForResponse((res) => res.url().endsWith(`${root}draft/publish/`) && res.request().method() === 'POST');
          published.catch(() => {});
          await op.click(page.getByRole('button', { name: 'Publish version', exact: true }), 'publish-repair');
          const result = (await (await published).json()).result;
          const number = result.version.number;
          const inspection = await api('GET', `${root}versions/${number}/inspection/`);
          const bytes = await api('GET', `${root}versions/${number}/inspection/files/?path=AGENTS.md`).catch(() => null);
          const row = (await pool.query('SELECT origin_number, parent_number FROM model_hub_hubskillversion WHERE skill_id=$1 AND number=$2', [input.contextId, number])).rows[0];
          requireValue(row.origin_number === input.versions.recorded && number > input.versions.head, 'Repair did not start from recorded v4 and publish after head');
          state.repaired = { number, origin: row.origin_number, parent: row.parent_number, id: result.version.id, digest: result.version.digest, inspection: Boolean(inspection), bytes };
          await expect(page.getByText('POLICY-REFUND: capped-25').first()).toBeVisible({ timeout: 30_000 }).catch(() => {});
          await shot('04-published-repair');
          receipt('pg', await record('repair-published', { evidenceClass: 'synthetic fixture', ...state.repaired }));
          return;
        }
        if (stage.id === 'matched-native-comparison') {
          // Publishing a repair carries the responder to the repaired release on its Tests tab; the application's
          // current production (its one live runtime binding) is already the baseline for the neighboring cases.
          await expect(page).toHaveURL(new RegExp(`version=${state.repaired.number}&tab=tests`));
          await expectPlan({ candidate: state.repaired.number, reference: input.versions.recorded, titles: Object.values(TITLE) });
          await expect(page.getByRole('combobox', { name: /^Compare with/ })).toContainText(`v${input.versions.head}`);
          await shot('05a-matched-plan');
          const evidenceId = await startRun('matched');
          const run = await waitRun(evidenceId, signal);
          requireValue(run.status === 'completed', `Matched run did not complete (${run.failure})`);
          state.runs.matched = run;
          state.matchedId = evidenceId;
          await op.transition('matched-result', () => expect(page.getByText(/Repair verified:|Repair not verified:|Application checks passed for these cases|Hold this release/).first()).toBeVisible({ timeout: 120_000 }));
          await expect(page.getByRole('region', { name: 'Latest result' }).getByText('Completed', { exact: true })).toBeVisible();
          await shot('05-matched-result');
          // Acceptance-matrix controls through the public API (not operator work).
          const targets = await api('GET', `${root}native/targets/`);
          const target = targets.targets[0];
          const suite = await api('GET', `${root}tests/`);
          const byTitle = Object.fromEntries(suite.cases.map((item) => [item.title, item.id]));
          const control = async (candidate, caseIds) => {
            const plan = { binding_id: target.binding_id, binding_revision: target.revision, candidate_version: candidate, baselines: { reference: input.versions.recorded }, case_ids: caseIds, repetitions: 1, historical_case_ids: [] };
            const planned = await api('POST', `${root}native/preview/`, plan);
            const created = await api('POST', `${root}native/runs/`, { ...plan, execution_fingerprint: planned.execution_fingerprint, acknowledge_spend: true });
            return waitRun(created.evidence_id, signal);
          };
          const publish = async (content, note) => {
            let draft = await api('POST', `${root}draft/start/`, { revision: 0, from_version: input.versions.recorded });
            draft = await api('PUT', `${root}draft/`, { revision: draft.revision, base_version: draft.base_version, files: [{ path: 'AGENTS.md', content }], description: 'Synthetic support desk', note });
            const review = await api('POST', `${root}draft/head-review/`, { revision: draft.revision });
            const ack = await api('POST', `${root}draft/acknowledge-head/`, { revision: draft.revision, expected_digest: draft.digest, expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint });
            return (await api('POST', `${root}draft/publish/`, { revision: ack.revision, expected_digest: draft.digest, base_version: draft.base_version, expected_head_version: review.expected_head_version, review_fingerprint: ack.review_fingerprint, note, acknowledge_warnings: true })).version.number;
          };
          ledger.fault('text-pass-native-tool-fail', 'wording-only-repair');
          const wording = await publish(input.controlRoots.wordingOnly, 'Control: wording-only repair');
          state.controls.textOnly = await control(wording, [byTitle[TITLE.refund]]);
          ledger.fault('routing-near-miss', 'dispute-as-refund');
          const overbroad = await publish(input.controlRoots.overbroad, 'Control: overbroad routing repair');
          state.controls.nearMiss = await control(overbroad, [byTitle[TITLE.chargeback]]);
          ledger.fault('missing-historical-state', 'case-without-business-state');
          const bare = { id: crypto.randomUUID(), title: 'Shipping without state', source_group_id: 'synthetic:bare', prompt: 'Where is my package? Please track the shipment.', fixtures: [{ id: crypto.randomUUID(), name: 'fi.application.expectations', text: JSON.stringify({ schema: 1, skill_id: input.skills.shipping, action: 'track_shipment' }) }], expected_trigger: null, reviewed: true,
            checks: [{ id: crypto.randomUUID(), kind: 'contains', critical: true, config: { text: 'package', case_sensitive: false } }] };
          const latest = await api('GET', `${root}tests/`);
          const keep = (item) => ({ id: item.id, title: item.title, source_group_id: item.source_group_id, prompt: item.prompt, fixtures: item.fixtures, expected_trigger: item.expected_trigger, checks: item.checks, reviewed: item.reviewed });
          await api('PUT', `${root}tests/`, { revision: latest.revision, cases: [...latest.cases, bare].map(keep) });
          state.controls.noState = await api('POST', `${root}native/preview/`, { binding_id: target.binding_id, binding_revision: target.revision, candidate_version: state.repaired.number, baselines: { reference: input.versions.recorded }, case_ids: [bare.id], repetitions: 1, historical_case_ids: [] });
          receipt('api', await record('matched-run', { evidenceClass: 'synthetic fixture', run, controls: state.controls }));
          return;
        }
        if (stage.id === 'inspect-and-annotate') {
          const run = state.runs.matched;
          const historical = run.cases.find((item) => item.historical);
          const cells = run.cells.filter((cell) => cell.case_id === historical.id && cell.variant === 'reference');
          const decision = { packet_sha256: digest({ run: run.id, cell: cells[0].attempt_id, outcomes: [cells[0].tools, cells[0].answer] }), preconditions_sha256: digest({ annotationRevision: run.annotation_revision, fingerprint: run.execution_fingerprint }) };
          await op.click(page.getByRole('row', { name: new RegExp(`${TITLE.refund}.*Was failing on v`) }).first(), 'open-original-failure-cell');
          await expect(page.getByText('Observed by the application')).toBeVisible();
          await shot('06-cell-observation');
          await op.click(page.getByRole('radio', { name: /Disagree/ }), 'choose-disagree');
          await edit('annotation-why', () => page.getByLabel('Why').fill('The answer claims success but the business ledger shows the refund was declined.'));
          const appended = page.waitForResponse((res) => res.url().includes('/annotations/') && res.request().method() === 'POST');
          await op.review('record-human-disagreement', [`cell-${cells[0].attempt_id}`], [], async () => { await page.getByRole('button', { name: 'Record assessment' }).click(); }, decision);
          requireValue((await appended).ok(), 'Annotation was not accepted');
          const list = await api('GET', `evidence/simulation_native/${run.id}/annotations/`);
          requireValue(list.results.length === 1 && list.results[0].verdict === 'disagree' && list.results[0].attempt_id === cells[0].attempt_id, 'Independent annotation observation differs');
          const after = await api('GET', `${root}native/runs/${run.id}/`);
          requireValue(canonicalCell(after.cells, cells[0]) === canonicalCell(run.cells, cells[0]), 'Automatic grade changed after annotation');
          await shot('07-annotation');
          receipt('api', await record('annotation', { evidenceClass: 'synthetic fixture', annotations: list.results, decision }), decision);
          return;
        }
        if (stage.id === 'retained-regression-pipeline') {
          // The incumbent CI is pointed at the exact candidate: one exact-version binding and a context-only credential.
          const label = `ci-${runId}`.toLowerCase();
          const access = await ledger.measure({ dimension: 'A', primitive: 'command', actor: 'incident-responder', step: stage.id, phase: stage.phase, target: 'issue-exact-candidate-access' }, async () => {
            const binding = await api('POST', `${root}bindings/`, { name: `retained-ci ${label}`.slice(0, 120), kind: 'runtime', selector: { version: state.repaired.number }, update_mode: 'manual', profile: 'retained-ci', profile_version: '1', repository: null, expected_report_interval_seconds: 3600 });
            const credential = await api('POST', 'contexts/credentials/', { purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: input.contextId, labels: [], version_ids: [state.repaired.id] }], binding_ids: [], expires_at: new Date(Date.now() + 3_600_000).toISOString() });
            return { bindingId: binding.id, secret: credential.secret };
          });
          // Handing the exact candidate identity and its credential to the separate CI system is a counted handoff.
          const ci = await op.handoff('retained-ci', () => op.command('run-retained-ci', 'docker', ['exec', '-e', `CTXHUB_CONTEXT_CREDENTIAL=${access.secret}`, '-e', 'CTXHUB_REGISTRY_URL=http://app:8000',
            input.runtimeContainer, 'python', '/opt/ctxhub/retained_ci.py', '--binding-id', access.bindingId, '--context-id', input.contextId, '--label', label], { timeoutMs: 280_000 }));
          // The pipeline prints its decision, configuration identity and per-case results; reading its output is
          // part of running it. The report file is read independently below, outside the operator ledger.
          const summary = JSON.parse(ci.stdout.trim().split('\n').at(-1));
          const report = JSON.parse(execFileSync('docker', ['exec', input.runtimeContainer, 'cat', `/fi_output/ci/${label}.json`], { encoding: 'utf8' }));
          requireValue(summary.passed === report.passed && summary.configuration_identity === report.configuration_identity && summary.decision === (report.passed ? 'passed' : 'failed')
            && digest(summary.cases) === digest(report.cases.map((row) => ({ case: row.case, status: row.status, passed: row.passed }))), 'The pipeline output differs from its independent report');
          state.ci = { label, summary, report };
          requireValue(state.ci.report.passed === true, 'The retained pipeline did not pass');
          receipt('external_pipeline', await record('retained-ci-report', { evidenceClass: 'synthetic fixture', report: state.ci.report }));
          return;
        }
        if (stage.id === 'release-ready-packet') {
          // The matched verification is still the one on screen (the CI ran elsewhere); the responder closes the
          // reviewed cell and continues; nothing is reopened or chosen again.
          await op.click(page.getByRole('button', { name: 'Close', exact: true }), 'close-reviewed-cell');
          // The optional CI attachment is shown open under the result; no gesture opens it.
          await expect(page.getByRole('button', { name: 'Attach your CI run (optional)', exact: true })).toHaveAttribute('aria-expanded', 'true', { timeout: 120_000 });
          await expect(page.locator(`[data-execution-fingerprint="${state.runs.matched.execution_fingerprint}"]`)).toBeVisible();
          // The pipeline's identity is carried from the CI result into the packet by the operator: a counted reconciliation.
          await op.reconcile('pipeline-identity-into-packet', async () => {
            await page.getByLabel('CI run link').fill(state.ci.summary.url);
            await page.getByLabel('CI pipeline name').fill(state.ci.report.configuration_identity);
            await page.getByLabel('CI result (pass or hold)').fill(state.ci.report.passed ? 'passed' : 'failed');
          });
          await op.click(page.getByRole('button', { name: 'Share run and attach CI result', exact: true }), 'prepare-release-packet');
          await expect(page.getByText(/Packet ready/)).toBeVisible();
          await shot('08-release-packet');
          const evidence = await api('GET', `evidence/simulation_native/${state.matchedId}/`);
          const preview = await api('POST', 'release-requests/preview/', { resource_id: input.contextId, kind: 'agent_context', candidate_version_id: state.repaired.id, label: 'production',
            target: (({ binding_id, revision, configuration_digest }) => ({ binding_id, environment_id: null, revision, configuration_digest }))((await api('GET', `${root}native/targets/`)).targets[0]),
            evidence: [{ source: 'simulation_native', source_id: state.matchedId, annotation_revision: evidence.annotation_revision }],
            external_pipeline: { url: state.ci.summary.url, configuration_identity: state.ci.report.configuration_identity, decision: 'passed', trust: 'operator_recorded_unimported', recorded_by: input.userId } });
          requireValue(evidence.visibility === 'workspace' && preview.packet.candidate_version_id === state.repaired.id && preview.packet.evidence.length === 1, 'Independent packet observation differs');
          state.packet = preview;
          receipt('api', await record('release-packet', { evidenceClass: 'synthetic fixture', fingerprint: preview.fingerprint, packet: preview.packet }));
          return;
        }
        if (stage.id === 'retained-trace-conformance') {
          // The separately measured J4 job releases the candidate to production. The harness stands in for it
          // outside this ledger: the production binding moves to the exact version, with a context-only credential for it.
          const bindings = await api('GET', `${root}bindings/`);
          const current = (bindings.results ?? bindings).find((row) => row.id === input.production.bindingId);
          const moved = await api('PATCH', `contexts/bindings/${input.production.bindingId}/`, { revision: current.revision, binding: { name: 'support-app production', kind: 'runtime', selector: { version: state.repaired.number }, update_mode: 'manual', profile: 'support-app', profile_version: '1', repository: null, expected_report_interval_seconds: 3600 } });
          const productionCredential = await api('POST', 'contexts/credentials/', { purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: input.contextId, labels: [], version_ids: [state.repaired.id] }], binding_ids: [], expires_at: new Date(Date.now() + 3_600_000).toISOString() });
          requireValue(moved.selector.version === state.repaired.number, 'Production binding did not move to the repaired version');
          const produced = JSON.parse(execFileSync('docker', ['exec', '-e', `CTXHUB_CONTEXT_CREDENTIAL=${productionCredential.secret}`, '-e', 'CTXHUB_REGISTRY_URL=http://app:8000', '-e', `TRACE_URL=${input.collectorInternalUrl}`,
            '-e', `TRACE_API_KEY=${input.apiKey}`, '-e', `TRACE_SECRET_KEY=${input.secretKey}`, '-e', `TRACE_PROJECT=${input.projectName}`, input.runtimeContainer, 'python', '/opt/ctxhub/production_request.py',
            '--binding-id', input.production.bindingId, '--context-id', input.contextId], { encoding: 'utf8' }).trim().split('\n').at(-1));
          state.production = produced;
          // Wait until the retained tracing store has the exported spans (independent of the UI).
          for (let attempt = 0; ; attempt++) {
            const count = Number((await (await fetch(`${input.chUrl}/?query=${encodeURIComponent(`SELECT count() FROM spans FINAL WHERE trace_id='${produced.trace_id}'`)}`, { headers: { Authorization: `Basic ${Buffer.from(`default:${input.chPassword}`).toString('base64')}` } })).text()).trim());
            if (count === 2) break;
            requireValue(attempt < 60 && !signal.aborted, 'The exported production trace did not arrive');
            await new Promise((r) => setTimeout(r, 2000));
          }
          await go('retained-tracing', '/dashboard/observe');
          await op.click(page.getByText(input.projectName, { exact: true }), 'open-incident-project');
          await op.transition('production-trace', () => op.click(page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Production refund request' }).first(), 'open-production-trace'));
          await page.waitForLoadState('networkidle');
          // The drawer is shown with whatever identity the tracing UI exposes; the exact identity is read from the API below.
          await expect(page.getByText(/fi\.context\.version_id|Production refund request/).nth(1)).toBeVisible({ timeout: 30_000 }).catch(() => {});
          await shot('09-production-trace');
          const trace = await api('GET', `../../tracer/trace/${produced.trace_id}/?project_id=${input.projectId}`);
          state.trace = JSON.stringify(trace);
          receipt('external_trace', await record('production-trace', { evidenceClass: 'synthetic fixture', trace: produced, observed: state.trace.length }));
          return;
        }
        throw new Error(`Unknown stage: ${stage.id}`);
      } catch (error) {
        // Operator-visible diagnostics for a failed stage; never request bodies or credentials.
        await page.screenshot({ path: resolve(output, 'shots', `failed-${stage.id}.png`), animations: 'disabled' }).catch(() => {});
        if (process.env.J2_DEBUG) console.error('J2_DEBUG', stage.id, String(error.message).slice(0, 700));
        throw error;
      } finally { signal.removeEventListener('abort', cancel); }
    },
    async observe(check, { signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      const run = state.runs.matched;
      const make = async (source, value, extra = {}) => ({ source, value, artifact_sha256: await record(`observe-${check.id}`, { evidenceClass: 'synthetic fixture', value, ...extra }) });
      const runtime = input.runtimeContainer;
      if (check.id === 'physical-source-and-redaction') {
        const row = state.capture.row;
        return make('pg', { projectId: row.trace_ref.project_id, traceId: row.trace_ref.trace_id, spanId: row.trace_ref.span_id, manualIdentityReentry: state.identityEdits > 0 });
      }
      if (check.id === 'historical-reproduced') {
        const rep = state.runs.reproduction;
        const attempt = await attemptOf(rep.job_id);
        const operations = runOperations(runtime, attempt, rep.cells);
        const reference = operations.find((row) => row.cell.variant === 'reference');
        const turns = firstTurns(await fixtureLedger());
        const recordedBytes = turns.some((turn) => turn.system.includes(input.recordedRoot.split('\n')[2]));
        const health = rep.runtime;
        const measured = JSON.parse(execFileSync('docker', ['exec', runtime, 'python', '/opt/ctxhub/measure.py', 'config'], { encoding: 'utf8' }));
        return make('guest', { reproduced: reference?.status === 'declined' && rep.result.reproduction === 'reproduced', exactHistoricalContext: recordedBytes && sha256(input.recordedRoot) === input.recordedRootSha256,
          qualifiedEnvironment: health.read_only_source === true && health.read_only_context === true && health.separate_writable_state === true && health.controlled_paid_egress === true && health.image_digest === measured.image_digest && health.code_digest === measured.code_digest });
      }
      if (check.id === 'origin-and-reviewed-head') {
        const row = (await pool.query('SELECT origin_number, parent_number FROM model_hub_hubskillversion WHERE skill_id=$1 AND number=$2', [input.contextId, state.repaired.number])).rows[0];
        return make('pg', { origin: row.origin_number, reviewedHead: row.parent_number, personalWorkPreserved: state.preserved.checkpoints === 1 });
      }
      if (check.id === 'case-arm-repeat-matrix') {
        const titles = Object.fromEntries(run.cases.map((item) => [item.id, item.title]));
        const rows = run.cells.map((cell) => ({ case_id: titles[cell.case_id], arm: cell.variant, repeat: cell.repeat_index, complete: cell.status === 'completed' && cell.attempt_id !== null }));
        return make('guest', rows);
      }
      if (check.id === 'natural-child-selection') {
        const ledger = await fixtureLedger();
        const turns = firstTurns(ledger);
        const candidateTurns = ledger.filter((row) => row.kind === 'model' && row.turn === 1 && row.messages[0].content.includes('POLICY-REFUND: capped-25') && !row.messages[0].content.includes('POLICY-ROUTING'));
        const selected = (row) => JSON.parse(row.messages.find((m) => m.role === 'assistant').tool_calls[0].function.arguments).skill_id;
        const byRequest = (text) => candidateTurns.filter((row) => row.request === text).map(selected);
        // Per catalog member: which candidate-arm requests selected it, retained as an artifact (not only a boolean).
        const catalogIds = [...new Set([...turns[0].system.matchAll(/"id":\s*"([^"]+)"/g)].map((match) => match[1]))];
        const wanted = { refund: input.skills.refund, chargeback: input.skills.chargeback, shipping: input.skills.shipping };
        const perMember = catalogIds.map((id) => {
          const selectedBy = Object.fromEntries(Object.keys(wanted).map((key) => [key, byRequest(input.requests[key]).filter((chosen) => chosen === id).length]));
          const expectedFor = Object.keys(wanted).filter((key) => wanted[key] === id);
          const correct = Object.keys(wanted).every((key) => (selectedBy[key] > 0) === expectedFor.includes(key));
          return { member_id: id, selected_by_request: selectedBy, expected_for: expectedFor, correct };
        });
        const requestsSeen = Object.fromEntries(Object.keys(wanted).map((key) => [key, byRequest(input.requests[key]).length]));
        return make('model_boundary', { catalogMembers: Math.min(...turns.map((t) => t.catalogMembers)), refundCorrect: byRequest(input.requests.refund).length > 0 && byRequest(input.requests.refund).every((id) => id === input.skills.refund),
          chargebackNearMissHeld: byRequest(input.requests.chargeback).length > 0 && byRequest(input.requests.chargeback).every((id) => id === input.skills.chargeback),
          shippingCorrect: byRequest(input.requests.shipping).length > 0 && byRequest(input.requests.shipping).every((id) => id === input.skills.shipping),
          membersWithResults: perMember.length, wrongMemberSelections: perMember.filter((row) => !row.correct).length },
        { perMember, requestsSeen });
      }
      if (check.id === 'tool-failure-hold') {
        const { textOnly, nearMiss } = state.controls;
        const evidence = await api('GET', `evidence/simulation_native/${textOnly.id}/`);
        const row = (await pool.query('SELECT count(*)::int AS n FROM model_hub_hubskillnativeevidence WHERE id=$1', [textOnly.id])).rows[0];
        return make('api', { textPassToolFailHeld: textOnly.decision.state === 'hold' && /text pass is not a repair/.test(textOnly.decision.reasons.join(' ')) && nearMiss.decision.state === 'hold',
          nativeEvidenceRequired: evidence.source === 'simulation_native' && row.n === 1 && state.controls.noState.reproduction === 'not_reproduced' }, { decisions: { textOnly: textOnly.decision, nearMiss: nearMiss.decision } });
      }
      if (check.id === 'neighboring-behavior') {
        const attempt = await attemptOf(run.job_id);
        const operations = runOperations(runtime, attempt, run.cells).filter((row) => row.cell.variant === 'candidate');
        const titles = Object.fromEntries(run.cases.map((item) => [item.id, item.title]));
        const ok = (title, action) => operations.some((row) => titles[row.cell.case_id] === title && row.action === action && row.status === 'applied');
        return make('guest', { chargebackPreserved: ok(TITLE.chargeback, 'open_chargeback'), shippingPreserved: ok(TITLE.shipping, 'track_shipment') });
      }
      if (check.id === 'external-identity') {
        const mentions = (id) => state.trace.includes(id);
        const old = state.repaired && input.v4Id;
        return make('external_trace', { changedHubIdentity: mentions(state.repaired.id) && mentions(state.repaired.digest) && state.production.version_id === state.repaired.id,
          oldExternalVersionReused: mentions(input.v4Id) }, { traceId: state.production.trace_id, old });
      }
      if (check.id === 'retained-pipeline-result') {
        const read = execFileSync('docker', ['exec', runtime, 'cat', `/fi_output/ci/${state.ci.label}.json`], { encoding: 'utf8' });
        const report = JSON.parse(read);
        return make('external_pipeline', { exactApplicationPipelineInspected: report.version_ids.length === 1 && report.version_ids[0] === state.repaired.id && report.cases.length === 3 && report.cases.every((row) => row.context_digest === state.repaired.digest),
          passed: report.passed === true }, { report });
      }
      throw new Error(`Whole-job independent evidence is not implemented: ${check.id}`);
    },
    async teardown() {
      await context?.close();
      await browser?.close();
      if (state.provisionImage) { try { execFileSync('docker', ['rmi', '-f', state.provisionImage], { stdio: 'ignore' }); } catch { /* best effort */ } }
      await fetch(`${input.fixtureOrigin}/scenarios/${input.providerNamespace}`, { method: 'DELETE' }).catch(() => {});
      await pool.end().catch(() => {});
      return { qualified: !browser || !browser.isConnected() };
    },
  };
}

function canonicalCell(cells, wanted) {
  const cell = cells.find((item) => item.attempt_id === wanted.attempt_id);
  return JSON.stringify({ selection: cell.selection, delivery: cell.delivery, tools: cell.tools, answer: cell.answer });
}
