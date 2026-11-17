import { isDeepStrictEqual } from 'node:util';
import type { GatewayAdmin } from '../../../lib/gateway-admin';
import { requireId, requireUuid } from './identifiers';
import { requireConfigSection, type ConfigSection } from './postgres-config';

type Admin = Pick<GatewayAdmin, 'config' | 'orgConfig' | 'credits' | 'key' | 'mcpServed'>;
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Unexpected admin object');
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Unexpected admin string');
  return value;
}
function flag(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('Unexpected admin boolean');
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    throw new Error('Admin integer missing or unsafe; a lossless wire reader is required');
  }
  return value;
}

export interface GatewayCreditState {
  org_id: string; granted_total_micros: string; granted_version: string;
  spent_micros: string; held_micros: string; available_micros: string;
  outbox_pending: string; ledger: string;
}

/** Endpoint/wire provenance: e2e/lib/gateway-admin.ts:credits and
 * agentcc-gateway/internal/server/credits_admin.go:creditsAdminView.
 * Reuse the local-only admin harness. Decimal strings normalize safe int64s;
 * values beyond JS's safe range FAIL rather than pretending rounded money is exact.
 * OutboxPending is the gateway's aggregate, not a per-request ack/marker proof.
 */
export async function readGatewayCredits(admin: Admin, organizationId: string): Promise<GatewayCreditState> {
  const org = requireUuid(organizationId);
  const value = await admin.credits(org);
  if (value.org_id !== org) throw new Error('Gateway credits returned a different organization');
  return {
    org_id: org, granted_total_micros: String(integer(value.granted_total_micros)),
    granted_version: String(integer(value.granted_version)), spent_micros: String(integer(value.spent_micros)),
    held_micros: String(integer(value.held_micros)), available_micros: String(integer(value.available_micros)),
    outbox_pending: String(integer(value.outbox_pending)), ledger: text(value.ledger),
  };
}

/** GET /-/config: server.go:configHandler. This endpoint does NOT expose
 * managed.require_redis or actual replica/container identities on this head.
 * Those witnesses remain owed; never infer them from ledger='redis'.
 */
export async function readGatewaySettings(admin: Admin) {
  const value = await admin.config();
  const logging = object(value.logging);
  const pipeline = object(value.guardrail_pipeline);
  return {
    logging: { request_logging: flag(logging.request_logging),
      include_bodies: flag(logging.include_bodies), content_mode: text(logging.content_mode) },
    guardrail_pipeline: { enabled: flag(pipeline.enabled), fail_open: flag(pipeline.fail_open),
      mode: text(pipeline.mode) },
    providers_count: integer(value.providers_count), models_count: integer(value.models_count),
  };
}

/** GET /-/orgs/{org}/config: admin_orgs.go:GetOrgConfig; meta.version is
 * generated/gateway_admin.go:ConfigMeta, managed_models is ManagedModelsConfig.
 * Omitted section/field stays null, NOT false/zero. No secret-bearing config
 * sections are returned. This meta version is not the credit-account ack version.
 */
export async function readGatewayOrgIdentity(admin: Admin, organizationId: string) {
  const value = await admin.orgConfig(requireUuid(organizationId));
  if (value === null) return null;
  const meta = value.meta === undefined ? null : object(value.meta);
  const managed = value.managed_models === undefined ? null : object(value.managed_models);
  return {
    version: meta === null ? null : integer(meta.version),
    config_id: meta?.config_id === undefined ? null : text(meta.config_id),
    managed_enabled: managed?.enabled === undefined ? null : flag(managed.enabled),
  };
}

/** Exact gateway-side section equality, not PG JSON equality: config_push.py
 * transforms several sections. The spec pins expected to the gateway contract.
 * Missing org/section is explicit and never equated to {} or a disabled default.
 */
export async function readGatewaySectionEquality(
  admin: Admin, organizationId: string, section: ConfigSection, expected: unknown,
): Promise<{ organizationPresent: boolean; sectionPresent: boolean; matches: boolean }> {
  requireConfigSection(section);
  if (expected === undefined) throw new Error('Explicit gateway section expectation required');
  const config = await admin.orgConfig(requireUuid(organizationId));
  const present = config !== null && Object.hasOwn(config, section);
  return { organizationPresent: config !== null, sectionPresent: present,
    matches: present && isDeepStrictEqual(config[section], expected) };
}

export type GatewayKeyState = { present: false } |
  { present: true; id: string; status: string; key_type: string };

/** GET /-/keys/{id}: admin_keys.go:GetKey. Do not emit key_prefix, metadata,
 * raw key, hash or owner label. Only the typed key_not_found 404 means absence.
 */
export async function readGatewayKey(admin: Admin, keyId: string): Promise<GatewayKeyState> {
  const id = requireId(keyId);
  const response = await admin.key(id);
  const value = object(response.body);
  if (response.status === 404 && object(value.error).code === 'key_not_found') return { present: false };
  if (response.status !== 200 || value.id !== id) throw new Error('Unexpected gateway key read result');
  return { present: true, id, status: text(value.status), key_type: text(value.key_type) };
}
