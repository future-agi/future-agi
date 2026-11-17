// Deliberately data-only: never echo rejected values into an error or receipt.
export function record(value: unknown, keys: readonly string[], code: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(code);
  const proto = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) throw new Error(code);
  const own = Reflect.ownKeys(value);
  if (own.length !== keys.length || !keys.every(key => own.includes(key))) throw new Error(code);
  for (const key of keys) {
    if (!Object.hasOwn(Object.getOwnPropertyDescriptor(value, key)!, 'value')) throw new Error(code);
  }
  return value as Record<string, unknown>;
}

export function isUuid(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
    && value !== '00000000-0000-0000-0000-000000000000';
}

export function loopbackOrigin(value: unknown): string {
  // Check the literal before URL parsing: URL canonicalizes numeric host aliases.
  if (typeof value !== 'string' || !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::[1-9][0-9]{0,4})?$/.test(value))
    throw new Error('F09-INPUT');
  try { return new URL(value).origin; } catch { throw new Error('F09-INPUT'); }
}
