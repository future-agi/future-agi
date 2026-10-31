// Plain helpers behind the run header: the words for a span of time, when the
// run finished, and what one of its calls is called.

// What a UI-only run action says instead of starting anything.
export const UI_ONLY_STARTED = "UI only. Nothing was started.";

// "45s", "10m 30s", "1h 4m". Null for a missing or negative span.
export function formatSpan(ms) {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

const time = (iso) => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
};

// The run's wall-clock span: start to finish, or to now while it runs.
export function runSpanMs(startedAt, finishedAt, now = Date.now()) {
  const start = time(startedAt);
  if (start == null) return null;
  const end = finishedAt ? time(finishedAt) : now;
  return end == null ? null : end - start;
}

// "just now", "2m ago", "3h ago", "4d ago", then the date. Null when the time
// is missing or unreadable, so the header leaves it out.
export function agoLabel(iso, now = Date.now()) {
  const t = time(iso);
  if (t == null) return null;
  const s = Math.max(0, Math.floor((now - t) / 1000));
  if (s < 45) return "just now";
  if (s < 90) return "a minute ago";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 604800) return `${Math.round(s / 86400)}d ago`;
  return new Date(t).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

// A voice run is made of calls; a chat run of conversations.
export const unitFor = (agentType, surface) =>
  (agentType ?? surface) === "voice" ? "call" : "conversation";

export const plural = (n, one, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;

// How many scenarios the run played. Each one runs `trials` times, so the
// calls divide evenly; a subset run names its scenarios outright.
export function scenarioCount(identity, stats) {
  if (identity?.scenarioIds?.length) return identity.scenarioIds.length;
  const trials = identity?.trials || 1;
  return Math.ceil((stats?.total ?? 0) / trials);
}

// Dollars, to the cent. "-" when the API sends no cost.
export const costLabel = (cost) =>
  cost == null ? "-" : `$${Number(cost).toFixed(2)}`;

// Below this many scenarios the backend withholds the range; rates read as more
// certain than they are, so the header falls back to raw counts.
export const MIN_INTERVAL_SCENARIOS = 10;

// Too few conclusive scenarios for a rate.
export const countsOnly = (reliability) =>
  (reliability?.rows ?? []).filter((row) => row.evaluated).length <
  MIN_INTERVAL_SCENARIOS;
