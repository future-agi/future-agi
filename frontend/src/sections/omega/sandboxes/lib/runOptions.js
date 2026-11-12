/**
 * The options of one command in the Run tab: a time limit, a working folder
 * inside /workspace and environment variables. Pure functions of the caps EE
 * returns with `GET /workspace/sandboxes` (`caps.exec`).
 *
 * The rules mirror EE's exec input check (`parseExecInput` in
 * packages/server/src/workspace/sandboxes/limits/input.ts), so the form refuses
 * what EE would refuse before anything is sent:
 * - `timeoutSeconds`: a whole number from 1 to caps.exec.maxTimeoutSeconds;
 * - `cwd`: a path relative to /workspace, without `.` or `..` segments, a
 *   backslash or NUL, at most 4096 characters;
 * - `env`: at most 32 variables; a name is a letter or `_` then letters,
 *   digits or `_` (128 at most), `OMEGA_*` is the sandbox's own; a value is at
 *   most 8192 bytes and has no NUL.
 * Only the keys EE accepts are ever sent.
 */
export const WORKSPACE_ROOT = "/workspace";
export const ENV_MAX_ENTRIES = 32;
export const ENV_VALUE_MAX_BYTES = 8192;
const ENV_KEY = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
const RESERVED_ENV_KEY = /^OMEGA_/i;
const CWD_MAX_CHARS = 4096;
const TIMEOUT_PRESETS = [30, 60, 120, 300, 600, 900, 1800, 3600];

const byteLength = (text) => new TextEncoder().encode(text).length;

/** `{ default, max }` in seconds, or null when this EE does not say (then no limit is sent). */
export function timeLimits(caps) {
  const exec = caps?.exec;
  if (
    !exec ||
    !Number.isSafeInteger(exec.defaultTimeoutSeconds) ||
    !Number.isSafeInteger(exec.maxTimeoutSeconds) ||
    exec.maxTimeoutSeconds < 1
  ) {
    return null;
  }
  return {
    default: Math.min(exec.defaultTimeoutSeconds, exec.maxTimeoutSeconds),
    max: exec.maxTimeoutSeconds,
  };
}

/** Time-limit choices: presets up to the maximum, plus the default and the maximum itself. */
export function timeoutChoices(caps) {
  const limits = timeLimits(caps);
  if (!limits) return [];
  const inRange = TIMEOUT_PRESETS.filter((seconds) => seconds <= limits.max);
  return [...new Set([...inRange, limits.default, limits.max])].sort(
    (a, b) => a - b,
  );
}

export function initialRunOptions(caps) {
  return {
    timeoutSeconds: timeLimits(caps)?.default ?? null,
    cwd: "",
    env: [],
  };
}

let rowSeq = 0;
/** One empty variable row; `id` only keys the row in the list. */
export function newEnvRow() {
  rowSeq += 1;
  return { id: `env-${rowSeq}`, key: "", value: "" };
}

/**
 * The folder as EE takes it: relative to /workspace, without leading or
 * trailing slashes. A pasted "/workspace/src" or "./src" becomes "src".
 */
export function normalizeCwd(raw) {
  let text = String(raw ?? "").trim();
  if (text === WORKSPACE_ROOT || text.startsWith(`${WORKSPACE_ROOT}/`)) {
    text = text.slice(WORKSPACE_ROOT.length);
  }
  return text
    .split("/")
    .filter((part, index) => part.length > 0 && !(index === 0 && part === "."))
    .join("/");
}

/** Why EE would refuse this folder, in words; null when it is fine. */
export function cwdError(raw) {
  const text = String(raw ?? "").trim();
  if (text === "") return null;
  if (text.length > CWD_MAX_CHARS) return "At most 4096 characters";
  if (text.includes("\\")) return "Use / between folders";
  if (text.includes("\u0000")) return "Remove the NUL character";
  const normalized = normalizeCwd(text);
  if (
    text.startsWith("/") &&
    text !== WORKSPACE_ROOT &&
    !text.startsWith(`${WORKSPACE_ROOT}/`)
  ) {
    return "Use a folder inside /workspace";
  }
  if (normalized.split("/").some((part) => part === "." || part === "..")) {
    return "Use a folder inside /workspace, without . or ..";
  }
  return null;
}

/** -> { [rowId]: { key?: message, value?: message } } for the rows EE would refuse. */
export function envErrors(rows) {
  const errors = {};
  const seen = new Set();
  const put = (row, field, message) => {
    errors[row.id] = { ...errors[row.id], [field]: message };
  };
  for (const row of rows) {
    const key = row.key.trim();
    if (key === "" && row.value === "") continue;
    if (key === "") put(row, "key", "Name this variable");
    else if (!ENV_KEY.test(key))
      put(row, "key", "Letters, digits and _, not starting with a digit");
    else if (RESERVED_ENV_KEY.test(key))
      put(row, "key", "OMEGA_ names are set by the sandbox itself");
    else if (seen.has(key)) put(row, "key", "Already set above");
    if (key !== "") seen.add(key);
    if (row.value.includes("\u0000"))
      put(row, "value", "Remove the NUL character");
    else if (byteLength(row.value) > ENV_VALUE_MAX_BYTES)
      put(row, "value", "At most 8192 bytes");
  }
  return errors;
}

/** Rows that carry a variable (a fully empty row is ignored). */
export function filledEnvRows(rows) {
  return rows.filter((row) => row.key.trim() !== "" || row.value !== "");
}

