import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';

// The Community edition lane (README "The Community edition lane"): its own
// disposable Standalone stack, booted as a fresh Community install by
// `E2E_STACK=community bin/e2e up` and wiped by `bin/e2e down -v`. A flow on
// it changes the licence the way an operator does, by setting EE_LICENSE_KEY
// and restarting: `bin/e2e licence <state>` signs a test licence with the
// lane's throwaway key, recreates the app container alone (the database and
// every volume stay) and waits for the backend's own validator verdict.

export type LaneLicence = 'enterprise' | 'expired' | 'removed';

const BIN_E2E = process.env.E2E_BIN || fileURLToPath(new URL('../../bin/e2e', import.meta.url));

/** Fail fast when a lane flow is pointed at any other stack. */
export function assertCommunityLane(): void {
  if (process.env.E2E_STACK !== 'community' || !process.env.E2E_API_URL) {
    throw new Error('This flow runs on the Community lane only: E2E_STACK=community bin/e2e up, '
      + 'then E2E_STACK=community bin/e2e test');
  }
}

/** Switch the lane's licence and restart its app; resolves once the backend
 * has validated the new licence state. Rejects with the command's output. */
export function setLaneLicence(state: LaneLicence, timeoutMs: number): Promise<void> {
  assertCommunityLane();
  return new Promise((resolve, reject) => {
    execFile(BIN_E2E, ['licence', state], { env: process.env, timeout: timeoutMs, maxBuffer: 16 << 20 },
      (err, stdout, stderr) => {
        if (!err) return resolve();
        const tail = `${stdout}\n${stderr}`.trim().split('\n').slice(-15).join('\n');
        reject(new Error(`bin/e2e licence ${state} failed: ${err.message}\n${tail}`));
      });
  });
}
