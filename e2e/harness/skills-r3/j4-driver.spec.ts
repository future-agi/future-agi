import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises';
import { resolve } from 'node:path';
import { spawnSync, execFileSync } from 'node:child_process';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { SkillsFixtures, answerRule } from '../../lib/skills-fixtures';
import { getJob, digest, sha256 } from './j4-registration.mjs';

// Fixture preparation is not a measured product journey. Evidence class:
// synthetic fixture. The registered CLI emits its own complete or failed
// reports; a successful fixture invocation is never whole-job qualification.
// Prerequisites created here: a released baseline context, the author's saved
// candidate draft and two completed private text runs on that exact digest.
// The configured cohort additionally starts with the reviewer authorized and
// the application target registered; the cold cohort configures both in-run.
const B = '/model-hub/skills/';
const COHORTS = (process.env.J4_COHORTS || 'cold,configured').split(',') as Array<'cold' | 'configured'>;

test('J4 registered driver retains honest cold and configured reports', async ({ scopeActors, request }, testInfo) => {
  test.setTimeout(3_600_000); // Two cohorts, each with a 25-minute child budget.
  if (process.env.SKILLS_JOURNEY_CASE === 'j4') expect(COHORTS).toEqual(['cold', 'configured']);
  const root = resolve(import.meta.dirname, '../../..');
  const output = resolve(process.env.J4_MEASUREMENT_DIR || '/tmp/j4-evidence/measurement');
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(process.env.J4_PROVENANCE || '/tmp/j4-provenance.json', 'utf8'));
  const service = process.env.E2E_STACK === 'standalone' ? 'app' : 'backend';
  const container = execFileSync(resolve(root, 'bin/e2e'), ['compose', 'ps', '-q', service], { encoding: 'utf8' }).trim();
  const runtimeDigest = execFileSync('docker', ['inspect', '--type', 'container', '--format', '{{.Image}}', container], { encoding: 'utf8' }).trim().replace(/^sha256:/, '');
  const packageHashes = await Promise.all(['frontend/package.json', 'e2e/package.json'].map(async path => sha256(await readFile(resolve(root, path)))));
  const lockHashes = await Promise.all(['frontend/yarn.lock', 'e2e/yarn.lock'].map(async path => sha256(await readFile(resolve(root, path)))));
  const sourcePaths = [
    'e2e/benchmarks/context-hub/drivers/j4.mjs',
    'e2e/stack/backend/e2e_j4_evidence_clock.py',
    'frontend/src/api/skills/review-authoring.js',
    'frontend/src/api/skills/review-production.js',
    'frontend/src/sections/skills/review/ReleaseRequestComposer.jsx',
    'frontend/src/sections/skills/review/ReleaseRequestPanel.jsx',
    'frontend/src/sections/skills/review/ReleaseRenewalDialog.jsx',
    'frontend/src/sections/skills/review/SkillsReviewControllers.jsx',
    'frontend/src/sections/skills/contexts/ContextTargetRegistration.jsx',
    'frontend/src/sections/skills/contexts/ContextReleaseReview.jsx',
    'frontend/src/sections/skills/contexts/ContextDetailView.jsx',
    'frontend/src/sections/skills/contexts/ContextDetailController.jsx',
    'futureagi/model_hub/services/skills_hub/r3/release_requests.py',
    'futureagi/model_hub/services/skills_hub/r3/release_options.py',
    'futureagi/model_hub/services/skills_hub/r3/release_inspection.py',
    'futureagi/model_hub/services/skills_hub/v12/consumers.py',
  ];
  const sourceHashes = Object.fromEntries(await Promise.all(sourcePaths.map(async path => [path, sha256(await readFile(resolve(root, path)))])));
  await writeFile(resolve(output, 'source-files.json'), JSON.stringify(sourceHashes, null, 2));
  const admin = scopeActors.ownerA;
  const author = scopeActors.member;
  const reviewer = scopeActors.viewer;
  const call = async (actor: typeof author, method: string, path: string, data?: unknown, base = B) => {
    const response = await request.fetch(`${E2E.apiUrl}${base}${path}`, { method, data, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() } });
    const body = await response.json();
    expect(response.ok(), JSON.stringify({ path, status: response.status(), body })).toBe(true);
    return body.result;
  };
  const role = (wsLevel: number) => call(admin, 'POST', 'organization/members/role/', { user_id: reviewer.userId, ws_level: wsLevel, workspace_id: admin.workspaceId }, '/accounts/');
  const wire = JSON.parse(await readFile(resolve(root, 'frontend/src/api/skills/__tests__/r3_l07/wire-fixtures.json'), 'utf8'));
  // Synthetic build output for the application target; inert identities only.
  const runtimeManifest = { ...wire.NativeConfiguration, entrypoint: 'refund_agent.app:invoke', reset_entrypoint: 'refund_agent.app:reset',
    observable_boundaries: ['model_request', 'root_delivery', 'checkpoint', 'child_selection', 'tool_result'].sort() };
  const job = getJob('J4');
  const fakes: SkillsFixtures[] = [];
  try {
    for (const cohort of COHORTS) {
      // One fake-model namespace per cohort; namespaces cannot be re-registered.
      const fake = new SkillsFixtures();
      fakes.push(fake);
      const runId = `j4-r6-${cohort}-${Date.now().toString(36)}`;
      const name = `e2e-${runId}`;
      const targetName = `Refund agent ${runId.slice(-6)}`;
      const manifestPath = resolve(output, `${runId}-runtime-manifest.json`);
      await writeFile(manifestPath, JSON.stringify(runtimeManifest, null, 2));
      // Cold starts from a workspace viewer that cannot review or release.
      await role(cohort === 'cold' ? 1 : 8);
      const created = await call(author, 'POST', 'contexts/', { name, description: 'Refund policy for the support agent', tags: [],
        files: [{ path: 'AGENTS.md', content: 'Refunds above 100 USD need a human approval.\n' }] });
      const id = created.context_id;
      const publish = async (note: string) => {
        const draft = await call(author, 'GET', `contexts/${id}/draft/`);
        const head = await call(author, 'POST', `contexts/${id}/draft/head-review/`, { revision: draft.revision });
        const ack = { revision: draft.revision, expected_digest: head.candidate_digest, expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint };
        const acknowledged = await call(author, 'POST', `contexts/${id}/draft/acknowledge-head/`, ack);
        return call(author, 'POST', `contexts/${id}/draft/publish/`, { ...ack, revision: acknowledged.revision, review_fingerprint: acknowledged.review_fingerprint, base_version: acknowledged.base_version, note, acknowledge_warnings: true });
      };
      const v1 = await publish('Released baseline');
      // The baseline release itself was reviewed by the workspace owner.
      const pv = await call(author, 'POST', 'release-requests/preview/', { resource_id: id, kind: 'agent_context', candidate_version_id: v1.version.id, label: 'production', target: null, evidence: [], external_pipeline: null });
      const sub = await call(author, 'POST', 'release-requests/', { packet: pv.packet, expected_fingerprint: pv.fingerprint, recipient_ids: [admin.userId] });
      await call(admin, 'POST', `release-requests/${sub.id}/decisions/`, { revision: sub.revision, expected_fingerprint: pv.fingerprint, decision: 'approve', comment: 'Baseline fixture' });
      await call(admin, 'POST', `release-requests/${sub.id}/execute/`, { revision: sub.revision, expected_fingerprint: pv.fingerprint });
      await call(author, 'POST', `contexts/${id}/draft/start/`, { revision: 0, from_version: 1 });
      const started = await call(author, 'GET', `contexts/${id}/draft/`);
      await call(author, 'PUT', `contexts/${id}/draft/`, { revision: started.revision, base_version: 1,
        files: [{ path: 'AGENTS.md', content: 'Refunds above 100 USD need a human approval.\nNever promise a refund before the order status is checked.\n' }],
        description: 'Refund policy for the support agent', note: '' });
      const draft = await call(author, 'GET', `contexts/${id}/draft/`);
      const prompts = [`${name}: refund 250 USD now?`, `${name}: order not found, refund?`];
      await fake.register(prompts.map((prompt) => answerRule(prompt, 0, { eligible: false })));
      await fake.connect(author.api);
      const runs: string[] = [];
      for (const [index, prompt] of prompts.entries()) {
        const config = { candidate: { kind: 'draft', revision: draft.revision, digest: draft.digest },
          cases: { kind: 'ad_hoc', cases: [{ id: randomUUID(), title: index ? 'Unknown order' : 'Large refund', source_group_id: randomUUID(), prompt, fixtures: [], expected_trigger: null, reviewed: true,
            checks: [{ id: randomUUID(), kind: 'json_equals', critical: true, config: { pointer: '/eligible', value: false } }] }] },
          baselines: [{ kind: 'production' }], competitors: [], harness: 'contexts-text-v1.2', model: { model_id: 'gpt-4o-mini', temperature: 0, max_output_tokens: 128 },
          judge: null, profile: 'quick', repetitions: 1, limits: { max_total_tokens: 150000, max_estimated_cost_usd: '1' }, purpose: 'regression', parent_run_id: null };
        const preview = await call(author, 'POST', `contexts/${id}/test-runs/preview/`, config);
        const run = await call(author, 'POST', `contexts/${id}/test-runs/`, { configuration: config, expected_fingerprint: preview.fingerprint, acknowledge_provider_processing: true,
          inventory_review: { fingerprint: preview.fingerprint, reviewed_all_files: true, no_known_answer_keys: true, classifications: [], false_positive_secret_findings: [] } });
        await expect.poll(async () => (await call(author, 'GET', `contexts/${id}/test-runs/${run.id}/`)).status, POLL.ASYNC_JOB).toBe('completed');
        runs.push(run.id);
      }
      let bindingId: string | null = null;
      if (cohort === 'configured') {
        const binding = await call(admin, 'POST', `contexts/${id}/bindings/`, { name: targetName, kind: 'runtime', selector: { label: 'production' },
          update_mode: 'new_thread', profile: 'native-runtime', profile_version: '1', repository: null, expected_report_interval_seconds: 3600, configuration: runtimeManifest });
        bindingId = binding.id;
      }
      const registration = { schema: 1, job: 'J4', cohort, evidence_scope: 'synthetic', population: job.population,
        customer_input_hash: digest({ name, baseline: 'Refunds above 100 USD need a human approval.', candidateDigest: draft.digest, evidenceRuns: 2 }),
        initial_conditions_hash: digest({ cohort, reviewerWorkspaceLevel: cohort === 'cold' ? 1 : 8, target: cohort === 'cold' ? 'unregistered' : 'registered', contextId: id, privateEvidence: runs.length }),
        safety_requirements_hash: digest(job.checks), complete_job_boundary_hash: digest(job.steps), fault_ids: job.fault_ids,
        expected_observations: { 'inbox-recipient-set': [reviewer.userId], 'unauthorized-unread-count': 0,
          'decision-actor-packet': true, 'stale-release-refused': true, 'exact-release-event': true,
          'pre-move-currentness': true, 'production-unchanged-before-release': true } };
      const registrationPath = resolve(output, `${runId}-registration.json`);
      await writeFile(registrationPath, JSON.stringify(registration, null, 2));
      const inputPath = `/tmp/${runId}-private.json`;
      const privateActor = (actor: typeof author, label: string) => ({ ...actor.tokens, userId: actor.userId, name: label, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
      await writeFile(inputPath, JSON.stringify({ appUrl: E2E.appUrl, apiUrl: E2E.apiUrl, pgUrl: E2E.pgUrl, outputDir: output, cohort,
        contextId: id, contextName: name, baselineVersion: 1, manifestPath, targetName, bindingId, evidenceIds: runs,
        admin: privateActor(admin, `E2E ${scopeActors.prefix}-a`), author: privateActor(author, `${scopeActors.prefix}-member`),
        reviewer: privateActor(reviewer, `${scopeActors.prefix}-viewer`),
        provenance: { ...manifest, source_hashes: sourceHashes, fixture_hash: digest({ baseline: v1.version, draft: draft.digest, runs, runtimeManifest }), runtime_digest: runtimeDigest, package_hashes: packageHashes, lockfile_hashes: lockHashes },
      }), { mode: 0o600 });
      try {
        const executed = spawnSync(process.execPath, ['e2e/benchmarks/context-hub/cli.mjs', 'run', 'J4', runId, registrationPath, 'registered', output], {
          cwd: root, env: { ...process.env, J4_DRIVER_INPUT: inputPath }, encoding: 'utf8', timeout: 1_500_000,
        });
        await writeFile(resolve(output, `${runId}-cli.json`), JSON.stringify({ exitCode: executed.status, signal: executed.signal, stdout: executed.stdout, stderr: executed.stderr }, null, 2));
        expect.soft(executed.status, `${cohort} CLI exit`).toBe(0);
        const report = await readFile(resolve(output, `${runId}.json`), 'utf8').then(JSON.parse).catch(() => null);
        // A missing report fails this run, but must not prevent the other cohort from executing.
        expect.soft(report, `${cohort} registered report`).toBeTruthy();
        if (!report) continue;
        await writeFile(resolve(output, `${cohort}.json`), JSON.stringify(report, null, 2));
        if (process.env.SKILLS_JOURNEY_CASE === 'j4') await testInfo.attach(`registration-${cohort}`, { contentType: 'application/json', body: JSON.stringify({
          status: report.status, provenance: report.provenance,
        }) });
        // Keep a failed report and still exercise the second cohort.
        expect.soft(report.status, `${cohort} journey`).toBe('completed');
        expect.soft(report).toMatchObject({ job: 'J4', cohort, run_id: runId, mode: 'product', evidence_scope: 'synthetic',
          teardown: 'confirmed', observations_complete: true, preregistration_hash: digest(registration),
          provenance: { product_head: manifest.product_head, runtime_digest: runtimeDigest } });
      } finally { await unlink(inputPath); }
    }
  } finally { for (const fake of fakes) await fake.dispose(); }
});
