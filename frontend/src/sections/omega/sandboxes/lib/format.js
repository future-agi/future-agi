/**
 * Display helpers for sandbox values. They only re-format what the API
 * returned; a missing value renders as an em dash, never as a guess.
 */
export const EMPTY = "—";

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function formatCpus(cpus) {
  if (typeof cpus !== "number" || !Number.isFinite(cpus)) return EMPTY;
  const text = Number.isInteger(cpus) ? String(cpus) : String(+cpus.toFixed(2));
  return `${text} CPU`;
}

export function formatMemoryMb(memoryMb) {
  if (typeof memoryMb !== "number" || !Number.isFinite(memoryMb)) return EMPTY;
  if (memoryMb >= 1024 && memoryMb % 1024 === 0)
    return `${memoryMb / 1024} GiB`;
  return `${memoryMb} MiB`;
}

/** `{cpus, memoryMb}` -> "1 CPU · 512 MB"; null limits -> em dash. */
export function formatLimits(limits) {
  if (!limits) return EMPTY;
  return `${formatCpus(limits.cpus)} · ${formatMemoryMb(limits.memoryMb)}`;
}

export function formatBytes(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) return EMPTY;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${+(bytes / 1024).toFixed(1)} KiB`;
  return `${+(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

/** Seconds in whole units, largest first: 45 -> "45 s", 5400 -> "1 h 30 min". */
export function formatDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0)
    return EMPTY;
  const s = Math.round(seconds);
  if (s < MINUTE) return `${s} s`;
  if (s < HOUR) return `${Math.round(s / MINUTE)} min`;
  if (s < DAY) {
    const h = Math.floor(s / HOUR);
    const m = Math.round((s % HOUR) / MINUTE);
    return m ? `${h} h ${m} min` : `${h} h`;
  }
  const d = Math.floor(s / DAY);
  const h = Math.round((s % DAY) / HOUR);
  return h ? `${d} d ${h} h` : `${d} d`;
}

export function formatMs(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return EMPTY;
  return ms < 1000 ? `${Math.round(ms)} ms` : `${+(ms / 1000).toFixed(1)} s`;
}

function parse(iso) {
  const t = typeof iso === "string" ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : null;
}

/** "just now", "4 min ago", "3 h ago", "2 d ago". */
export function timeAgo(iso, now = Date.now()) {
  const t = parse(iso);
  if (t === null) return EMPTY;
  const seconds = Math.max(0, (now - t) / 1000);
  if (seconds < 45) return "just now";
  return `${formatDuration(seconds)} ago`;
}

/**
 * When the idle timeout will hibernate an active sandbox: "in 28 min",
 * "any moment" once due, em dash when the API gives no instant.
 */
export function hibernatesIn(iso, now = Date.now()) {
  const t = parse(iso);
  if (t === null) return EMPTY;
  const seconds = (t - now) / 1000;
  if (seconds <= 30) return "any moment";
  return `in ${formatDuration(seconds)}`;
}

export function formatDateTime(iso) {
  const t = parse(iso);
  if (t === null) return EMPTY;
  return new Date(t).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * The exact instant with its time zone, shown on hover beside a relative
 * time: "Nov 10, 2026, 01:08:00 AM UTC". `timeZone` defaults to the viewer's.
 */
export function formatExactTime(iso, { timeZone } = {}) {
  const t = parse(iso);
  if (t === null) return EMPTY;
  return new Date(t).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
    ...(timeZone ? { timeZone } : {}),
  });
}

/** Shorten a long id for a cell, keeping both ends readable. */
export function shortId(id, keep = 8) {
  if (typeof id !== "string") return EMPTY;
  return id.length > keep * 2 + 1 ? `${id.slice(0, keep)}…${id.slice(-4)}` : id;
}

/**
 * Who did an Activity entry. EE writes the idle reaper and other automatic
 * work as `system:<what>`: those read as "Automatic" (with "(idle)" for the
 * idle hibernation), never as the internal name. A person is EE's account id
 * until the Activity API names people (SB:W2-OBSERVE), shortened.
 */
export function actorLabel(actor) {
  if (typeof actor !== "string" || actor === "") return EMPTY;
  if (actor.startsWith("system:"))
    return /idle|reap/i.test(actor) ? "Automatic (idle)" : "Automatic";
  return shortId(actor);
}

const STATE_LABELS = {
  active: "Running",
  hibernated: "Hibernated",
  recovery_required: "Needs recovery",
  destroyed: "Deleted",
};

export function stateLabel(state) {
  return STATE_LABELS[state] || state || EMPTY;
}

const RUNTIME_LABELS = {
  container: "Container",
  local_process: "Local process",
};

export function runtimeLabel(kind) {
  return RUNTIME_LABELS[kind] || kind || EMPTY;
}

const NETWORK_LABELS = {
  none: "Off",
  full: "Policy-controlled",
  host: "Host network",
};

export function networkLabel(network) {
  return NETWORK_LABELS[network] || EMPTY;
}

const VERB_LABELS = {
  "sandbox.create": "Created",
  "sandbox.restore": "Restored from a snapshot",
  "sandbox.update": "Settings changed",
  "sandbox.exec": "Ran a command",
  "sandbox.exec.cancel": "Cancelled a command",
  "sandbox.file.write": "Wrote a file",
  "sandbox.file.upload": "Uploaded a file",
  "sandbox.file.download": "Downloaded a file",
  "sandbox.file.delete": "Deleted a file or folder",
  "sandbox.file.move": "Renamed or moved",
  "sandbox.file.mkdir": "Created a folder",
  "sandbox.snapshot": "Took a snapshot",
  "sandbox.hibernate": "Hibernated",
  "sandbox.resume": "Resumed",
  "sandbox.destroy": "Deleted",
};

/** Known audit verbs read as words; an unknown verb is shown as recorded. */
export function verbLabel(verb) {
  return VERB_LABELS[verb] || verb || EMPTY;
}
