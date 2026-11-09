import { redactEmbedSecrets, scrubEmbedSecrets } from "./guard";

// 1.360.2 parseName(): ph_<escaped project key>_posthog. Session persistence
// uses the same name in sessionStorage. Never inspect unrelated application
// storage, and never remove IDs, consent, flags, or other PostHog properties.
const isPosthogStore = (name) => /^ph_.+_posthog$/.test(name);
const cleanBlob = (raw) => {
  const value = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Invalid PostHog persistence");
  }
  const clean = scrubEmbedSecrets(value);
  return clean === value ? raw : JSON.stringify(clean);
};

const cleanStorage = (storage) => {
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (!isPosthogStore(key)) continue;
    const raw = storage.getItem(key);
    if (raw === null) continue;
    const clean = cleanBlob(raw);
    if (clean !== raw) {
      storage.setItem(key, clean);
      if (storage.getItem(key) !== clean)
        throw new Error("PostHog cleanup failed");
    }
  }
};

const posthogCookies = () =>
  document.cookie.split(";").flatMap((entry) => {
    const boundary = entry.indexOf("=");
    const name = entry.slice(0, boundary).trim();
    return boundary > 0 && isPosthogStore(name)
      ? [{ name, raw: decodeURIComponent(entry.slice(boundary + 1)) }]
      : [];
  });

const cleanCookies = () => {
  // document.cookie cannot reveal Domain. Rewrite visible PostHog cookies at
  // host-only and ancestor scopes (including .futureagi.com), retaining their
  // complete data. Match SDK Path=/, SameSite=Lax, secure and default lifetime.
  const labels = window.location.hostname.split(".");
  const domains = [""];
  for (let index = 0; index < labels.length - 1; index += 1) {
    domains.push(`; Domain=${labels.slice(index).join(".")}`);
  }
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  for (const { name, raw } of posthogCookies()) {
    const clean = cleanBlob(raw);
    if (clean === raw) continue;
    for (const domain of domains) {
      document.cookie = `${name}=${encodeURIComponent(clean)}; Path=/; SameSite=Lax; Max-Age=31536000${domain}${secure}`;
    }
  }
  // Fail closed if the browser would not overwrite a visible dirty copy.
  if (posthogCookies().some(({ raw }) => cleanBlob(raw) !== raw)) {
    throw new Error("PostHog cookie cleanup failed");
  }
};

// Must run BEFORE init: the persistence constructor saves immediately, and
// flags requests bypass before_send. On inaccessible/malformed stored data,
// leave it intact and skip this SDK rather than sending an uninspected copy.
export function cleanStoredPosthogPersistence() {
  if (typeof window === "undefined") return false;
  try {
    cleanStorage(window.localStorage);
    cleanStorage(window.sessionStorage);
    cleanCookies();
    return true;
  } catch {
    return false;
  }
}

// Normalize each property to JSON data before walking it. URL/class/toJSON
// values otherwise materialize credentials only when storage or /flags encodes
// them. Detach aliases as well. Reject only unserializable properties, retaining
// unrelated IDs, consent and flags; never leave a rejected value in memory.
const scrubJsonProperties = (properties) => {
  if (
    !properties ||
    typeof properties !== "object" ||
    Array.isArray(properties)
  ) {
    throw new Error("Invalid PostHog properties");
  }
  return scrubEmbedSecrets(
    Object.fromEntries(
      Object.keys(properties).flatMap((key) => {
        try {
          const serialized = JSON.stringify(properties[key], (_name, value) =>
            typeof value === "bigint" ? value.toString() : value,
          );
          return serialized === undefined
            ? []
            : [[key, JSON.parse(serialized)]];
        } catch {
          return [];
        }
      }),
    ),
  );
};

const guardedPersistence = new WeakSet();
const guardedClients = new WeakSet();

const guardFlagsRequests = (ph) => {
  if (guardedClients.has(ph) || typeof ph._send_request !== "function") return;
  const send = ph._send_request;
  ph._send_request = function sendSanitizedFlags(request) {
    if (/\/flags\/(?:\?|$)/.test(request.url)) {
      try {
        request = { ...request, data: scrubJsonProperties(request.data) };
      } catch {
        return undefined;
      }
    }
    return send.call(this, request);
  };
  guardedClients.add(ph);
};

// In 1.360.2 both SessionPropsManager and feature-flag overrides register and
// save BEFORE before_send. Intercept each instance's final save boundary, not
// just the loaded snapshot or a session callback (which runs AFTER a raw save).
// reset() clears props but retains these instances, so this also covers every
// later session, direct flags setters and persistence backend migrations.
const guardPersistenceWrites = (persistence) => {
  if (guardedPersistence.has(persistence)) return;
  const save = persistence.save;
  persistence.save = function saveSanitizedProperties() {
    this.props = scrubJsonProperties(this.props);
    return save.call(this);
  };
  guardedPersistence.add(persistence);
};

// loaded precedes the initial pageview and flags loading. Keep automatic
// referrer/campaign writers off, seed clean initial attribution, and guard the
// independent session-source and person/group-property writers on every save.
export function preparePosthogPersistence(ph) {
  // /flags bypasses before_send. Recheck its independent wire boundary even
  // when a caller mutates a retained property without triggering save().
  guardFlagsRequests(ph);
  for (const persistence of new Set([ph.persistence, ph.sessionPersistence])) {
    if (!persistence) continue;
    guardPersistenceWrites(persistence);
    persistence.props = scrubJsonProperties(persistence.props);
    if (
      persistence === ph.persistence &&
      !persistence.props.$initial_person_info
    ) {
      persistence.props = {
        ...persistence.props,
        $initial_person_info: {
          u: redactEmbedSecrets(window.location.href),
          r: redactEmbedSecrets(document.referrer || "$direct"),
        },
      };
    }
    persistence.save();
  }
}
