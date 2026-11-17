// The personal API key a code panel puts in its snippets, held in this tab's
// memory only.
//
// Future AGI shows a user key's secret once, in the reply that creates it (the
// database keeps a keyed hash, accounts/views/keys.py), so a panel cannot read
// an existing key back: it creates one when the person asks, keeps it here
// and never writes it anywhere else. Not in localStorage, sessionStorage, a
// URL, a query cache or a log line (the rule for secrets
// in the UI).
//
// The key belongs to the person who created it and the organization they made
// it in, and to nobody else (P11 fix round 2). A panel shows it only to that
// same person in that same organization, and shows nothing when either is
// unknown. It leaves the tab when they forget it, when they sign out (the auth
// provider's logout calls forgetKey, as it resets its other per-user state),
// the moment any panel sees another person or nobody signed in, or when the
// tab reloads. A key is never held for an unknown person or organization.
//
// Only a personal key works through the Omega session exchange.

import { useEffect, useSyncExternalStore } from "react";

import axios, { endpoints } from "src/utils/axios";

import { isUsableKey } from "./curl";

let held = null;
const listeners = new Set();

function emit() {
  for (const listener of listeners) listener();
}

const known = (value) =>
  value === undefined || value === null || value === "" ? null : String(value);

export function heldKey() {
  return held;
}

/**
 * Keep a newly created key for this tab, as `userId`'s key in
 * `organizationId`. Anything malformed, or a key for an unknown person or
 * organization, is refused, not kept.
 */
export function holdKey({
  keyId,
  name,
  apiKey,
  secretKey,
  organizationId,
  userId,
}) {
  const next = Object.freeze({
    keyId: keyId ?? null,
    name: name ?? null,
    apiKey,
    secretKey,
    organizationId: known(organizationId),
    userId: known(userId),
  });
  if (!isUsableKey(next)) throw new TypeError("Not a usable API key pair.");
  if (!next.userId || !next.organizationId)
    throw new TypeError(
      "A key is held only for a known person and organization.",
    );
  held = next;
  emit();
  return next;
}

/** Drop the key from this tab. It stays valid in Settings > API keys until disabled there. */
export function forgetKey() {
  if (held === null) return;
  held = null;
  emit();
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * `key` when it is `userId`'s key in `organizationId`; null otherwise, and
 * null whenever the key, the person or the organization is unknown.
 */
export function keyFor(key, { userId, organizationId } = {}) {
  const user = known(userId);
  const organization = known(organizationId);
  if (!key || !user || !organization) return null;
  if (key.userId !== user || key.organizationId !== organization) return null;
  return key;
}

/**
 * The held key when the person signed in (`userId`) made it in the
 * organization this page shows; else null. A key held for anyone other than
 * the person signed in now (another person, or nobody) is dropped from the tab.
 */
export function useHeldKey({ organizationId, userId } = {}) {
  const key = useSyncExternalStore(subscribe, heldKey, heldKey);
  const notTheirs = key !== null && key.userId !== known(userId);
  useEffect(() => {
    if (notTheirs) forgetKey();
  }, [notTheirs]);
  return keyFor(key, { userId, organizationId });
}

const pad = (n) => String(n).padStart(2, "0");

/** "Code snippets 2026-10-22 10:41:07": unique enough, and says where it came from. */
export function panelKeyName(now = new Date()) {
  return `Code snippets ${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}

/**
 * Create a personal API key for the person signed in (`userId`), in the
 * selected organization, and hold it as theirs. Refuses before any request
 * when either is unknown, so no key is made that no panel would show. Rejects
 * with the request's error; the error is never logged here (its config
 * carries no secret, its reply would).
 */
export async function createPanelKey({
  organizationId = null,
  userId = null,
  post = (url, body) => axios.post(url, body),
  now = new Date(),
} = {}) {
  if (!known(organizationId) || !known(userId))
    throw new TypeError(
      "No key is made for an unknown person or organization.",
    );
  const name = panelKeyName(now);
  const response = await post(endpoints.keys.generateSecretKey, {
    key_name: name,
  });
  const result = response?.data?.result ?? {};
  return holdKey({
    keyId: result.key_id ?? result.keyId,
    name: result.key_name ?? result.keyName ?? name,
    apiKey: result.api_key ?? result.apiKey,
    secretKey: result.secret_key ?? result.secretKey,
    organizationId,
    userId,
  });
}
