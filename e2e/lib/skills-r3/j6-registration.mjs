import { readdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { getJob } from '../../benchmarks/context-hub/jobs.mjs';
import { approvedEnvironment } from '../../benchmarks/context-hub/runner.mjs';
import { COMMIT, digest, requireValue, sha256 } from './canonical.mjs';
import { API, GOOD_ROOT } from './j6-scenario.mjs';

const exec = promisify(execFile);

// Deterministic fixture constants, fixed BEFORE the measured run. They come from
// the packaged application's own checkpoint arithmetic for the refund task:
// model turn, load, model turn, read, model turn, refund tool (receipt persisted
// at checkpoint 5, registry lost before the advance); resume advances it to 7.
export const PAUSED_CHECKPOINT = 5;
export const COMPLETED_CHECKPOINT = 7;

/** Preregistration is derived from the prepared fixture, never from a run's output. */
export async function buildJ6Registration({ seed, provenance, wheelsDir, guest, author, apiUrl }) {
  requireValue(provenance && [provenance.product_base, provenance.product_head, provenance.product_tree, provenance.sdk_commit].every((value) => COMMIT.test(value)), 'Supply real source refs in J6_PROVENANCE; dirty provenance stays explicit');
  requireValue(['gcp', 'blacksmith'].includes(provenance.environment) && approvedEnvironment(provenance) && typeof provenance.dirty === 'boolean', 'Expected an observed GCP project or Blacksmith runner and checkout state in J6_PROVENANCE');
  const headers = { Authorization: `Bearer ${author.access}`, 'X-Organization-Id': author.organizationId, 'X-Workspace-Id': author.workspaceId };
  const files = [];
  for (let offset = 0; ; offset = files.length) {
    const response = await fetch(`${apiUrl}${API}contexts/${seed.contextId}/versions/1/inspection/?file_offset=${offset}&file_limit=25`, { headers, redirect: 'error', signal: AbortSignal.timeout(30_000) });
    requireValue(response.ok, 'Retained fixture inspection unavailable');
    const value = (await response.json()).result;
    requireValue(value.manifest.version_id === seed.versions[0].id && value.distribution_blocked === false, 'The recovery candidate changed');
    files.push(...value.manifest.files.map(({ path, sha256: hash, size_bytes }) => ({ path, sha256: hash, size_bytes })));
    if (value.manifest.next_file_offset === null) break;
    requireValue(value.manifest.next_file_offset === files.length && files.length < 10_000, 'Incomplete fixture manifest');
  }
  const packages = (await readdir(wheelsDir)).filter((path) => path.endsWith('.whl')).sort();
  requireValue(packages.length === 2, 'Pin both SDK and application wheels');
  const packageHashes = await Promise.all(packages.map(async (path) => sha256(await readFile(resolve(wheelsDir, path)))));
  const image = (await exec('docker', ['image', 'inspect', guest.names.image, '--format', '{{.Id}}'], { encoding: 'utf8' })).stdout.trim().replace(/^sha256:/, '');
  const filled = { environment: provenance.environment, project: provenance.project, runner: provenance.runner, run_id: provenance.run_id, product_base: provenance.product_base, product_head: provenance.product_head,
    product_tree: provenance.product_tree, sdk_commit: provenance.sdk_commit, dirty: provenance.dirty, fixture_hash: seed.catalogHash, runtime_digest: image,
    package_hashes: packageHashes, lockfile_hashes: [sha256(await readFile('yarn.lock'))] };
  const job = getJob('J6');
  const registration = { schema: 1, job: 'J6', cohort: 'configured', evidence_scope: 'synthetic',
    customer_input_hash: seed.catalogHash, initial_conditions_hash: digest({ versions: seed.versions, bindings: seed.bindings, incident: seed.incident.codes, orders: Object.keys(seed.orders), goodCommit: seed.goodCommit }),
    safety_requirements_hash: digest({ complete_closure: true, duplicate_business_effects: 0, false_recovered_states: 0, eight_fault_matrix: job.fault_ids }),
    complete_job_boundary_hash: digest({ steps: job.steps, checks: job.checks }), population: job.population, fault_ids: job.fault_ids,
    expected_observations: {
      'eligible-complete-closure': files,
      'rollback-event-and-aba': { rollback: { from_version: 3, to_version: 1, events: 1 }, aba_rejected: { status: 409, label_events_added: 0 }, quarantined_preview_refused: { code: 'context_quarantined' } },
      'original-checkpoint-identity': { same_session: true, same_version_id: seed.versions[0].id, paused_checkpoint: PAUSED_CHECKPOINT, completed_checkpoint: COMPLETED_CHECKPOINT, receipt_before_checkpoint: true },
      'business-action-count': { orders: 4, applied_operations: 4, business_requests: 4, duplicate_effects: 0 },
      'original-case-recovered': { status: 'applied', amount: 25, rule: 'refund_within_window', delivered_root_sha256: sha256(Buffer.from(GOOD_ROOT(1), 'utf8')), retired_child_bytes_delivered: true },
      'unknown-consumer-remains-unknown': { U: ['unknown'] },
      'revoked-quarantined-denied': { expired_credential_denied: true, quarantined_inspection_blocked: true },
    },
    notes: 'Synthetic fixture, VM container guest (not E2B or hosted Simulation). The closure is preregistered from public inspection metadata; the driver independently reads every file chunk.' };
  return { registration, provenance: filled };
}
