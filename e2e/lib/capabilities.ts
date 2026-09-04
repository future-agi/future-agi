import { ApiClient } from './api-client';

// Entitlements as the backend resolves them, which is NOT the same question as
// `GET /api/deployment-info/`'s oss|ee.
//
// Deployment mode is keyed on EE_LICENSE_KEY merely being SET
// (futureagi/tfc/views/deployment.py), while a feature is allowed only if the
// license actually names it (tfc/capabilities/service.py `_check_self_hosted_ee`)
// — and only for the `oss_locked` ones, since everything else runs free
// off-cloud. Two valid EE licenses therefore disagree: this repo's dev key
// grants falcon_ai, CI's does not, so a mode-gated spec passes locally and
// fails in CI on a 402 that is correct product behaviour.
//
// Gate on the capability and a spec is right under any license.
interface CapabilitiesResponse {
  features: Record<string, { allowed: boolean; reason_code: string | null }>;
}

export type Capability =
  | 'agentic_eval' | 'falcon_ai' | 'turing_models' | 'protect' | 'error_feed';

export async function fetchCapabilities(api: ApiClient): Promise<Record<string, boolean>> {
  const res = await api.get<CapabilitiesResponse>('/api/capabilities/');
  return Object.fromEntries(
    Object.entries(res.features).map(([id, f]) => [id, f.allowed === true]),
  );
}

// Fails closed: an id the backend did not report reads as denied rather than
// silently taking the entitled branch.
export function allowed(caps: Record<string, boolean>, id: Capability): boolean {
  return caps[id] === true;
}
