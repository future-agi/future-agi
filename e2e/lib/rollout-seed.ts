import { execFileSync } from 'node:child_process';
import { resolveBackendContainer } from './simulate-seed';

/**
 * Admits one organization to an Omega product the way a staff allow entry at stage S0
 * does (`omega_rollout.testing.admit_for_tests`), inside the running backend container.
 *
 * The rollout's own write paths (`omega_rollout` admin API and management command) need a
 * named staff operator holding `operate_rollout`, and this harness signs up ordinary
 * users only, so there is no product route to call instead. Scoped per call: only the
 * given org is admitted, so parallel workers never share an admission. The container is
 * found as `seedCallExecutions` finds it (the stack whose Postgres publishes
 * `E2E_PG_URL`'s port; `SIMULATE_SEED_CONTAINER` overrides).
 */
const ADMIT_SCRIPT = `
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()

from accounts.models import Organization
from omega_rollout import products
from omega_rollout.testing import admit_for_tests

product = os.environ["SEED_PRODUCT"]
if not products.is_product(product):
    raise SystemExit(f"unknown Omega product {product!r}")
organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
admit_for_tests(organization, products=[product])
print("admitted", product, organization.id)
`;

export function admitToProduct(organizationId: string, product: 'deployments'): void {
  const container = resolveBackendContainer();
  execFileSync('docker', ['exec', '-i',
    '-e', `SEED_ORG_ID=${organizationId}`,
    '-e', `SEED_PRODUCT=${product}`,
    container, 'sh', '-c', 'cd /app/backend && PYTHONPATH=/app/backend python -',
  ], { input: ADMIT_SCRIPT, encoding: 'utf-8' });
}
