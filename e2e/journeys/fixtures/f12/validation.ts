// Plain decoded data only; fixed errors never retain rejected values.
export function fail(code = 'INPUT'): never { throw new Error(`F12-${code}`); }
export function record(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail();
  if (![Object.prototype, null].includes(Object.getPrototypeOf(value))) return fail();
  const own = Reflect.ownKeys(value);
  if (own.length !== keys.length || !keys.every(k => own.includes(k))) return fail();
  for (const k of keys) if (!Object.hasOwn(Object.getOwnPropertyDescriptor(value, k)!, 'value')) return fail();
  return value as Record<string, unknown>;
}
export function list(value: unknown, max = 10000): unknown[] {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype || value.length > max) return fail();
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
  if (typeof value !== 'string' || value.length > 20 || !/^(0|[1-9][0-9]*|-[1-9][0-9]*)$/.test(value)) return fail('AMOUNT');
  const n = BigInt(value);
  if (n < (nonnegative ? 0n : -9223372036854775808n) || n > 9223372036854775807n) return fail('AMOUNT');
  return n;
}
export function cents(value: unknown): bigint {
  const n = integer(value, true);
  if (n < 10000n || n > 99999999990000n || n % 10000n !== 0n) return fail('AMOUNT');
  return n;
}
export function usd(value: unknown): string {
  const n = integer(value, true);
  if (n > 1000000000000000n) return fail('AMOUNT');
  return `${n / 1000000n}.${String(n % 1000000n).padStart(6, '0')}`;
}
export function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) return fail('TIME');
  return value;
}
export function timestamp(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$/.test(value)) return fail('TIME');
  const ms = value.slice(0, 23) + 'Z'; const d = new Date(ms);
  if (!Number.isFinite(d.getTime()) || d.toISOString() !== ms || value.startsWith('0000')) return fail('TIME');
  return value;
}
export function loopback(value: unknown): string {
  if (typeof value !== 'string' || !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::[1-9][0-9]{0,4})?$/.test(value)) return fail('ORIGIN');
  try { return new URL(value).origin; } catch { return fail('ORIGIN'); }
}
export function localPath(value: unknown): string {
  if (typeof value !== 'string' || value.length > 512 || !/^\/[a-zA-Z0-9_/-]*$/.test(value) || value.includes('//')) return fail('PATH');
  return value;
}
export function schema(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-z_][a-z0-9_]{0,62}$/.test(value)) return fail('SCHEMA');
  return value;
}
export function exactSet(actual: readonly string[], expected: readonly string[]): void {
  if (new Set(actual).size !== actual.length || new Set(expected).size !== expected.length
    || actual.length !== expected.length || actual.some(id => !expected.includes(id))) fail('COHORT');
}
export const ORACLE_ONLY = 'oracle-only-not-financial-acceptance' as const;
