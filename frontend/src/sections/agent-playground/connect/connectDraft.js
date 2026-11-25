import { GITHUB_SOURCE, UPLOAD_SOURCE } from "./connectSources";

const DRAFT_KEYS = {
  [GITHUB_SOURCE]: "agents.connect.github.draft",
  [UPLOAD_SOURCE]: "agents.connect.upload.draft",
};

const keyOf = (source = GITHUB_SOURCE) =>
  DRAFT_KEYS[source] || DRAFT_KEYS[GITHUB_SOURCE];

export function readConnectDraft(source, key) {
  try {
    const raw = sessionStorage.getItem(key || keyOf(source));
    const draft = raw ? JSON.parse(raw) : null;
    return draft && typeof draft === "object" ? draft : null;
  } catch {
    return null;
  }
}

export function saveConnectDraft(draft, source, key) {
  try {
    sessionStorage.setItem(key || keyOf(source), JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function clearConnectDraft(source, key) {
  try {
    sessionStorage.removeItem(key || keyOf(source));
    return true;
  } catch {
    return false;
  }
}

export const hasConnectDraft = (source) => readConnectDraft(source) !== null;

export const selectionKey = (...parts) => JSON.stringify(parts);

export const credentialsOf = (held, key) =>
  held?.key === key ? held : { key, values: {}, extra: [] };

export const shownEnvValues = (credentials, envNames) => {
  const shown = new Set([...envNames, ...credentials.extra]);
  return Object.fromEntries(
    Object.entries(credentials.values).filter(
      ([envName, value]) => value && shown.has(envName),
    ),
  );
};

export const draftHasProgress = (draft) =>
  draft.stage !== "source" ||
  !!draft.scanId ||
  !!draft.folder ||
  !!draft.link?.trim() ||
  !!draft.repository ||
  !!draft.name?.trim();
