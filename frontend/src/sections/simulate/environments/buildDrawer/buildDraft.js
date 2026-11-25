const PREFIX = "environments.build.";

export const BUILD_PARAM = "build";
export const AGENT_WAY_PREFIX = "agent:";

export const buildDraftKey = (way, part) => `${PREFIX}${way}.${part}`;

export function readBuildDraft(key) {
  try {
    const raw = sessionStorage.getItem(key);
    const draft = raw ? JSON.parse(raw) : null;
    return draft && typeof draft === "object" ? draft : null;
  } catch {
    return null;
  }
}

export function saveBuildDraft(key, draft) {
  try {
    sessionStorage.setItem(key, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function clearBuildDraft(key) {
  try {
    sessionStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

export function clearBuildDrafts(way) {
  const prefix = way ? `${PREFIX}${way}.` : PREFIX;
  try {
    Object.keys(sessionStorage)
      .filter((key) => key.startsWith(prefix))
      .forEach((key) => sessionStorage.removeItem(key));
    return true;
  } catch {
    return false;
  }
}
