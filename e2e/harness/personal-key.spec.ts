import { test, expect } from '@playwright/test';
import { createPersonalKey } from '../lib/personal-key';
import type { ApiClient } from '../lib/api-client';
import type { APIRequestContext } from '@playwright/test';
import { provisionActor } from '../lib/provisioning';
import { mintedValues } from '../lib/minted-values';

// Offline contract: the shared credential registry is called before the issuer returns.
test('offline: personal-key issuance registers every credential form', async () => {
  const previous = process.env.E2E_VALUE_FREE;
  const holder = globalThis as unknown as { __futureagiE2eValueFree?: unknown };
  const before = holder.__futureagiE2eValueFree;
  const registered = new Set<string>();
  process.env.E2E_VALUE_FREE = '1';
  holder.__futureagiE2eValueFree = { registerAs: (_kind: string, ...values: string[]) => values.forEach(value => registered.add(value)) };
  const key = { key_id: 'synthetic-key-id', key_name: 'synthetic-key-name',
    api_key: 'fixture-personal-public-value', secret_key: 'fixture-personal-secret-value',
    masked_api_key: 'fixt**********alue', masked_secret_key: 'fixt**********alue' };
  const calls: { path: string; data: unknown }[] = [];
  const api = { post: async (path: string, data: unknown) => {
    calls.push({ path, data }); return { status: true, result: key };
  } } as unknown as ApiClient;
  try {
    const result = await createPersonalKey(api, key.key_name);
    expect(calls).toEqual([{ path: '/accounts/key/generate_secret_key/', data: { key_name: key.key_name } }]);
    expect(result).toBe(key);
    for (const value of [key.api_key, key.secret_key, key.masked_api_key, key.masked_secret_key,
      key.api_key.slice(0, 8), key.secret_key.slice(0, 8), Buffer.from(`${key.api_key}:${key.secret_key}`).toString('base64')]) {
      expect(registered.has(value)).toBe(true);
    }
    delete holder.__futureagiE2eValueFree;
    await expect(createPersonalKey(api, 'refused')).rejects.toThrow('initialized value-free registry');
    expect(calls).toHaveLength(1);
  } finally {
    holder.__futureagiE2eValueFree = before;
    if (previous === undefined) delete process.env.E2E_VALUE_FREE; else process.env.E2E_VALUE_FREE = previous;
  }
});

// HR01/708J union: the third argument still runs before personal-key issuance;
// the fourth keeps the value-free actor set's disjoint signup domain.
test('offline: provisioning composes the personal-key callback, domain and hygiene registry', async () => {
  const previous = process.env.E2E_VALUE_FREE;
  delete process.env.E2E_VALUE_FREE;
  const calls: string[] = [];
  let signup: { email: string; password: string } | undefined;
  const tokens = { access: 'union-fixture-access', refresh: 'union-fixture-refresh' };
  const key = { api_key: 'union-fixture-personal-public', secret_key: 'union-fixture-personal-secret' };
  const req = {
    post: async (url: string, options: { data: unknown }) => {
      const path = new URL(url).pathname;
      calls.push(path);
      if (path === '/accounts/signup/') signup = options.data as typeof signup;
      const body = path === '/accounts/token/' ? tokens
        : path === '/accounts/key/generate_secret_key/' ? { status: true, result: key } : {};
      return { status: () => 200, text: async () => JSON.stringify(body) };
    },
    get: async (url: string) => {
      const path = new URL(url).pathname;
      calls.push(path);
      expect(path).toBe('/accounts/user-info/');
      return { status: () => 200, text: async () => JSON.stringify({ organization: { id: 'union-org' }, default_workspace_id: 'union-workspace' }) };
    },
  } as unknown as APIRequestContext;
  try {
    const actor = await provisionActor(req, 'union', async identity => {
      calls.push('before-personal-key');
      expect(identity.email.endsWith('@union-disjoint.test')).toBe(true);
      expect(identity).not.toHaveProperty('apiKey');
    }, 'union-disjoint.test');
    expect(calls).toEqual(['/accounts/signup/', '/accounts/token/', '/accounts/user-info/',
      'before-personal-key', '/accounts/key/generate_secret_key/']);
    expect(signup?.email).toBe(actor.email);
    expect(actor.apiKey).toBe(key.api_key);
    expect(actor.secretKey).toBe(key.secret_key);
    for (const value of [actor.password, tokens.access, tokens.refresh, key.api_key, key.secret_key]) {
      expect(mintedValues()).toContain(value);
    }
  } finally {
    if (previous === undefined) delete process.env.E2E_VALUE_FREE; else process.env.E2E_VALUE_FREE = previous;
  }
});
