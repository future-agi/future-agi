// W9 dataset organisation rules (W9-SCOPE §5 A1, A6, A7): the one place the
// metadata, folder and agent rules and their sentences live. The mock's
// datasetOrg.validateOrg and the dialogs both call these, so the client
// refuses exactly what the server refuses, with the same words. Pure.
//
// The limits are the contract written in W9-SCOPE (not tunables).

export const METADATA_KEY_RE = /^[a-z][a-z0-9_.-]{0,39}$/;
export const METADATA_MAX_KEYS = 30;
export const METADATA_VALUE_MAX_CHARS = 200;
export const FOLDER_SEGMENT_MAX_CHARS = 64;
export const FOLDER_MAX_DEPTH = 5;
export const AGENTS_MAX = 20;

/** The organisation keys `edit_dataset_behavior` accepts besides its own. */
export const ORG_KEYS = Object.freeze(["metadata", "folder", "agent_ids"]);

export const keySentence = (key) =>
  `Metadata key “${key}” must start with a lowercase letter and use only lowercase letters, digits, “.”, “_” or “-” (40 characters at most).`;

const isPlainObject = (v) =>
  v !== null && typeof v === "object" && !Array.isArray(v);

/** One metadata value's problem, or null. `null` (= remove) is valid. */
export function metadataValueProblem(key, value) {
  if (value === null) return null;
  if (typeof value === "boolean") return null;
  if (typeof value === "number")
    return Number.isFinite(value)
      ? null
      : `“${key}” must be text, a number or true/false.`;
  if (typeof value === "string")
    return value.length > METADATA_VALUE_MAX_CHARS
      ? `The value of “${key}” is longer than ${METADATA_VALUE_MAX_CHARS} characters.`
      : null;
  if (Array.isArray(value))
    return `“${key}” must be text, a number or true/false, not a list.`;
  return `“${key}” must be text, a number or true/false, not an object.`;
}

/**
 * A metadata map's first problem, or null. `current` is the dataset's map
 * before the write: the key count is checked on the result.
 */
export function metadataProblem(metadata, current = {}) {
  if (metadata === undefined) return null;
  if (!isPlainObject(metadata))
    return "Metadata must be an object of key: value pairs.";
  const keys = Object.keys(metadata);
  const badKey = keys.find((k) => !METADATA_KEY_RE.test(k));
  if (badKey !== undefined) return keySentence(badKey);
  for (const k of keys) {
    const p = metadataValueProblem(k, metadata[k]);
    if (p) return p;
  }
  const next = applyMetadata(current, metadata);
  if (Object.keys(next).length > METADATA_MAX_KEYS)
    return `A dataset can have up to ${METADATA_MAX_KEYS} metadata keys.`;
  return null;
}

/** `current` with `patch` applied: a `null` value removes the key. Pure. */
export function applyMetadata(current, patch) {
  const out = { ...(isPlainObject(current) ? current : {}) };
  Object.entries(patch ?? {}).forEach(([k, v]) => {
    if (v === null) delete out[k];
    else out[k] = v;
  });
  return out;
}

/**
 * A folder path in its stored form: segments trimmed, no leading or
 * trailing "/". Empty segments are kept (as "") so validation can name them.
 */
export function normalizeFolder(path) {
  const raw = String(path ?? "").trim();
  if (!raw) return "";
  return raw
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .map((s) => s.trim())
    .join("/");
}

/** A folder path's problem, or null. `""` (no folder) is valid. */
export function folderProblem(path) {
  if (path === undefined || path === null) return null;
  if (typeof path !== "string")
    return "A folder must be a path like support/billing.";
  const norm = normalizeFolder(path);
  if (!norm) return null;
  const segs = norm.split("/");
  if (segs.some((s) => !s))
    return "A folder name can't be empty or contain “/”.";
  if (segs.some((s) => s.length > FOLDER_SEGMENT_MAX_CHARS))
    return `A folder name can be ${FOLDER_SEGMENT_MAX_CHARS} characters at most.`;
  if (segs.length > FOLDER_MAX_DEPTH)
    return `Folders go ${FOLDER_MAX_DEPTH} levels deep at most.`;
  return null;
}

/** An agent list's problem, or null. `graphIds`: the workspace's graphs. */
export function agentsProblem(ids, { graphIds } = {}) {
  if (ids === undefined) return null;
  if (!Array.isArray(ids)) return "agent_ids must be a list of agent ids.";
  const unique = [...new Set(ids.map(String))];
  if (unique.length > AGENTS_MAX)
    return `A dataset can be attached to up to ${AGENTS_MAX} agents.`;
  if (graphIds) {
    const known = new Set(graphIds.map(String));
    const missing = unique.find((id) => !known.has(id));
    if (missing !== undefined)
      return `Agent ${missing} doesn't exist in this workspace.`;
  }
  return null;
}

/**
 * The first problem of an organisation patch `{metadata?, folder?,
 * agent_ids?}`, or null. `current` is the dataset's org record.
 */
export function orgPatchProblem(patch, { graphIds, current } = {}) {
  if (!patch) return null;
  return (
    metadataProblem(patch.metadata, current?.metadata ?? {}) ??
    folderProblem(patch.folder) ??
    agentsProblem(patch.agent_ids, { graphIds })
  );
}

/** Splits a request body into its organisation keys and the rest. Pure. */
export function splitOrgKeys(body) {
  const org = {};
  const rest = {};
  Object.entries(body ?? {}).forEach(([k, v]) => {
    if (ORG_KEYS.includes(k)) org[k] = v;
    else rest[k] = v;
  });
  return { org, rest, hasOrg: Object.keys(org).length > 0 };
}
