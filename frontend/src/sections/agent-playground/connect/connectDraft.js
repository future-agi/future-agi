import { GITHUB_SOURCE, UPLOAD_SOURCE } from "./connectSources";

const DRAFT_KEYS = {
  [GITHUB_SOURCE]: "agents.connect.github.draft",
  [UPLOAD_SOURCE]: "agents.connect.upload.draft",
};

const keyOf = (source = GITHUB_SOURCE) =>
  DRAFT_KEYS[source] || DRAFT_KEYS[GITHUB_SOURCE];

export function readConnectDraft(source) {
  try {
    const raw = sessionStorage.getItem(keyOf(source));
    const draft = raw ? JSON.parse(raw) : null;
    return draft && typeof draft === "object" ? draft : null;
  } catch {
    return null;
  }
}

export function saveConnectDraft(draft, source) {
  try {
    sessionStorage.setItem(keyOf(source), JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function clearConnectDraft(source) {
  try {
    sessionStorage.removeItem(keyOf(source));
    return true;
  } catch {
    return false;
  }
}

export const hasConnectDraft = (source) => readConnectDraft(source) !== null;

export const draftHasProgress = (draft) =>
  draft.stage !== "source" ||
  !!draft.scanId ||
  !!draft.folder ||
  !!draft.link?.trim() ||
  !!draft.repository ||
  !!draft.name?.trim();
