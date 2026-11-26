// Pure reviewer-rotation logic for reviewer-assigner.mjs: no network, no env.
// Tested by reviewer-rotation.test.mjs.

// The team works 10:00-19:00 IST (UTC+5:30), Monday to Friday.
const WORK_OFFSET_MS = 330 * 60 * 1000;
const WORK_START_H = 10;
const WORK_END_H = 19;
const HOUR = 3600 * 1000;
const DAY = 24 * HOUR;

export const REASSIGN_AFTER_MS = 4 * HOUR;
export const REVIEW_MARKER = "<!-- devflow-review:v1 -->";
const ASSIGN_RE = /<!--\s*devflow-assigned:v1\b([^>]*)-->/;

const lower = (s) => String(s || "").toLowerCase();

function uniqueExcept(logins, author) {
  const seen = new Set([lower(author)]);
  return logins.filter((l) => !seen.has(lower(l)) && seen.add(lower(l)));
}

// Members of every matched team, then fallback_lead, then every member of every team.
export function candidatesFor(teams, config, author) {
  const members = uniqueExcept(
    teams.flatMap((t) => t.members),
    author,
  );
  if (members.length) return members;
  if (config.fallback_lead && lower(config.fallback_lead) !== lower(author))
    return [config.fallback_lead];
  return uniqueExcept(
    Object.values(config.teams).flatMap((t) => t.members),
    author,
  );
}

// Lowest open-review load wins. Ties rotate on `seed` (the PR number): the search
// index lags, so PRs opened together see the same counts.
export function pickLeastLoaded(
  candidates,
  load,
  { exclude = [], seed = 0 } = {},
) {
  const skip = new Set(exclude.map(lower));
  const pool = candidates.filter((c) => !skip.has(lower(c)));
  if (!pool.length) return null;
  const loadOf = (c) => load.get(c) ?? 0;
  const min = Math.min(...pool.map(loadOf));
  const tied = pool
    .filter((c) => loadOf(c) === min)
    .sort((a, b) => lower(a).localeCompare(lower(b)));
  return tied[Math.abs(seed) % tied.length];
}

// Milliseconds of team working time between two instants.
export function workingMsBetween(fromMs, toMs) {
  if (!(toMs > fromMs)) return 0;
  const from = fromMs + WORK_OFFSET_MS;
  const to = toMs + WORK_OFFSET_MS;
  let total = 0;
  for (let day = Math.floor(from / DAY) * DAY; day < to; day += DAY) {
    const weekday = new Date(day).getUTCDay();
    if (weekday === 0 || weekday === 6) continue;
    const start = Math.max(from, day + WORK_START_H * HOUR);
    const end = Math.min(to, day + WORK_END_H * HOUR);
    if (end > start) total += end - start;
  }
  return total;
}

// Sticky marker that review-inbox parses. One login per marker.
export function renderAssignment({ login, at, round }) {
  return `<!-- devflow-assigned:v1 login=${login} at=${at} by=reviewer-assigner round=${round} -->`;
}

export function parseAssignment(body) {
  const m = ASSIGN_RE.exec(body || "");
  if (!m) return null;
  const kv = Object.fromEntries(
    m[1]
      .trim()
      .split(/\s+/)
      .map((pair) => {
        const i = pair.indexOf("=");
        return i === -1 ? [pair, ""] : [pair.slice(0, i), pair.slice(i + 1)];
      }),
  );
  if (!kv.login || Number.isNaN(Date.parse(kv.at))) return null;
  return { login: kv.login, at: kv.at, round: Number(kv.round) || 1 };
}

// Due when the assignee posted no agent review since assignment for 4 working hours.
export function reassignDue(assignment, reviews, nowMs) {
  const atMs = Date.parse(assignment.at);
  const reviewed = reviews.some(
    (r) =>
      lower(r.user?.login) === lower(assignment.login) &&
      (r.body || "").includes(REVIEW_MARKER) &&
      Date.parse(r.submitted_at) >= atMs,
  );
  return !reviewed && workingMsBetween(atMs, nowMs) >= REASSIGN_AFTER_MS;
}
