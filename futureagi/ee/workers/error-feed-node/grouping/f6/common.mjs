import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

export const canonical = value => JSON.stringify(value, (_key, x) => x && typeof x === 'object' && !Array.isArray(x)
  ? Object.fromEntries(Object.keys(x).sort().map(k => [k, x[k]])) : x);
export const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
export const bytesDigest = value => createHash('sha256').update(value).digest('hex');
export const unique = values => [...new Set(values)].sort();
export function requireText(value, name) { assert.ok(typeof value === 'string' && value.trim(), name); return value; }
export function exactKeys(value, keys) {
  assert.ok(value && typeof value === 'object' && !Array.isArray(value));
  assert.deepEqual(Object.keys(value).sort(), [...keys].sort(), 'Unexpected/missing fields');
}
export const pairKey = (a, b) => canonical([a, b].sort());
export const scopeKey = r => canonical([r.organization_id, r.project_id]);
export const sameScope = (a, b) => scopeKey(a) === scopeKey(b);
export function pairSafety(a, b, constraints) {
  assert.ok(a && b, 'Unknown occurrence');
  if (!sameScope(a, b)) return 'hard_scope';
  if (constraints.has(pairKey(a.id, b.id))) return 'cannot_link';
  if (a.control || b.control) return 'healthy_control';
  return null;
}
export function validateMembers(ids, byId, constraints) {
  assert.ok(ids.length && unique(ids).length === ids.length, 'Empty/duplicate membership');
  for (let i = 0; i < ids.length; i++) {
    assert.ok(byId.has(ids[i]) && !byId.get(ids[i]).control, 'Unknown/control member');
    for (let j = 0; j < i; j++) assert.equal(pairSafety(byId.get(ids[i]), byId.get(ids[j]), constraints), null, 'Unsafe membership');
  }
}
export const VIEWS = ['task', 'state', 'tools', 'artifact', 'interaction', 'outcome', 'provider', 'semantics'];
export function cosine(a, b) {
  assert.ok(a.length === b.length && a.length, 'Vector dimensions differ');
  const dot = a.reduce((n, v, i) => n + v * b[i], 0);
  const norm = Math.hypot(...a) * Math.hypot(...b);
  return norm ? Math.max(-1, Math.min(1, dot / norm)) : 0;
}
export function validateVector(v, dimension) {
  assert.ok(Array.isArray(v) && v.length === dimension && v.every(Number.isFinite) && Math.hypot(...v) > 0, 'Invalid embedding');
  return v;
}