/** -> { cwd?, env?, timeoutSeconds? } messages; empty when the options can be sent. */
export function validateRunOptions(options, caps) {
  const errors = {};
  const cwd = cwdError(options.cwd);
  if (cwd) errors.cwd = cwd;
  const env = envErrors(options.env);
  if (Object.keys(env).length > 0) errors.env = env;
  if (filledEnvRows(options.env).length > ENV_MAX_ENTRIES) {
    errors.envCount = `At most ${ENV_MAX_ENTRIES} variables`;
  }
  const limits = timeLimits(caps);
  if (limits && options.timeoutSeconds !== null) {
    const t = options.timeoutSeconds;
    if (!Number.isSafeInteger(t) || t < 1 || t > limits.max) {
      errors.timeoutSeconds = `From 1 second to ${formatSeconds(limits.max)}`;
    }
  }
  return errors;
}

/**
 * The exec body's option keys. The time limit is always sent when this EE
 * states one, so what the tab shows is exactly what runs.
 */
export function runPayload(options, caps) {
  const body = {};
  const cwd = normalizeCwd(options.cwd);
  if (cwd) body.cwd = cwd;
  const rows = filledEnvRows(options.env);
  if (rows.length > 0) {
    body.env = Object.fromEntries(
      rows.map((row) => [row.key.trim(), row.value]),
    );
  }
  if (timeLimits(caps) && options.timeoutSeconds !== null) {
    body.timeoutSeconds = options.timeoutSeconds;
  }
  return body;
}

/**
 * Where to look for a working folder: its parent's listing must show it.
 * EE's file listing answers a missing path with an empty listing (never a
 * 404), so the folder itself cannot be listed to prove it exists.
 */
export function folderLookup(cwd) {
  const parts = normalizeCwd(cwd).split("/").filter(Boolean);
  if (parts.length === 0) return null;
  return {
    parent: parts.slice(0, -1).join("/"),
    name: parts[parts.length - 1],
  };
}

/**
 * True when the parent's listing proves the folder is missing. An entry of
 * that name that is not marked a directory may be a symlink (EE lists with
 * lstat), so only a missing name counts.
 */
export function folderMissing(cwd, listing) {
  const lookup = folderLookup(cwd);
  if (!lookup || !Array.isArray(listing?.entries)) return false;
  return !listing.entries.some((entry) => entry.name === lookup.name);
}

/** "/workspace" or "/workspace/src/app". */
export function workdirLabel(cwd) {
  const relative = normalizeCwd(cwd);
  return relative ? `${WORKSPACE_ROOT}/${relative}` : WORKSPACE_ROOT;
}

/**
 * 30 -> "30 s", 60 -> "1 min", 90 -> "1 min 30 s", 3600 -> "1 h". Past an
 * hour the seconds are dropped, never rounded up, so a limit never reads
 * longer than it is (7170 -> "1 h 59 min").
 */
export function formatSeconds(seconds) {
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return s ? `${m} min ${s} s` : `${m} min`;
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}

const unit = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

/**
 * 45 -> "45 seconds", 900 -> "15 minutes", 5400 -> "1 hour 30 minutes". Past
 * an hour the seconds are dropped, never rounded up (7170 -> "1 hour 59
 * minutes").
 */
export function formatSecondsInWords(seconds) {
  if (seconds < 60) return unit(seconds, "second");
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return s ? `${unit(m, "minute")} ${unit(s, "second")}` : unit(m, "minute");
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m ? `${unit(h, "hour")} ${unit(m, "minute")}` : unit(h, "hour");
}

/**
 * The longest a command may run here, from EE's caps
 * (`caps.exec.maxTimeoutSeconds`): "Commands can run for up to 15 minutes
 * here." Null when EE states no limit.
 */
export function commandLimitLine(caps) {
  const limits = timeLimits(caps);
  if (!limits) return null;
  return `Commands can run for up to ${formatSecondsInWords(limits.max)} here.`;
}

/** Said beside the command limit on a hosted deployment. */
export const SELF_HOSTED_LONGER_COMMANDS =
  "Self-hosted installs can allow longer commands.";

/** "Runs in /workspace/src · stops after 10 min · 2 variables". */
export function optionsSummary(options, caps) {
  const parts = [`Runs in ${workdirLabel(options.cwd)}`];
  if (timeLimits(caps) && options.timeoutSeconds !== null) {
    parts.push(
      `a stop will be requested after ${formatSeconds(options.timeoutSeconds)}`,
    );
  }
  const count = filledEnvRows(options.env).length;
  if (count > 0) parts.push(`${count} variable${count === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

/** True when the user changed anything from the defaults. */
export function hasCustomOptions(options, caps) {
  return (
    normalizeCwd(options.cwd) !== "" ||
    filledEnvRows(options.env).length > 0 ||
    (timeLimits(caps) !== null &&
      options.timeoutSeconds !== timeLimits(caps).default)
  );
}

/** A byte cap in the unit EE states it: 1000000 -> "1 MB", 1048576 -> "1 MiB". */
export function formatByteCap(bytes) {
  if (!Number.isSafeInteger(bytes) || bytes <= 0) return null;
  if (bytes % 1_000_000 === 0) return `${bytes / 1_000_000} MB`;
  if (bytes % 1_048_576 === 0) return `${bytes / 1_048_576} MiB`;
  if (bytes % 1000 === 0) return `${bytes / 1000} KB`;
  return `${bytes} bytes`;
}
