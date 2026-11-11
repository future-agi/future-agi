import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile, unlink } from 'node:fs/promises';
import { resolve } from 'node:path';
import { spawnSync, execFileSync } from 'node:child_process';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { getJob, digest, sha256 } from './j4-registration.mjs';

// Fixture preparation is not a measured product journey. The registered CLI
// emits its own failed/complete reports, including partial counts and misses.
// Never promote a successful fixture invocation into whole-job qualification.
test('J4 registered driver retains honest cold and configured reports', async ({ scopeActors, request }) => {
  test.setTimeout(660_000);
  const root = resolve(import.meta.dirname, '../../..');
  const output = resolve(process.env.J4_MEASUREMENT_DIR || '/tmp/j4-evidence/measurement');
  await mkdir(output, { recursive: true });
  const manifest = JSON.parse(await readFile(process.env.J4_PROVENANCE || '/tmp/j4-provenance.json', 'utf8'));
  const runtimeDigest = execFileSync('docker', ['image', 'inspect', 'futureagi/standalone:e2esa', '--format', '{{.Id}}'], { encoding: 'utf8' }).trim().replace(/^sha256:/, '');
  const packageHashes = await Promise.all(['frontend/package.json', 'e2e/package.json'].map(async path => sha256(await readFile(resolve(root, path)))));
  const lockHashes = await Promise.all(['frontend/yarn.lock', 'e2e/yarn.lock'].map(async path => sha256(await readFile(resolve(root, path)))));
  const sourcePaths = [
    'e2e/benchmarks/context-hub/drivers/j4.mjs',
    'frontend/src/api/skills/review-authoring.js',
    'frontend/src/api/skills/review-production.js',
    'frontend/src/api/skills/context-consumers.js',
    'futureagi/model_hub/serializers/skills_hub_v12.py',
    'futureagi/model_hub/services/skills_hub/v12/consumers.py',
    'frontend/src/sections/skills/review/ReleaseRequestComposer.jsx',
    'frontend/src/sections/skills/review/ReleaseInbox.jsx',
    'frontend/src/sections/skills/review/SkillsReviewControllers.jsx',
    'futureagi/model_hub/services/skills_hub/r3/release_options.py',
    'futureagi/model_hub/services/skills_hub/r3/release_inspection.py',
    'frontend/src/sections/skills/review/ReleaseReviewBody.jsx',
    'futureagi/model_hub/views/skills_release_requests.py',
    'futureagi/model_hub/serializers/skills_release_requests.py',
    'futureagi/model_hub/urls_skills_release_requests.py',
  ];
  const sourceHashes = Object.fromEntries(await Promise.all(sourcePaths.map(async path => [path, sha256(await readFile(resolve(root, path)))])));
  await writeFile(resolve(output, 'source-files.json'), JSON.stringify(sourceHashes, null, 2));
  const author = scopeActors.member;
  const reviewer = scopeActors.ownerA;
  const call = async (actor: typeof author, method: string, path: string, data: unknown) => {
    const response = await request.fetch(`${E2E.apiUrl}/model-hub/skills/${path}`, { method, data, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() } });
    expect(response.ok()).toBe(true);
    return (await response.json()).result;
  };
  const job = getJob('J4');
  for (const cohort of ['cold', 'configured'] as const) {
    const runId = `j4-r5-${cohort}-${Date.now().toString(36)}`;
    const name = `e2e-${runId}`;
    const files = (body: string) => [{ path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Use when reviewing a synthetic release policy.\n---\n${body}\n` }];
    const baseline = await call(reviewer, 'POST', 'publish/', { files: files('Previous reviewed policy.'), labels: ['production'], expected_release_revision: 0 });
    await call(author, 'PUT', `${baseline.skill.id}/draft/`, { revision: 0, base_version: 1, files: files('Candidate policy requiring independent approval.') });
    const registration = { schema: 1, job: 'J4', cohort, evidence_scope: 'synthetic', population: job.population,
      customer_input_hash: digest({ name, initialDraft: true, baselineVersion: 1 }),
      initial_conditions_hash: digest({ configuredIdentities: true, cohort, skillId: baseline.skill.id, evidence: 'no typed evidence fixture available' }),
      safety_requirements_hash: digest(job.checks), complete_job_boundary_hash: digest(job.steps), fault_ids: job.fault_ids,
      expected_observations: { 'inbox-recipient-set': [reviewer.userId], 'unauthorized-unread-count': 0,
        'decision-actor-packet': true, 'stale-release-refused': true, 'exact-release-event': true,
        'pre-move-currentness': true, 'production-unchanged-before-release': true } };
    const registrationPath = resolve(output, `${runId}-registration.json`);
    await writeFile(registrationPath, JSON.stringify(registration, null, 2));
    const inputPath = `/tmp/${runId}-private.json`;
    const privateActor = (actor: typeof author, name: string) => ({ ...actor.tokens, userId: actor.userId, name, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
    await writeFile(inputPath, JSON.stringify({ appUrl: E2E.appUrl, apiUrl: E2E.apiUrl, pgUrl: E2E.pgUrl, outputDir: output,
      skillId: baseline.skill.id, skillName: name, baselineVersion: 1,
      author: privateActor(author, 'Synthetic author'), reviewer: privateActor(reviewer, `E2E ${scopeActors.prefix}-a`),
      provenance: { ...manifest, source_hashes: sourceHashes, fixture_hash: digest({ baseline: baseline.version, files: files('Candidate policy requiring independent approval.') }), runtime_digest: runtimeDigest, package_hashes: packageHashes, lockfile_hashes: lockHashes },
    }), { mode: 0o600 });
    try {
      const executed = spawnSync(process.execPath, ['e2e/benchmarks/context-hub/cli.mjs', 'run', 'J4', runId, registrationPath, 'registered', output], {
        cwd: root, env: { ...process.env, J4_DRIVER_INPUT: inputPath }, encoding: 'utf8', timeout: 300_000,
      });
      await writeFile(resolve(output, `${runId}-cli.json`), JSON.stringify({ exitCode: executed.status, signal: executed.signal, stdout: executed.stdout, stderr: executed.stderr }, null, 2));
      const report = JSON.parse(await readFile(resolve(output, `${runId}.json`), 'utf8'));
      await writeFile(resolve(output, `${cohort}.json`), JSON.stringify(report, null, 2));
      // The invocation must leave an actual harness report; completeness belongs
      // to that report, not an expectation that forces an unfinished job green.
      expect(report.run_id).toBe(runId);
      expect(report.mode).toBe('product');
      expect(report.evidence_scope).toBe('synthetic');
    } finally { await unlink(inputPath); }
  }
});
