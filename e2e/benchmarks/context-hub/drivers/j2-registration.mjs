// Cloud-only. Preregisters the J2 expected observations BEFORE the registered driver runs and binds
// provenance. It is not a result and never reads one.
//   node j2-registration.mjs INPUT.json COHORT OUT_DIR
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

if (process.platform !== 'linux') throw new Error('Run only on the assigned cloud VM');
const [inputPath, cohort, outDir] = process.argv.slice(2);
const root = process.env.J2_LANE_ROOT;
const { getJob } = await import(pathToFileURL(resolve(root, 'e2e/benchmarks/context-hub/jobs.mjs')));
const { digest } = await import(pathToFileURL(resolve(root, 'e2e/lib/skills-r3/canonical.mjs')));
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const input = JSON.parse(await readFile(inputPath, 'utf8'));
const job = getJob('J2');
const image = execFileSync('docker', ['image', 'inspect', 'futureagi/native-runtime:e2esa', '--format', '{{.Id}}'], { encoding: 'utf8' }).trim().replace(/^sha256:/, '');
const runtime = resolve(root, 'e2e/stack/native-runtime');
const manifest = (await readFile(resolve(runtime, 'wheels/SHA256SUMS'), 'utf8')).trim().split('\n');
const wheels = await Promise.all(manifest.map(async line => {
  const [, expected, name] = /^([a-f0-9]{64})\s+\.\/([a-zA-Z0-9_.-]+\.whl)$/.exec(line) ?? [];
  if (!name || sha(await readFile(resolve(runtime, 'wheels', name))) !== expected) throw new Error('Native wheel does not match the declared distribution');
  return expected;
}));
const lock = await Promise.all(['frontend/yarn.lock', 'e2e/yarn.lock'].map(async (path) => sha(await readFile(resolve(root, path)))));
const titles = ['Original refund failure', 'Working chargeback case', 'Working shipping case'];
const catalog = { skills: Object.keys(input.skills), requests: input.requests, titles };
input.provenance = {
  environment: 'gcp', project: 'fagi-omega-staging', vm: process.env.CTXHUB_VM,
  product_base: process.env.PRODUCT_BASE, product_head: process.env.PRODUCT_HEAD, product_tree: process.env.PRODUCT_TREE,
  sdk_commit: process.env.SDK_COMMIT, dirty: process.env.PRODUCT_DIRTY !== 'false',
  fixture_hash: digest(catalog), runtime_digest: image, package_hashes: wheels, lockfile_hashes: lock,
  limitations: ['Synthetic incident span in this product\'s tracing, not retained Langfuse.', 'Container runtime with measured isolation, not E2B template qualification.', 'Deterministic model fixture reads delivered context bytes; no provider quality claim.'],
};
await writeFile(inputPath, JSON.stringify(input), { mode: 0o600 });
const expected = {
  'physical-source-and-redaction': { projectId: input.projectId, traceId: input.traceId, spanId: input.spanId, manualIdentityReentry: false },
  'historical-reproduced': { reproduced: true, exactHistoricalContext: true, qualifiedEnvironment: true },
  'origin-and-reviewed-head': { origin: 4, reviewedHead: 6, personalWorkPreserved: true },
  'case-arm-repeat-matrix': titles.flatMap((case_id) => ['candidate', 'reference', 'production'].map((arm) => ({ case_id, arm, repeat: 0, complete: true }))),
  'natural-child-selection': { catalogMembers: 15, refundCorrect: true, chargebackNearMissHeld: true, shippingCorrect: true, membersWithResults: 15, wrongMemberSelections: 0 },
  'tool-failure-hold': { textPassToolFailHeld: true, nativeEvidenceRequired: true },
  'neighboring-behavior': { chargebackPreserved: true, shippingPreserved: true },
  'external-identity': { changedHubIdentity: true, oldExternalVersionReused: false },
  'retained-pipeline-result': { exactApplicationPipelineInspected: true, passed: true },
};
const registration = { schema: 1, job: 'J2', cohort, evidence_scope: 'synthetic', evidence_class: 'synthetic fixture',
  customer_input_hash: digest({ catalog, source: { projectId: input.projectId, traceId: input.traceId, spanId: input.spanId } }),
  initial_conditions_hash: digest({ cohort, source: 'real OTLP synthetic failure', preexistingReviewedCases: 2, sharedApplication: 'container runtime', personalDraft: 'v6', targetPinned: cohort === 'configured' }),
  safety_requirements_hash: digest({ authorization: true, exactIdentities: true, noFalseGreen: true, noBusinessDuplicates: true, noNeighborRegression: true }),
  complete_job_boundary_hash: digest(job.steps), population: job.population, expected_observations: expected, fault_ids: job.fault_ids,
  independent_comparator_reviewer: null, strongest_configured_route_hash: null,
  limitations: ['Synthetic fixture mechanics; the ratio against a customer comparator is not established.',
    'Cold scope: the packaged runtime build and measured-configuration pin are counted; the application source, the 15-skill context, the tracing project that holds the incident, workspace credentials and the spend policy are preconditions recorded in initial_conditions_hash and are not counted.'] };
await mkdir(outDir, { recursive: true });
await writeFile(resolve(outDir, `${cohort}-registration.json`), JSON.stringify(registration, null, 2), { mode: 0o600 });
console.log('registered', cohort);
