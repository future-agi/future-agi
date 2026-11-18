import { isDeepStrictEqual } from 'node:util';
import { gatewayKeyInventory, gatewayProvidersAbsent } from './typesafe-live';
import type { CleanupProvider, CleanupKey, LimitSnapshot, TypeSafeCleanupIO } from './typesafe-live';

type ObjectValue = Record<string, unknown>;
const object = (v: unknown): v is ObjectValue => !!v && typeof v === 'object' && !Array.isArray(v);
const limits = (v: ObjectValue) => Object.fromEntries(['rate_limiting', 'budgets']
  .filter(field => Object.hasOwn(v, field)).map(field => [field, structuredClone(v[field])]));

// org_config.py creates complete, immutable versions (POST is NOT a patch).
// Preserve every unrelated setting when making a temporary limits version.
const configFields = ['guardrails', 'routing', 'cache', 'rate_limiting', 'budgets', 'cost_tracking', 'ip_acl',
  'alerting', 'privacy', 'tool_policy', 'mcp', 'a2a', 'audit', 'model_database', 'model_map'];

export interface TypeSafeCleanupOptions {
  apiUrl: string; gatewayUrl: string; accessToken: string; organizationId: string; workspaceId: string;
  adminToken: string; signal?: AbortSignal;
  // Body restores, like body config writes, must settle before runner inventory.
  drainWrites?: boolean;
}

