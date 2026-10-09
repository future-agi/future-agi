// The managed mock's licence check (lib/managed-mock.ts).
//
// The shared E2E stacks boot with a test-signed Enterprise licence (README
// "Licences on the E2E stacks") so every Playwright actor can keep its own
// organization under the Community rule. Wherever the inspection is strict (the
// eval background opt-in, and always on Standalone) the managed mock admits a
// licence only when all of this holds, and STOPs the run otherwise:
//
//   - it is exactly the licence bin/e2e wrote for this Compose project
//     (<E2E_LICENCE_DIR>/<project>/licence.env), and the container trusts
//     exactly that file's public key;
//   - its RS256 signature verifies against that throwaway key, and its claims
//     are the E2E test licence's (e2e/scripts/test-licence.mjs licenceClaims)
//     with no product features, so Falcon AI, Turing Models and Protect stay off;
//   - FUTURE_AGI_LICENSE_URL is the closed loopback port and the Enterprise
//     heartbeat is disabled.
//
// Why that is enough: activation (futureagi/ee/licensing/activation_client.py)
// and the heartbeat (heartbeat.py) are the backend's only calls to Future AGI's
// licence service, both go to FUTURE_AGI_LICENSE_URL, and every managed-service
// token comes from activation. With nothing listening on that port no token is
// ever issued, so neither the licence service nor a managed gateway can be
// reached. The gateway client stays on the mock because AGENTCC_INTERNAL_API_KEY
// is pinned to the mock key (gateway_llm_client._deployment_mode).
//
// Nothing derived from either key is printed or returned in a receipt.
import { createPublicKey, verify, type KeyObject } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { requireSafe } from './managed-mock';

/** docker-compose.e2e.yml and docker-compose.standalone-e2e.yml: a port nothing listens on. */
export const CLOSED_LICENCE_SERVICE = 'http://127.0.0.1:9';

export interface LaneLicence {
  /** E2E_LICENCE_STATE from the file: enterprise or expired (removed carries no key). */
  state: string;
  key: string;
  /** EE_LICENSE_PUBLIC_KEY exactly as the env file, and so the container, carries it. */
  publicKey: string;
}

/** bin/e2e LICENCE_ENV for `project`. */
export function laneLicenceFile(project: string, env: NodeJS.ProcessEnv = process.env): string {
  const base = env.E2E_LICENCE_DIR || path.join(env.TMPDIR || '/tmp', 'futureagi-e2e-licence');
  return path.join(base, project, 'licence.env');
}

// e2e/scripts/test-licence.mjs licenceClaims, minus the dates.
const TEST_CLAIMS: Record<string, unknown> = {
  typ: 'futureagi-enterprise-license', iss: 'https://licenses.futureagi.com', aud: 'futureagi-self-hosted',
  license_id: 'lic_e2e_test_0001', customer_id: 'cus_e2e_test', band: 'e2e-test',
};

/** STOP unless `key` is the E2E test licence, signed by `publicKey`'s throwaway key, with no product features. */
export function verifyTestLicence(key: string, publicKey: string): void {
  const parts = key.split('.');
  requireSafe(parts.length === 3 && parts.every(part => /^[A-Za-z0-9_-]+$/.test(part)), 'licence is not a compact JWT');
  let header: Record<string, unknown>;
  let claims: Record<string, unknown>;
  let trusted: KeyObject;
  try {
    header = JSON.parse(Buffer.from(parts[0], 'base64url').toString('utf8'));
    claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
    // One line in the env file; tfc/settings/settings.py turns the literal \n back into newlines.
    trusted = createPublicKey(publicKey.replace(/\\n/g, '\n'));
  } catch {
    throw new Error('STOP: managed mock licence or its public key is unreadable');
  }
  requireSafe(header.alg === 'RS256' && header.kid === 'default', 'licence is not signed for the env public key');
  requireSafe(verify('sha256', Buffer.from(`${parts[0]}.${parts[1]}`), trusted, Buffer.from(parts[2], 'base64url')),
    'licence is not signed by the lane test key');
  requireSafe(Object.entries(TEST_CLAIMS).every(([name, value]) => claims[name] === value),
    'licence is not the E2E test licence');
  requireSafe(Array.isArray(claims.features) && claims.features.length === 0, 'test licence enables product features');
}

/** The licence bin/e2e wrote for `project`, verified; undefined when the stack runs without one. */
export function readLaneLicence(project: string, env: NodeJS.ProcessEnv = process.env): LaneLicence | undefined {
  const file = laneLicenceFile(project, env);
  if (!existsSync(file)) return undefined;
  const values: Record<string, string> = {};
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    const match = /^([A-Z0-9_]+)=(.*)$/.exec(line);
    if (match) values[match[1]] = match[2];
  }
  if (!values.EE_LICENSE_KEY) return undefined;
  requireSafe(values.EE_LICENSE_PUBLIC_KEY, 'lane licence has no public key');
  verifyTestLicence(values.EE_LICENSE_KEY, values.EE_LICENSE_PUBLIC_KEY);
  return { state: values.E2E_LICENCE_STATE ?? '', key: values.EE_LICENSE_KEY, publicKey: values.EE_LICENSE_PUBLIC_KEY };
}

/** What a licensed container must carry wherever the inspection is strict. */
export function licensedMockValues(licence: LaneLicence): Record<string, string> {
  return { EE_LICENSE_KEY: licence.key, EE_LICENSE_PUBLIC_KEY: licence.publicKey,
    FUTURE_AGI_LICENSE_URL: CLOSED_LICENCE_SERVICE, FUTURE_AGI_ENTERPRISE_HEARTBEAT_DISABLED: 'true' };
}
