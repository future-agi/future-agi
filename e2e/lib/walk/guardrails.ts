// W18 readers: public writes live in the spec; these are read-only state and
// value-free evidence. Pinned to agentcc models/{org_config,guardrail_policy},
// services/{guardrail_sync,config_push,guardrail_policy_checks} on RLINE-PORT r5.
import { createHash } from 'node:crypto';
import { expect } from '@playwright/test';
import type { StateProbe } from '../state-probe';
import { POLL } from '../state-probe';
import type { GatewayAdmin } from '../gateway-admin';
import type { WalkReceipt, VerdictRow } from './receipt';

export const fingerprint = (value: unknown): string =>
  `sha256:${createHash('sha256').update(JSON.stringify(value) ?? 'undefined').digest('hex')}`;

/** Never turn a returned false into PASS, nor retain assertion diffs/identities. */
export async function checked<T>(receipt: WalkReceipt, feature: string, lane: VerdictRow['lane'],
  action: () => Promise<T>): Promise<T> {
  try {
    const value = await action();
    if (value === false || value === undefined) throw new Error('assertion must return observed evidence');
    receipt.verdict({ feature, lane, where: 'W18', verdict: 'PASS', detail: { observed: fingerprint(value) } });
    return value;
  } catch (error) {
    const locations = [...String((error as Error)?.stack ?? '').matchAll(/\/(e2e\/(?:lib|flows|harness)\/[A-Za-z0-9_./-]+):(\d+):(\d+)/g)]
      .slice(0, 6).map(m => ({ file: m[1], line: Number(m[2]), column: Number(m[3]) }));
    receipt.verdict({ feature, lane, where: 'W18', verdict: 'FAIL',
      detail: { observed: fingerprint(String(error)), locations } });
    throw new Error(`W18 assertion failed: ${feature}`);
  }
}

export interface Check {
  name: string; enabled: boolean | null; action: string; threshold: number;
  config: Record<string, unknown>; mode?: string;
}
export interface Policy {
  id: string; name: string; checks: Check[]; scope: string; mode: string; priority: number; is_active: boolean;
}
export const policyRows = (probe: StateProbe, org: string) => probe.pg<Policy>(
  `SELECT id, name, checks, scope, mode, priority, is_active FROM agentcc_guardrail_policy
   WHERE organization_id=$1 AND deleted=false ORDER BY priority, name`, [org]);
export const activeRows = (probe: StateProbe, org: string) => probe.pg<{ version: number; guardrails: any }>(
  'SELECT version, guardrails FROM agentcc_org_config WHERE organization_id=$1 AND is_active=true AND deleted=false', [org]);

/** Byte-level snapshots include inactive versions, timestamps, and policy blobs. */
export async function storedBytes(probe: StateProbe, org: string) {
  const configs = await probe.pg<{ value: string }>(
    'SELECT row_to_json(t)::text AS value FROM agentcc_org_config t WHERE organization_id=$1 ORDER BY id', [org]);
  const policies = await probe.pg<{ value: string }>(
    'SELECT row_to_json(t)::text AS value FROM agentcc_guardrail_policy t WHERE organization_id=$1 ORDER BY id', [org]);
  return { configs, policies };
}

/** Independently expected policy merge, preserving original list order and last-name-wins. */
export function policySection(policies: Policy[]) {
  const merged = new Map<string, unknown>();
  for (const p of policies) for (const c of p.checks) merged.set(c.name, {
    ...c, _policy: p.id, _policy_name: p.name, _mode: p.mode, _scope: p.scope,
  });
  return { enabled: true, checks: [...merged.values()] };
}

/** The fields Go serializes (tenant.GuardrailCheck); no credentials in this fixture. */
export function gatewaySection(policies: Policy[]) {
  const checks: Record<string, unknown> = {};
  for (const p of policies) for (const c of p.checks) checks[c.name] = {
    enabled: c.enabled === true,
    action: c.action,
    ...(c.threshold ? { confidence_threshold: c.threshold } : {}),
    ...(Object.keys(c.config).length || c.name === 'presidio-pii' ? {
      config: { ...c.config, ...(c.name === 'presidio-pii' ? { provider: 'presidio' } : {}) },
    } : {}),
  };
  // config_push._rebuilt_guardrails always sends parallel; Go omits false fail_open.
  return { pipeline_mode: 'parallel', checks };
}

export async function mirroredPolicies(probe: StateProbe, admin: GatewayAdmin, org: string,
  seed: { id: string; name: string; checks: Check[] }, consoleChecks: Check[]) {
  const rows = await policyRows(probe, org);
  const expected = [{ id: seed.id, name: seed.name, checks: seed.checks, scope: 'global',
    mode: 'enforce', priority: 100, is_active: true }];
  if (consoleChecks.length) {
    // services/guardrail_credentials.py CONSOLE_GUARDRAIL_POLICY. Discover only
    // its minted row ID, not its checks or defaults; all expected fields independent.
    const consoleRow = rows.find(p => p.name === '__ui_guardrails__');
    expect(consoleRow).toBeDefined();
    expected.push({ id: consoleRow!.id, name: '__ui_guardrails__', checks: consoleChecks,
      scope: 'global', mode: 'enforce', priority: 100, is_active: true });
  }
  expected.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
  expect(rows).toEqual(expected);
  const active = await activeRows(probe, org);
  expect(active).toHaveLength(1);
  expect(active[0].guardrails).toEqual(policySection(expected));
  await expect.poll(async () => (await admin.orgConfig(org))?.guardrails, POLL.GATEWAY_SYNC)
    .toEqual(gatewaySection(expected));
  return { rows, active, mirrored: (await admin.orgConfig(org))?.guardrails };
}

export interface Frame { event: string; data: string }
// SSE protocol parser, not a substring-only no-leak assertion (GW-E2E-019).
export function frames(raw: string): Frame[] {
  return raw.replace(/\r\n/g, '\n').split('\n\n').map(block => {
    const lines = block.split('\n');
    return { event: lines.filter(l => l.startsWith('event:')).map(l => l.slice(6).trimStart()).join(''),
      data: lines.filter(l => l.startsWith('data:')).map(l => l.slice(5).trimStart()).join('\n') };
  }).filter(f => f.event || f.data);
}
export const frameKind = (f: Frame) => {
  if (f.data === '[DONE]') return 'done';
  const body = JSON.parse(f.data);
  return body.error ? `error:${body.error.type}/${body.error.code}` : 'chunk';
};
export const frameText = (items: Frame[]) => items.filter(f => frameKind(f) === 'chunk')
  .map(f => JSON.parse(f.data).choices?.[0]?.delta?.content ?? '').join('');

export async function logRows(probe: StateProbe, org: string, ids: string[]) {
  expect(ids.every(Boolean)).toBe(true);
  const read = () => probe.pg<{ request_id: string; status_code: number; guardrail_triggered: boolean;
    metadata: Record<string, unknown>; is_stream: boolean }>(
    `SELECT request_id, status_code, guardrail_triggered, metadata, is_stream FROM agentcc_request_log
     WHERE organization_id=$1 AND request_id=ANY($2::text[])`, [org, ids]);
  await expect.poll(async () => (await read()).map(r => r.request_id).sort(), POLL.ASYNC_JOB).toEqual([...ids].sort());
  const byId = new Map((await read()).map(row => [row.request_id, row]));
  return ids.map(id => byId.get(id)!);
}
