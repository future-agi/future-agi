// Data-only boundary. Fixed error codes; never include rejected values.
export function fail(code = 'INPUT'): never { throw new Error(`F05-${code}`); }

export function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail();
  if (![Object.prototype, null].includes(Object.getPrototypeOf(value))) return fail();
  const own = Reflect.ownKeys(value);
  if (own.length !== keys.length || !keys.every(key => own.includes(key))) return fail();
  for (const key of keys) {
    if (!Object.hasOwn(Object.getOwnPropertyDescriptor(value, key)!, 'value')) return fail();
  }
  return value as Record<string, unknown>;
}

export function list(value: unknown, max = 10000): unknown[] {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype || value.length > max) return fail();
  // Reject holes, extra properties and getters, including on numeric slots.
  if (Reflect.ownKeys(value).length !== value.length + 1) return fail();
  for (let i = 0; i < value.length; i++) {
    const d = Object.getOwnPropertyDescriptor(value, String(i));
    if (!d || !Object.hasOwn(d, 'value')) return fail();
  }
  return value;
}

export function uuid(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
    || value === '00000000-0000-0000-0000-000000000000') return fail();
  return value;
}

export function integer(value: unknown, nonnegative = false): bigint {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]*|-[1-9][0-9]*)$/.test(value) || value.length > 20) return fail('AMOUNT');
  const n = BigInt(value);
  if (n < (nonnegative ? 0n : -9223372036854775808n) || n > 9223372036854775807n) return fail('AMOUNT');
  return n;
}

export function timestamp(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$/.test(value)) return fail('TIME');
  const ms = value.slice(0, 23) + 'Z';
  const date = new Date(ms);
  if (!Number.isFinite(date.getTime()) || date.toISOString() !== ms || value.startsWith('0000')) return fail('TIME');
  // Return original microsecond precision, not Date's truncated milliseconds.
  return value;
}

export function loopback(value: unknown): string {
  if (typeof value !== 'string' || !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::[1-9][0-9]{0,4})?$/.test(value)) return fail('ORIGIN');
  try { return new URL(value).origin; } catch { return fail('ORIGIN'); }
}

export function model(value: unknown): string {
  // Captures here contain only test-owned labels. Not a production model-ID validator.
  if (typeof value !== 'string' || !/^e2e-f05-[a-z0-9-]{1,64}$/.test(value)) return fail();
  return value;
}
