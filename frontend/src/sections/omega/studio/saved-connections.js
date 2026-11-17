// Studio servers this browser has connected to: a URL and a label each,
// nothing else. Keys never reach storage: every entry is rebuilt from its URL
// and label before it is written, so a stray field cannot ride along.
//
// localStorage  omega.studio.connections  [{url, label}], newest first
// sessionStorage omega.studio.active      the server this tab is showing (URL
//               only), so a reload frames it again. The child's memory-only
//               key is gone: needs-key asks for a freshly opened/pasted link.
import { normalizeStudioServer, serverDisplayName } from "./connect-link";

export const SAVED_CONNECTIONS_KEY = "omega.studio.connections";
export const ACTIVE_CONNECTION_KEY = "omega.studio.active";
export const SAVED_CONNECTIONS_EVENT = "omega:studio-connections";
export const MAX_SAVED_CONNECTIONS = 8;
const MAX_LABEL_LENGTH = 80;

const storage = (kind) => {
  try {
    return typeof window === "undefined" ? null : window[kind];
  } catch {
    return null;
  }
};

const cleanLabel = (label, url) => {
  const text = typeof label === "string" ? label.trim() : "";
  return (text || serverDisplayName(url)).slice(0, MAX_LABEL_LENGTH);
};

function cleanEntry(entry) {
  if (!entry || typeof entry !== "object") return null;
  const server = normalizeStudioServer(entry.url);
  if (!server) return null;
  return { url: server.url, label: cleanLabel(entry.label, server.url) };
}

export function readSavedConnections() {
  let raw;
  try {
    raw = JSON.parse(storage("localStorage")?.getItem(SAVED_CONNECTIONS_KEY));
  } catch {
    return [];
  }
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const out = [];
  for (const item of raw) {
    const entry = cleanEntry(item);
    if (!entry || seen.has(entry.url)) continue;
    seen.add(entry.url);
    out.push(entry);
    if (out.length === MAX_SAVED_CONNECTIONS) break;
  }
  return out;
}

function writeSavedConnections(list) {
  const clean = list
    .map(cleanEntry)
    .filter(Boolean)
    .slice(0, MAX_SAVED_CONNECTIONS)
    .map(({ url, label }) => ({ url, label }));
  try {
    storage("localStorage")?.setItem(
      SAVED_CONNECTIONS_KEY,
      JSON.stringify(clean),
    );
  } catch {
    /* storage full or blocked: the list just isn't remembered */
  }
  try {
    window.dispatchEvent(new Event(SAVED_CONNECTIONS_EVENT));
  } catch {
    /* no window */
  }
  return clean;
}

// Moves a server to the top of the list, keeping the label it already has.
export function rememberConnection(url) {
  const server = normalizeStudioServer(url);
  if (!server) return readSavedConnections();
  const list = readSavedConnections();
  const existing = list.find((item) => item.url === server.url);
  const rest = list.filter((item) => item.url !== server.url);
  return writeSavedConnections([
    existing || { url: server.url, label: serverDisplayName(server.url) },
    ...rest,
  ]);
}

export function renameConnection(url, label) {
  return writeSavedConnections(
    readSavedConnections().map((item) =>
      item.url === url ? { url: item.url, label } : item,
    ),
  );
}

export function forgetConnection(url) {
  return writeSavedConnections(
    readSavedConnections().filter((item) => item.url !== url),
  );
}

export function readActiveConnection() {
  try {
    return (
      normalizeStudioServer(
        storage("sessionStorage")?.getItem(ACTIVE_CONNECTION_KEY) || "",
      )?.url ?? null
    );
  } catch {
    return null;
  }
}

export function writeActiveConnection(url) {
  const server = normalizeStudioServer(url);
  try {
    if (server)
      storage("sessionStorage")?.setItem(ACTIVE_CONNECTION_KEY, server.url);
  } catch {
    /* not remembered across a reload */
  }
}

export function clearActiveConnection() {
  try {
    storage("sessionStorage")?.removeItem(ACTIVE_CONNECTION_KEY);
  } catch {
    /* nothing to clear */
  }
}
