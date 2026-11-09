import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import type { MockReceipt } from '../../lib/managed-mock';

const root = fileURLToPath(new URL('../../../', import.meta.url));

/** Fault injection for a dedicated, single-worker proof, never a shared suite.
 * No API responses or product code are replaced: the real Temporal RPC times out.
 */
export function pauseAdmissionService(receipt: MockReceipt, workers: number): () => void {
  if (process.env.E2E_EXPERIMENT_ADMISSION_OUTAGE !== '1') return () => {};
  if (workers !== 1 || process.env.E2E_STACK === 'standalone' ||
      !/^futureagi-e2e-xp-admission-[a-z0-9-]+$/.test(receipt.project) ||
      process.env.E2E_COMPOSE_PROJECT !== receipt.project || process.env.DOCKER_HOST) {
    throw new Error('Admission outage requires a dedicated distributed project and one worker');
  }
  const run = (file: string, args: string[]): string => {
    try {
      return execFileSync(file, args, { encoding: 'utf8', timeout: 30_000,
        env: { ...process.env, DOCKER_CONTEXT: receipt.context }, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch {
      // Do not expose subprocess output or environment values.
      throw new Error('Admission outage control failed');
    }
  };
  const docker = (...args: string[]) => run('docker', ['--context', receipt.context, ...args]);
  const daemon = JSON.parse(docker('context', 'inspect', receipt.context));
  if (daemon.length !== 1 || !daemon[0].Endpoints?.docker?.Host?.startsWith('unix://')) {
    throw new Error('Admission outage requires a local Docker daemon');
  }
  const id = run(`${root}bin/e2e`, ['compose', 'ps', '-q', 'temporal']).trim();
  if (!/^[a-f0-9]{64}$/.test(id)) throw new Error('Admission service is missing or ambiguous');
  // Inspect only identity/state fields; never return container credentials.
  const inspect = () => JSON.parse(docker('inspect', '--format',
    '{"labels":{{json .Config.Labels}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"networks":{{json .NetworkSettings.Networks}}}', id));
  const before = inspect();
  const networks = Object.values(before.networks) as { NetworkID: string }[];
  if (before.labels['com.docker.compose.project'] !== receipt.project ||
      before.labels['com.docker.compose.service'] !== 'temporal' ||
      !before.running || before.paused || networks.length !== 1 ||
      networks[0].NetworkID !== receipt.networkId) {
    throw new Error('Admission service is not the running isolated Temporal container');
  }
  let restored = false;
  const restore = () => {
    if (!restored) {
      docker('unpause', id);
      restored = true;
    }
  };
  try {
    docker('pause', id);
    if (!inspect().paused) throw new Error('Admission fault was not applied');
  } catch (error) {
    // A timed-out pause or inspection may already have reached the daemon.
    // Attempt cleanup even if another inspection would also fail.
    restore();
    throw error;
  }
  return restore;
}
