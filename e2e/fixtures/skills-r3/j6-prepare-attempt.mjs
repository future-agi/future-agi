// Cloud-only preregistration for a synthetic, already-configured incident.
// This does not count fixture provisioning as customer work, create a comparator,
// qualify the native topology, or turn a partial driver into a complete job.
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { getJob } from '../../benchmarks/context-hub/jobs.mjs';
import { COMMIT, digest, requireValue, sha256 } from '../../lib/skills-r3/canonical.mjs';

requireValue(process.platform === 'linux', 'Use the assigned cloud VM');
const [seedPath, directory, productHead, productTree, sdkCommit, wheelsDirectory] = process.argv.slice(2);
requireValue([productHead, productTree, sdkCommit].every(value => COMMIT.test(value)), 'Supply real source refs; dirty provenance remains explicit');
const seed = JSON.parse(await readFile(seedPath, 'utf8'));
requireValue(['localhost', '127.0.0.1'].includes(new URL(seed.apiUrl).hostname), 'Owned stack required');
const headers = { Authorization: `Bearer ${seed.author.access}`, 'X-Organization-Id': seed.author.organizationId, 'X-Workspace-Id': seed.author.workspaceId };
const files = [];
for (let offset = 0; ; offset = files.length) {
  const response = await fetch(`${seed.apiUrl}/model-hub/skills/contexts/${seed.contextId}/versions/${seed.targetVersion}/inspection/?file_offset=${offset}&file_limit=25`, { headers, redirect: 'error', signal: AbortSignal.timeout(30_000) });
  requireValue(response.ok, 'Retained fixture inspection unavailable');
  const value = (await response.json()).result;
  requireValue(value.manifest.version_id === seed.initial.version.id && value.distribution_blocked === false, 'Initial candidate changed');
  files.push(...value.manifest.files.map(({ path, sha256, size_bytes }) => ({ path, sha256, size_bytes })));
  if (value.manifest.next_file_offset === null) break;
  requireValue(value.manifest.next_file_offset === files.length && files.length > offset && files.length < 10000, 'Incomplete fixture manifest');
}
const packages = (await readdir(wheelsDirectory)).filter(path => path.endsWith('.whl'));
requireValue(packages.length === 2, 'Pin both SDK and application wheels');
const packageHashes = await Promise.all(packages.sort().map(async path => sha256(await readFile(resolve(wheelsDirectory, path)))));
const runtimeDigest = execFileSync('docker', ['image', 'inspect', 'futureagi/standalone:e2esa', '--format', '{{.Id}}'], { encoding: 'utf8' }).trim().replace(/^sha256:/, '');
const provenance = { environment: 'gcp', project: 'fagi-omega-staging', product_base: productHead,
  product_head: productHead, product_tree: productTree, sdk_commit: sdkCommit, dirty: true,
  fixture_hash: seed.fixture_hash, runtime_digest: runtimeDigest, package_hashes: packageHashes,
  lockfile_hashes: [sha256(await readFile('e2e/yarn.lock'))] };
const job = getJob('J6');
const registration = { schema: 1, job: 'J6', cohort: 'configured', evidence_scope: 'synthetic',
  customer_input_hash: seed.fixture_hash, initial_conditions_hash: digest(seed.initial),
  safety_requirements_hash: digest({ complete_closure: true, duplicate_business_effects: 0, false_recovered_states: 0 }),
  complete_job_boundary_hash: digest({ steps: job.steps, checks: job.checks }), population: job.population, fault_ids: job.fault_ids,
  expected_observations: {
    'eligible-complete-closure': files,
    'rollback-event-and-aba': { rollback: true, aba_rejected: true },
    'original-checkpoint-identity': { session_id: seed.initial.expected_session, version_id: seed.initial.version.id },
    'business-action-count': 1,
    'original-case-recovered': true,
    'unknown-consumer-remains-unknown': { binding_id: seed.bindingIds[1], states: ['unknown'] },
    'revoked-quarantined-denied': { revoked_denied: true, quarantined_denied: true },
  },
  notes: 'Synthetic fixture. Public inspection metadata preregisters immutable file hashes; the driver independently reads actual file chunks. This attempt cannot establish a ratio or native qualification.',
};
await mkdir(directory, { recursive: true });
await writeFile(resolve(directory, 'registration.json'), JSON.stringify(registration, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
await writeFile(resolve(directory, 'provenance.json'), JSON.stringify(provenance, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
// Keep the credential-bearing input outside the evidence directory.
await writeFile(seedPath + '.driver', JSON.stringify({ ...seed, outputDir: resolve(directory, 'observations'), provenance }), { mode: 0o600, flag: 'wx' });
