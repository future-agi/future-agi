import { test, expect } from '../lib/fixtures';
import { fetchDeploymentMode } from '../lib/deployment';

// Denial reasons that mean the LICENCE ITSELF did not validate, as opposed to
// a valid licence that simply omits a feature
// (futureagi/tfc/capabilities/service.py DenialReason).
const LICENCE_INVALID_REASONS = new Set([
  'LICENSE_MISSING',
  'LICENSE_INVALID',
  'LICENSE_EXPIRED',
]);

interface CapabilitiesResponse {
  features: Record<string, { allowed: boolean; reason_code: string | null }>;
}

// Mode and entitlement are decided by different inputs, and only one of them is
// visible in CI's green tick. `GET /api/deployment-info/` reports "ee" as soon
// as EE_LICENSE_KEY is merely SET, while a feature is allowed only if the
// licence validates against a trust root — and `_BUNDLED_KEYS` in
// futureagi/ee/licensing/keyring.py is still empty pre-GA, so that root is
// EE_LICENSE_PUBLIC_KEY or nothing.
//
// Miss that key, or paste a PEM whose armour is malformed, and the ee leg still
// boots as "ee", still passes its mode check, and quietly runs every gated flow
// down its locked branch: a second OSS run wearing an EE label. This test is
// what makes that state loud.
test('EE mode carries a licence that actually validates', async ({ actor }) => {
  const mode = await fetchDeploymentMode(actor.api);
  test.skip(mode !== 'ee', `deployment mode is "${mode}", not ee`);

  const caps = await actor.api.get<CapabilitiesResponse>('/api/capabilities/');
  const unvalidated = Object.entries(caps.features)
    .filter(([, f]) => f.allowed !== true && LICENCE_INVALID_REASONS.has(f.reason_code ?? ''))
    .map(([id, f]) => `${id}=${f.reason_code}`);

  // LICENSE_FEATURE_MISSING is deliberately absent from the set above: a valid
  // licence that omits a feature is a real, supported configuration.
  expect(
    unvalidated,
    'the licence did not validate — check EE_LICENSE_PUBLIC_KEY is set and its PEM armour is intact',
  ).toEqual([]);
});
