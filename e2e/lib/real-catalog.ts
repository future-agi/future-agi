// The mcp-catalog profile's catalogue preflight (lib/profile.ts): Django serves
// the connector catalogue this build bundles, and nothing is mounted over it.
// Its flow (GW-E2E-026) runs these before it reads the catalogue.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { inspectManagedMock, requireSafe, type Container } from './managed-mock';
import { walkGatewayProfile } from './profile';

const root = fileURLToPath(new URL('../../', import.meta.url));
/** The catalogue this build bundles: Django's copy of agentcc-catalog's dist/catalog.json. */
export const BUNDLE_FILE = `${root}futureagi/agentcc/connector_catalog/catalog.json`;
const DJANGO_CATALOG_DIR = '/app/backend/agentcc/connector_catalog';
// H-MCP's mounts: its fixture catalogue and its mock's trust root.
const FIXTURE_DIR = '/opt/e2e/mcp';

export interface Bundle {
  version: string; sha256: string; sourceCommit: string;
  apps: { id: string; name: string; icon?: { light?: string; dark?: string } | null }[];
  providers: { transport?: { url?: string } }[];
}

export const readBundle = (): Bundle => JSON.parse(readFileSync(BUNDLE_FILE, 'utf8'));

const refuse = (ok: unknown, reason: string): void => {
  if (!ok) throw new Error(`STOP: ${reason}`);
};

/** The catalogue the catalog API names (its `catalog`: sha256, version and
 * source_commit) must be the bundle's, exactly. */
export function validateRealCatalog(named: unknown, bundle: Bundle): void {
  const card = (named && typeof named === 'object' ? named : {}) as Record<string, unknown>;
  refuse(card.sha256 === bundle.sha256 && card.version === bundle.version &&
    card.source_commit === bundle.sourceCommit,
  'the connector catalogue Django serves is not the one this build bundles');
}

/** The app container mounts nothing over Django's catalogue copy, and none of
 * H-MCP's fixture mounts. */
export function validateRealCatalogMounts(container: Pick<Container, 'Mounts'>): void {
  refuse(!container.Mounts.some((m) => m.Destination === DJANGO_CATALOG_DIR ||
    m.Destination.startsWith(`${DJANGO_CATALOG_DIR}/`) || m.Destination === FIXTURE_DIR ||
    m.Destination.startsWith(`${FIXTURE_DIR}/`)),
  'the app mounts a connector catalogue over the one this build bundles');
}

/** Before any catalogue read: attest the local stack, then that the app container
 * mounts nothing over the bundled catalogue. Returns the bundle the API must name. */
export function inspectRealCatalog(): Bundle {
  requireSafe(walkGatewayProfile() === 'mcp-catalog', 'the real catalogue flow requires the mcp-catalog profile');
  inspectManagedMock();
  const ids = execFileSync(`${root}bin/e2e`, ['compose', 'ps', '-q', 'app'], { encoding: 'utf8', timeout: 15_000 })
    .trim().split(/\s+/);
  requireSafe(ids.length === 1 && /^[a-f0-9]{12,64}$/.test(ids[0]), 'the app container is ambiguous');
  const containers: Container[] = JSON.parse(execFileSync('docker', ['inspect', ids[0]], { encoding: 'utf8', timeout: 15_000 }));
  requireSafe(containers.length === 1, 'the app container is ambiguous');
  validateRealCatalogMounts(containers[0]);
  return readBundle();
}
