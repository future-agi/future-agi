import { hasCredentialUrl } from "./auth-url-rules";
import { redactEmbedSecrets, scrubEmbedSecrets } from "./guard";

const cleanProperties = (raw) => {
  const value = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Invalid Mixpanel persistence");
  }
  const clean = scrubEmbedSecrets(value);
  return clean === value ? raw : JSON.stringify(clean);
};

const containsCredential = (value) => {
  const pending = [value];
  while (pending.length) {
    const item = pending.pop();
    if (typeof item === "string") {
      if (hasCredentialUrl(item) || redactEmbedSecrets(item) !== item)
        return true;
    } else if (item && typeof item === "object") {
      for (const key of Object.keys(item)) pending.push(key, item[key]);
    }
  }
  return false;
};

const cleanQueue = (raw) => {
  const value = JSON.parse(raw);
  if (
    !Array.isArray(value) ||
    value.some(
      (item) =>
        !item ||
        typeof item !== "object" ||
        Array.isArray(item) ||
        typeof item.id !== "string" ||
        !item.id ||
        !item.payload ||
        typeof item.payload !== "object",
    )
  ) {
    throw new Error("Invalid Mixpanel queue");
  }
  const clean = scrubEmbedSecrets(value);
  // Auth-route detection deliberately also matches redacted URLs. Drop these
  // old entries conservatively: orphaned items bypass before_send in 2.53.0.
  // Other entries keep their queue IDs, deadlines and unrelated properties.
  const retained = clean.filter((item) => !containsCredential(item));
  return clean === value && retained.length === value.length
    ? raw
    : JSON.stringify(retained);
};

const cleanStorageEntry = (storage, key, scrub) => {
  const raw = storage.getItem(key);
  if (raw === null) return;
  const clean = scrub(raw);
  if (clean === raw) return;
  storage.setItem(key, clean);
  if (storage.getItem(key) !== clean) {
    throw new Error("Mixpanel storage cleanup failed");
  }
};

const projectCookies = (name) =>
  document.cookie.split(";").flatMap((entry) => {
    const boundary = entry.indexOf("=");
    return boundary > 0 && entry.slice(0, boundary).trim() === name
      ? [decodeURIComponent(entry.slice(boundary + 1))]
      : [];
  });

const cleanCookies = (storage, name) => {
  const cookies = projectCookies(name);
  if (!cookies.length) return;
  const cleaned = cookies.map(cleanProperties);
  // Domain is not exposed by document.cookie. If distinct copies coexist,
  // don't overwrite one identity with another: leave the SDK disabled instead.
  if (new Set(cleaned.map((raw) => JSON.stringify(JSON.parse(raw)))).size > 1) {
    throw new Error("Ambiguous Mixpanel cookies");
  }
  // Migrate before init, retaining every existing localStorage key (including
  // falsy values). Never plant persistent cookies at scopes the SDK won't
  // remove on reset. If storage is unsupported or the write doesn't stick,
  // fail closed instead of rewriting a cookie fallback ourselves.
  const existing = JSON.parse(cleanProperties(storage.getItem(name) ?? "{}"));
  const merged = JSON.stringify({ ...JSON.parse(cleaned[0]), ...existing });
  storage.setItem(name, merged);
  if (storage.getItem(name) !== merged) {
    throw new Error("Mixpanel cookie migration failed");
  }
  const labels = window.location.hostname.split(".");
  const domains = [""];
  for (let index = 0; index < labels.length; index += 1) {
    domains.push(`; Domain=${labels.slice(index).join(".")}`);
  }
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  // Include already-clean copies left by older bundles: upgrade() in 2.53.0
  // removes only host-only and .extract_domain, not intermediate parents.
  // Expire all possible Path=/ scopes; never copy the identity into them.
  for (const domain of domains) {
    document.cookie = `${name}=; Path=/; SameSite=Lax; Max-Age=0${domain}${secure}`;
  }
  if (projectCookies(name).length) {
    throw new Error("Mixpanel cookie cleanup failed");
  }
};

// Run synchronously BEFORE init: batchers autostart before the persistence
// constructor, and that constructor reloads/re-saves superproperties. Inspect
// only this project's SDK keys, never unrelated application state or lock keys.
// A blocked read/write or malformed blob disables this SDK, without logging
// values or deleting unrelated identity data to recover analytics.
export function cleanStoredMixpanelPersistence(token) {
  if (typeof window === "undefined") return false;
  try {
    const storage = window.localStorage;
    const name = `mp_${token}_mixpanel`;
    cleanStorageEntry(storage, name, cleanProperties);
    for (const suffix of ["ev", "pp", "gr"]) {
      cleanStorageEntry(storage, `__mpq_${token}_${suffix}`, cleanQueue);
    }
    cleanCookies(storage, name);
    return true;
  } catch {
    return false;
  }
}
