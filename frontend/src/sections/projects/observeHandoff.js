import { UNITS } from "./observePopulation";

// Hand-offs from the Observe views to Instant eval's Ask page (a question
// answered for every row in a view) and Insights (a report that finds the
// patterns), and between the two. Small context rides in
// the URL; row ids, which can run to thousands, ride in session storage under
// a short key so links stay short and survive a reload of the target page.

const PREFIX = "observe-handoff:";
export const INSIGHTS_NEW_PARAM = "new";
export const INSIGHTS_SEED_PARAM = "seed";
export const ASK_NEW_PARAM = "new";
export const ASK_SEED_PARAM = "seed";

const makeKey = () =>
  `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

// Hand-offs kept per tab. Entries are read again on a reload of the target
// page, so they aren't removed when read; the oldest go instead, and all but
// the new one when the storage is full (some hold thousands of ids).
export const KEEP_HANDOFFS = 10;

function handoffKeys(storage) {
  const keys = [];
  for (let i = 0; i < storage.length; i += 1) {
    const name = storage.key(i);
    if (name?.startsWith(PREFIX)) keys.push(name);
  }
  // Keys start with their time in base 36, so they sort oldest first.
  return keys.sort();
}

function pruneHandoffs(storage, keep) {
  const keys = handoffKeys(storage);
  keys.slice(0, Math.max(0, keys.length - keep)).forEach((name) => {
    storage.removeItem(name);
  });
}

export function stashHandoff(value) {
  const key = makeKey();
  const storage = globalThis.sessionStorage;
  try {
    if (!storage) return key;
    const raw = JSON.stringify(value);
    pruneHandoffs(storage, KEEP_HANDOFFS - 1);
    try {
      storage.setItem(PREFIX + key, raw);
    } catch {
      // Full: drop every older hand-off and try once more.
      pruneHandoffs(storage, 0);
      storage.setItem(PREFIX + key, raw);
    }
  } catch {
    // Storage unavailable: the target opens with the scope from the URL only.
  }
  return key;
}

export function readHandoff(key) {
  if (!key) return null;
  try {
    const raw = globalThis.sessionStorage?.getItem(PREFIX + key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/**
 * Open the "New report" flow in the Insights tab.
 *
 * seed: {
 *   unit: "traces" | "sessions" | "users",
 *   filters?: backend filters of the source view, its created_at window included,
 *   dateOption?: the view's date preset ("7D", "Today", …) or "Custom",
 *   window?: [isoStart, isoEnd], the window those filters carry,
 *   label?: human scope label ("Past 7D · 2 filters"),
 *   population?: [{id, time, user_id}]  // exact rows, e.g. every "No" answer
 *   question?: string                    // a starting question for the report
 *   source?: string                      // "Instant eval: <question>"
 * }
 */
export function insightsReportLink(projectId, seed) {
  const params = new URLSearchParams({ [INSIGHTS_NEW_PARAM]: "1" });
  params.set(INSIGHTS_SEED_PARAM, stashHandoff(seed));
  return `/dashboard/observe/${projectId}/insights?${params.toString()}`;
}

// The Observe tab bar entry a unit's rows come from. Spans live on the
// Traces view's Spans tab.
export const UNIT_TAB = {
  traces: "traces",
  spans: "spans",
  sessions: "sessions",
  users: "users",
};

/**
 * Open the Ask page (Instant eval) with a new question about rows of a view.
 * The tab bar keeps highlighting the view the rows came from.
 *
 * ask: {
 *   unit: "traces" | "spans" | "sessions" | "users",
 *   filters?: backend filters of the source view, its created_at window included,
 *   dateOption?: the view's date preset ("7D", "Today", …) or "Custom",
 *   window?: [isoStart, isoEnd], the window those filters carry,
 *   label?: human scope label ("Past 7D · 2 filters"),
 *   population?: [{id, time, user_id}],   // exact rows, when already known
 *   ids?: string[],                       // only these rows
 *   source?: string,                      // where the scope came from, when not a view
 *   questions?: [{question, answerType: "yes_no" | "category", categories?, definitions?}],
 * }
 */
export function askLink(projectId, ask) {
  const unit = UNITS[ask?.unit] ? ask.unit : "traces";
  const params = new URLSearchParams({
    [ASK_NEW_PARAM]: "1",
    [ASK_SEED_PARAM]: stashHandoff({ ...ask, unit }),
    unit,
    tab: UNIT_TAB[unit],
  });
  return `/dashboard/observe/${projectId}/ask?${params.toString()}`;
}