// Shared Node-only transport: neither raw admin responses nor configuration
// credentials enter Playwright's step/trace attachments. All errors are data-free.
// Instantiate afresh with the fixture's independent signal for runner teardown.
export function createTypeSafeCleanupIO(options: TypeSafeCleanupOptions): TypeSafeCleanupIO & {
  captureLimits(): Promise<LimitSnapshot>;
  changeLimits(changes: { rate_limiting?: unknown; budgets?: unknown }): Promise<void>;
} {
  const deadline = Date.now() + 120_000;
  const headers = { Authorization: `Bearer ${options.accessToken}`, 'X-Organization-Id': options.organizationId,
    'X-Workspace-Id': options.workspaceId, 'Content-Type': 'application/json' };
  const adminHeaders = { Authorization: `Bearer ${options.adminToken}`, 'Content-Type': 'application/json' };
  const request = async (gateway: boolean, method: string, route: string, body?: unknown, drain = false) => {
    try {
      options.signal?.throwIfAborted();
      // Body config writes retain the live harness's 120s drain budget; cleanup
      // requests keep their existing 10s cap inside the independent 120s slot.
      const timeout = AbortSignal.timeout(Math.max(1, Math.min(drain ? 120_000 : 10_000, deadline - Date.now())));
      const response = await fetch(`${(gateway ? options.gatewayUrl : options.apiUrl).replace(/\/+$/, '')}${route}`, {
        method, headers: gateway ? adminHeaders : headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal: options.signal && !drain ? AbortSignal.any([options.signal, timeout]) : timeout,
      });
      const value: unknown = await response.json();
      options.signal?.throwIfAborted();
      if (!object(value)) throw new Error();
      return { status: response.status, value };
    } catch { throw new Error('TypeSafe cleanup request failed; details withheld'); }
  };
  const list = async <T>(route: string, fields: string[]): Promise<T[]> => {
    const { status, value } = await request(false, 'GET', route);
    const rows = value.result;
    if (status !== 200 || !Array.isArray(rows)
      || !rows.every(row => object(row) && fields.every(field => typeof row[field] === 'string'))) {
      throw new Error('Cleanup inventory unavailable or malformed');
    }
    return rows as T[];
  };
  const active = async () => {
    const { status, value } = await request(false, 'GET', '/agentcc/org-configs/active/');
    // The model always serializes both JSON columns; result:null means no active
    // version. There is no API to deactivate the last version: fail BEFORE a
    // limit write instead of pretending {} restores that absent state.
    const row = value.result;
    if (status !== 200 || !object(row) || typeof row.id !== 'string' || row.organization !== options.organizationId
      || row.is_active !== true || !Object.hasOwn(row, 'rate_limiting') || !Object.hasOwn(row, 'budgets')) {
      throw new Error('Restorable owned org config snapshot unavailable');
    }
    return row;
  };
  const gatewayLimits = async () => {
    const { status, value } = await request(true, 'GET', '/-/orgs/configs');
    if (status !== 200 || !object(value.data)) throw new Error('Gateway config inventory unavailable');
    if (!Object.hasOwn(value.data, options.organizationId)) return {};
    const row = value.data[options.organizationId];
    if (!object(row)) throw new Error('Gateway config inventory malformed');
    for (const field of ['rate_limiting', 'budgets']) {
      if (Object.hasOwn(row, field) && row[field] !== null && !object(row[field])) throw new Error('Gateway limits malformed');
    }
    return limits(row);
  };
  return {
    listProviders: () => list<CleanupProvider>('/agentcc/provider-credentials/', ['id', 'provider_name']),
    listKeys: () => list<CleanupKey>('/agentcc/api-keys/', ['id', 'name', 'status', 'gateway_key_id']),
    listGatewayKeys: async () => {
      const { status, value } = await request(true, 'GET', '/-/keys');
      if (status !== 200) throw new Error('Gateway key inventory unavailable');
      return gatewayKeyInventory(value);
    },
    deleteProvider: async id => {
      const { status, value } = await request(false, 'DELETE', `/agentcc/provider-credentials/${encodeURIComponent(id)}/`);
      if (status !== 200 || !object(value.result) || value.result.deleted !== true) throw new Error('Provider deletion refused');
    },
    revokeKey: async id => {
      const { status, value } = await request(false, 'POST', `/agentcc/api-keys/${encodeURIComponent(id)}/revoke/`);
      if (status !== 200 || !object(value.result) || value.result.status !== 'revoked') throw new Error('Key revocation refused');
    },
    revokeGatewayKey: async id => {
      const { status, value } = await request(true, 'DELETE', `/-/keys/${encodeURIComponent(id)}`);
      if (!(status === 200 && value.status === 'revoked')
        && !(status === 404 && object(value.error) && value.error.code === 'key_not_found')) throw new Error('Gateway key revocation refused');
    },
    gatewayProvidersAbsent: async names => {
      const { status, value } = await request(true, 'GET', '/-/orgs/configs');
      return status === 200 && gatewayProvidersAbsent(value, options.organizationId, names);
    },
    gatewayKeyRevoked: async key => {
      const { status, value } = await request(true, 'GET', `/-/keys/${encodeURIComponent(key.gateway_key_id)}`);
      return (status === 200 && value.status === 'revoked')
        || (status === 404 && object(value.error) && value.error.code === 'key_not_found');
    },
    captureLimits: async () => {
      const row = await active();
      return { backend: { id: row.id as string, ...limits(row) }, gateway: await gatewayLimits() };
    },
    changeLimits: async changes => {
      const row = await active();
      const body = Object.fromEntries(configFields.filter(field => Object.hasOwn(row, field)).map(field => [field, row[field]]));
      const { status, value } = await request(false, 'POST', '/agentcc/org-configs/',
        { ...body, ...changes, change_description: 'e2e TypeSafe temporary limits' }, true);
      if (status !== 200 || !object(value.result) || value.result.gateway_synced !== true) throw new Error('Temporary limits not synchronized');
    },
    restoreLimits: async snapshot => {
      const { status, value } = await request(false, 'POST', `/agentcc/org-configs/${encodeURIComponent(snapshot.backend.id)}/activate/`,
        undefined, options.drainWrites);
      if (status !== 200 || !object(value.result) || value.result.id !== snapshot.backend.id
        || value.result.organization !== options.organizationId || value.result.is_active !== true
        || value.result.gateway_synced === false) throw new Error('Org limit restoration refused');
    },
    backendLimitsRestored: async snapshot => {
      const row = await active();
      return row.id === snapshot.backend.id && isDeepStrictEqual(limits(row), limits(snapshot.backend));
    },
    gatewayLimitsRestored: async snapshot => isDeepStrictEqual(await gatewayLimits(), snapshot.gateway),
    waitUntil: async check => {
      do {
        options.signal?.throwIfAborted();
        if (await check()) return;
        if (Date.now() >= deadline) break;
        await new Promise(resolve => setTimeout(resolve, Math.min(500, deadline - Date.now())));
      } while (Date.now() < deadline);
      throw new Error('Cleanup verification timed out');
    },
  };
}
