// H-8 (HO S3): key scopes. A key is either Full access (no scopes: it acts
// as the person who made it) or Ingestion only (`scopes: ["ingest"]`: it
// sends traces and logs and cannot read or change datasets; the dataset
// guard refuses it with "This API key can't read datasets."). The backend
// has no key scopes yet, so the Keys page shows them only in prototype
// mode. Pure.
import { isPrototypeMode } from "src/utils/prototype";

/** The scope names a key can carry. */
export const KEY_SCOPES = Object.freeze(["ingest"]);

/** The Access choices of Create key, in order (Full access first). */
export const KEY_ACCESS = Object.freeze([
  Object.freeze({
    value: "full",
    label: "Full access",
    help: null,
    scopes: null,
  }),
  Object.freeze({
    value: "ingest",
    label: "Ingestion only",
    help: "Sends traces and logs. Can't read or change datasets.",
    scopes: Object.freeze(["ingest"]),
  }),
]);

export const DEFAULT_KEY_ACCESS = KEY_ACCESS[0].value;

/** The create body's `scopes` for an Access choice (absent for Full access). */
export function scopesOfAccess(value) {
  const choice = KEY_ACCESS.find((a) => a.value === value);
  return choice?.scopes ? [...choice.scopes] : null;
}

/** The create body: `{key_name}` plus `scopes` only for a scoped key. */
export function createKeyBody(keyName, access = DEFAULT_KEY_ACCESS) {
  const scopes = scopesOfAccess(access);
  return { key_name: keyName, ...(scopes ? { scopes } : {}) };
}

/** The Access column's words for a key's `scopes`. */
export function keyAccessLabel(scopes) {
  if (!Array.isArray(scopes) || !scopes.length) return KEY_ACCESS[0].label;
  const match = KEY_ACCESS.find(
    (a) =>
      a.scopes &&
      a.scopes.length === scopes.length &&
      a.scopes.every((s) => scopes.includes(s)),
  );
  return match ? match.label : scopes.join(", ");
}

/**
 * Pure: `scopes` from a create body, or `{error}` for a scope this server
 * does not know. Absent or empty = Full access (null).
 */
export function parseKeyScopes(raw) {
  if (raw == null) return { scopes: null };
  if (!Array.isArray(raw)) return { error: "scopes must be a list." };
  const scopes = [...new Set(raw.map(String))];
  const unknown = scopes.filter((s) => !KEY_SCOPES.includes(s));
  if (unknown.length)
    return { error: `Unknown key scope: ${unknown.join(", ")}` };
  return { scopes: scopes.length ? scopes : null };
}

/** Whether the Keys page shows key scopes (prototype mode only). */
export const keyScopesShown = () => isPrototypeMode();
