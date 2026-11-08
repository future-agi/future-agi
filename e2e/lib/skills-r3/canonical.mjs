import { createHash } from 'node:crypto';

/** Harness identity only. Product digests must still be checked by independent oracles. */
export function canonical(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number' && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && Object.getPrototypeOf(value) === Object.prototype) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  throw new TypeError('Only finite JSON values are accepted');
}
export const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
export const digest = (value) => sha256(canonical(value));
export function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}
export function exactSet(actual, expected, label = 'set') {
  requireValue(Array.isArray(actual) && Array.isArray(expected), `${label}: arrays required`);
  requireValue(new Set(actual).size === actual.length, `${label}: duplicate actual member`);
  requireValue(new Set(expected).size === expected.length, `${label}: duplicate expected member`);
  requireValue(canonical([...actual].sort()) === canonical([...expected].sort()), `${label}: membership differs`);
}
export const SHA256 = /^[a-f0-9]{64}$/;
export const COMMIT = /^[a-f0-9]{40}$/;
export function token(value, field) {
  requireValue(typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,159}$/.test(value)
    && !/^(?:sk-|gh[pousr]_|xox[abprs]-|eyJ)/.test(value), `${field}: use an opaque identifier, not payload text`);
  return value;
}
