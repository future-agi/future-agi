import type { ApiClient } from './api-client';

export interface PersonalKey {
  key_id: string; key_name: string; api_key: string; secret_key: string;
  masked_api_key: string; masked_secret_key: string;
}

// Share the process-global instance initialized by the value-free harness.
// That harness is composed independently. Do not create a second registry or
// silently continue a value-free run when its bootstrap has not loaded.
interface ValueRegistry {
  registerAs(what: string, ...values: string[]): unknown;
}
function registry(): ValueRegistry | undefined {
  if (process.env.E2E_VALUE_FREE !== '1') return undefined;
  const holder = globalThis as unknown as { __futureagiE2eValueFree?: ValueRegistry };
  const value = holder.__futureagiE2eValueFree;
  if (!value) throw new Error('Personal-key provisioning requires the initialized value-free registry');
  return value;
}

/** Explicitly issue this signed-in actor's own key; never fetch an organization pair. */
export async function createPersonalKey(api: ApiClient, name: string): Promise<PersonalKey> {
  const values = registry(); // fail before issuing anything if publication cannot be safe
  const response = await api.post<{ status: boolean; result: PersonalKey }>(
    '/accounts/key/generate_secret_key/', { key_name: name });
  const key = response.result;
  // Register before a caller can assert, attach, log or return these values.
  // Includes raw, HTTP Basic, URL/JSON-escaped and displayed forms.
  values?.registerAs('credential', key.api_key, key.secret_key,
    Buffer.from(`${key.api_key}:${key.secret_key}`).toString('base64'),
    encodeURIComponent(key.api_key), encodeURIComponent(key.secret_key),
    JSON.stringify(key.api_key).slice(1, -1), JSON.stringify(key.secret_key).slice(1, -1),
    key.api_key.slice(0, 8), key.secret_key.slice(0, 8), key.masked_api_key, key.masked_secret_key);
  return key;
}
