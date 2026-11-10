import { execFile } from 'node:child_process';
import { readFile, writeFile, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';

const execute = promisify(execFile);
const hash = (value) => createHash('sha256').update(value).digest('hex');

/** Independent filesystem oracle: neither SDK verification nor a UI toast is truth. */
export function assertInventory(inventory, expected) {
  const ids = inventory.targets.map((row) => row.repository_id).sort();
  if (inventory.target_count !== expected.length || new Set(ids).size !== expected.length
      || JSON.stringify(ids) !== JSON.stringify(expected.map((row) => row.id).sort())) {
    throw new Error('inventory_target_accounting');
  }
  for (const witness of expected) {
    const target = inventory.targets.find((row) => row.repository_id === witness.id);
    if (!witness.available) {
      if (target.state !== 'unavailable') throw new Error('unavailable_target_lost');
      continue;
    }
    if (target.state !== 'inventoried' || target.ownership !== 'not_authorized'
        || target.observation.inventory_complete !== true) throw new Error('inventory_false_green');
    const actual = target.observation.findings.filter((row) => row.sha256 !== undefined)
      .map((row) => [row.path, row.sha256]).sort();
    const wanted = Object.entries(witness.files).sort();
    if (JSON.stringify(actual) !== JSON.stringify(wanted)) throw new Error('instruction_inventory_mismatch');
    if (target.observation.lock_sha256 !== witness.lock_sha256) throw new Error('lock_observation_mismatch');
  }
}

/** Executes the released CLI surface, with no imports of the SDK's implementation. */
export async function runInventory({ python, manifestPath, evidencePath, signal }) {
  let stdout;
  let exitCode = 0;
  try {
    ({ stdout } = await execute(python, ['-I', '-m', 'fi.context', 'inventory', '--repositories', manifestPath],
      { signal, timeout: 60_000, maxBuffer: 8 * 1024 * 1024 }));
  } catch (error) {
    // Exit 6 is the public CLI's explicit incomplete/unavailable result, not
    // success. Retain it so the independent oracle can account for every target.
    if (error.code !== 6 || typeof error.stdout !== 'string') throw error;
    stdout = error.stdout;
    exitCode = error.code;
  }
  const inventory = JSON.parse(stdout);
  const artifact = JSON.stringify({ exitCode, inventory }, null, 2);
  await writeFile(evidencePath, artifact, { flag: 'wx', mode: 0o600 });
  return { exitCode, inventory, artifact_sha256: hash(artifact) };
}

/**
 * J5 public-interface driver. This implementation qualifies the cold inventory
 * prefix only. It deliberately fails at ownership rather than substituting
 * synthetic plans/PR receipts for the missing qualified campaign executor.
 * The unchanged runner also refuses J5 without a real Checkpoint-P receipt.
 * Do not manufacture that receipt just to reach this prefix.
 */
export async function createDriver({ jobId, runId, registration }) {
  if (jobId !== 'J5' || !runId) throw new Error('J5 driver requires its registered job/run');
  const config = registration.driver_config;
  if (!config?.python || !config.manifest_path || !config.evidence_path || !config.expected_path) {
    throw new Error('J5 requires explicit installed SDK and selected repository inputs');
  }
  let observed;
  return {
    mode: 'product',
    provenance: registration.driver_provenance,
    async perform(stage, { ledger, signal }) {
      signal.throwIfAborted();
      if (stage.id !== 'inventory-existing-repositories') {
        throw new Error('j5_incomplete: ownership/executor/merge/install/load/reverse and authorized GitHub conformance are not qualified');
      }
      const witnessBytes = await readFile(config.expected_path, 'utf8');
      const expected = JSON.parse(witnessBytes);
      if (expected.length !== 40) throw new Error('J5 requires all forty selected repositories');
      observed = await ledger.measure({ dimension: 'A', primitive: 'command', actor: 'operator',
        step: stage.id, phase: stage.phase, target: 'selected-repositories',
        items: expected.map((row) => `repo-${row.id}`),
        exceptionItems: expected.filter((row) => !row.available).map((row) => `repo-${row.id}`) },
      () => runInventory({ python: config.python, manifestPath: config.manifest_path,
        evidencePath: config.evidence_path, signal }));
      assertInventory(observed.inventory, expected);
      const selected = JSON.parse(await readFile(config.manifest_path, 'utf8'));
      const current = [];
      for (const witness of expected) {
        signal.throwIfAborted();
        const project = selected.find((row) => row.repository_id === witness.id)?.project;
        if (!project) throw new Error('independent_selection_mismatch');
        if (!witness.available) {
          try {
            await lstat(project);
          } catch (error) {
            if (error.code !== 'ENOENT') throw error;
            current.push({ ...witness, files: {} });
            continue;
          }
          throw new Error('unavailable_repository_now_present');
        }
        const files = {};
        for (const path of Object.keys(witness.files)) {
          if (path.startsWith('/') || path.split('/').some((part) => ['', '.', '..'].includes(part))) {
            throw new Error('invalid_witness_relative_path');
          }
          files[path] = hash(await readFile(join(project, path)));
        }
        let lock = null;
        try { lock = hash(await readFile(join(project, '.futureagi-context.lock.json'))); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
        current.push({ ...witness, files, lock_sha256: lock });
      }
      assertInventory(observed.inventory, current);
      const filesystemArtifact = JSON.stringify(current, null, 2);
      await writeFile(config.evidence_path + '.filesystem.json', filesystemArtifact, { flag: 'wx', mode: 0o600 });
      ledger.stage({ step: stage.id, phase: stage.phase, source: 'filesystem',
        artifactHash: hash(filesystemArtifact), actionSequences: [] });
    },
    async observe(check, { signal }) {
      signal.throwIfAborted();
      // A local selection inventory is not the hub's frozen campaign membership,
      // PR accounting, pin policy, model delivery or real GitHub conformance.
      throw new Error(`j5_unobserved:${check.id}`);
    },
    async teardown() {
      // Inputs belong to the caller. Destruction/settlement of the complete job
      // is not established by finishing this read-only prefix.
      return { qualified: false };
    },
  };
}
