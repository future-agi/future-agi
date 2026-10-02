// localStorage adapter for Request Logs column preferences (R13-R16).
//
// One key per authenticated user + organization + view. The adapter never
// enumerates keys, never falls back to another identity's key and never
// throws: every failure is reported as `{ ok: false, reason }`.

import { validateConfig } from "./columnModel";

export const STORAGE_PREFIX = "agentcc.requestLogs.columns.v1";

function nonEmptyString(value) {
  return typeof value === "string" && value.length > 0;
}

export function buildKey(userId, orgId, viewId) {
  if (
    !nonEmptyString(userId) ||
    !nonEmptyString(orgId) ||
    !nonEmptyString(viewId)
  ) {
    return null;
  }
  return `${STORAGE_PREFIX}:${userId}:${orgId}:${viewId}`;
}

function getStorage() {
  try {
    // `globalThis.localStorage` is `window.localStorage` in browsers; reading it
    // can throw (SecurityError) when storage is blocked.
    const storage = globalThis.localStorage;
    return storage || null;
  } catch {
    return null;
  }
}

/**
 * @returns {{ ok: boolean, config: object|null, reason?: string }}
 *   `config` is null for an absent or rejected record; a rejected record is
 *   left untouched in storage (AC12).
 */
export function readConfig(key) {
  if (!key) return { ok: false, config: null, reason: "no-key" };
  const storage = getStorage();
  if (!storage) return { ok: false, config: null, reason: "read-failed" };

  let raw;
  try {
    raw = storage.getItem(key);
  } catch {
    return { ok: false, config: null, reason: "read-failed" };
  }
  if (raw === null || raw === undefined) return { ok: true, config: null };
  if (typeof raw !== "string") {
    return { ok: false, config: null, reason: "invalid" };
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, config: null, reason: "invalid" };
  }
  const config = validateConfig(parsed);
  if (!config) return { ok: false, config: null, reason: "invalid" };
  return { ok: true, config };
}

export function writeConfig(key, config) {
  if (!key) return { ok: false, reason: "no-key" };
  const normalized = validateConfig(config);
  if (!normalized) return { ok: false, reason: "invalid" };
  const storage = getStorage();
  if (!storage) return { ok: false, reason: "write-failed" };
  try {
    storage.setItem(key, JSON.stringify(normalized));
    return { ok: true };
  } catch {
    return { ok: false, reason: "write-failed" };
  }
}

export function removeConfig(key) {
  if (!key) return { ok: false, reason: "no-key" };
  const storage = getStorage();
  if (!storage) return { ok: false, reason: "remove-failed" };
  try {
    storage.removeItem(key);
    return { ok: true };
  } catch {
    return { ok: false, reason: "remove-failed" };
  }
}
