// R3 P3 (CR#42): runs the viewer is waiting on, across pages. Zustand,
// mirrored to sessionStorage (per tab; wrapped, never required) so a page
// change or a reload keeps the tray. Entries: {runId, experimentId,
// datasetId, label, name, kind, waiting, addedAt}.
import { create } from "zustand";
import {
  ledgerNounOf,
  runLedgerParts,
} from "src/sections/common/Runs/runLedger";

export const RUN_TRAY_KEY = "fa.runTray";
export const RUN_TRAY_MAX = 8;
export const RUN_STATUS_UNAVAILABLE = "Status unavailable";

const read = () => {
  try {
    const raw = window.sessionStorage.getItem(RUN_TRAY_KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.filter((e) => e?.runId) : [];
  } catch {
    return [];
  }
};

const write = (list) => {
  try {
    window.sessionStorage.setItem(RUN_TRAY_KEY, JSON.stringify(list));
  } catch {
    // Private windows / blocked storage: the tray still works this page.
  }
};

/** Pure: add or refresh entries (newest first, capped, deduped by run). */
export function mergeTray(list, entries, now = Date.now()) {
  const byId = new Map(list.map((e) => [e.runId, e]));
  entries
    .filter((e) => e?.runId)
    .forEach((e) => {
      const prev = byId.get(e.runId);
      byId.set(e.runId, { ...prev, ...e, addedAt: prev?.addedAt ?? now });
    });
  return [...byId.values()]
    .sort((a, b) => b.addedAt - a.addedAt)
    .slice(0, RUN_TRAY_MAX);
}

/** IT-R3 G1 P1: a tracked run's experiment no longer exists (404 after a
 * reload of the in-memory prototype, or it was deleted). Pure. */
export const isMissingExperimentError = (err) => {
  const code = err?.statusCode ?? err?.status ?? err?.response?.status;
  if (code === 404) return true;
  const msg = String(err?.result ?? err?.message ?? "");
  return (code === 400 || code == null) && /not found/i.test(msg);
};

/** IT-R3 G1 P1: the one neutral toast when tracked runs disappear. Pure. */
export const removedRunsText = (labels) => {
  const list = [...new Set((labels ?? []).filter(Boolean))];
  if (!list.length) return "A run was removed";
  return list.length === 1
    ? `Run ${list[0]} was removed`
    : `Runs ${list.join(", ")} were removed`;
};

/**
 * IT-R3 G1 P1: the tray's one-line summary from tracked entries + the live
 * runs the watcher saw (`live` = {runId: Run}). Pure.
 * L7c g5 (runtray:pill, runtray:dock, sdk:waiting-copy): the pill, the dock
 * chip and its tooltip say the same thing with the one waiting phrase from
 * the run ledger (`waitingPhrase(run)` → `waiting for results`); a run
 * that only waits is never "going", and nothing names "your code":
 *   `1 run waiting for results · #6A` (every tracked run waits),
 *   `2 runs · 1 going · #6A waiting for results` (mixed),
 *   `2 runs going` (none waits).
 * `short` (the dock) drops the waiting phrase: `1 run waiting · #6A`.
 * `isWaiting(run)` decides waiting (injected so this stays a .js module
 * without the status helpers).
 */
export function trayLabel(
  tracked,
  live,
  isWaiting,
  { short = false, waitingPhrase = () => "waiting", checks = {} } = {},
) {
  const n = (tracked ?? []).length;
  if (!n) return "";
  const noun = n === 1 ? "run" : "runs";
  if (tracked.some((e) => checks[e.experimentId]?.failed))
    return `${n} ${noun} · ${RUN_STATUS_UNAVAILABLE}`;
  if (tracked.some((e) => !live?.[e.runId])) return `${n} ${noun} · Checking…`;
  const waiting = tracked.filter((e) => {
    const run = live?.[e.runId];
    return run && isWaiting(run);
  });
  const first = waiting[0];
  const phrase = first ? waitingPhrase(live?.[first.runId]) : "waiting";
  if (waiting.length === n) {
    const lead = (first ?? tracked[0])?.label;
    return short
      ? `${n} ${noun} waiting${lead ? ` · ${lead}` : ""}`
      : `${n} ${noun} ${phrase}${lead ? ` · ${lead}` : ""}`;
  }
  if (!waiting.length) {
    if (!short) return `${n} ${noun} going`;
    const lead = tracked[0]?.label;
    return `${n} ${noun} going${lead ? ` · ${lead}` : ""}`;
  }
  const going = n - waiting.length;
  return short
    ? `${n} ${noun} · ${going} going · ${waiting.length} waiting`
    : `${n} ${noun} · ${going} going · ${first.label} ${phrase}${waiting.length > 1 ? ` (+${waiting.length - 1} waiting)` : ""}`;
}

/**
 * L7c g5 (runtray:finish-toast): the finish toast says what the run's own
 * ledger says about errors (`#4A finished · Completed · 18 rows errored
 * (RateLimitError)`); the tab-title badge is gone (it was never seen: a
 * hidden tab polls too slowly to finish a run while hidden). Pure.
 */
export function finishToastText(run, statusLabel) {
  const parts = runLedgerParts(run);
  const errored = parts.find((x) => x.key === "errored");
  // L7c g5 (runtray:finish-toast): tries that errored on rows whose other
  // tries ran (TS #4A: `18 tries errored (RateLimitError)`) are said too,
  // in the ledger's own words.
  const erroredTries = parts.find((x) => x.key === "errored_tries");
  const noun = ledgerNounOf(run?.ledger);
  const erroredText = errored
    ? `${errored.n} ${errored.n === 1 ? noun : `${noun}s`} errored${errored.cause ? ` (${errored.cause})` : ""}`
    : null;
  return [
    `${run?.label ?? "Run"} finished`,
    statusLabel,
    erroredText,
    erroredTries?.n > 0 ? erroredTries.text : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export const useRunTray = create((set) => ({
  runs: typeof window === "undefined" ? [] : read(),
  // IT-R3 G1 P1: the live runs the tray watcher last saw (not persisted), so
  // the docked chip in an open drawer reads the same status as the pill.
  live: {},
  // Query state and retry callbacks are transient, never session-persisted.
  checks: {},
  setCheck: (experimentId, check) =>
    set((s) => {
      const checks = { ...s.checks };
      if (check) checks[experimentId] = check;
      else delete checks[experimentId];
      return { checks };
    }),
  setLive: (runs) =>
    set((s) => {
      const next = { ...s.live };
      (runs ?? []).forEach((r) => {
        if (r?.run_id) next[r.run_id] = r;
      });
      return { live: next };
    }),
  track: (entries) =>
    set((s) => {
      const next = mergeTray(s.runs, [].concat(entries));
      write(next);
      return { runs: next };
    }),
  untrack: (runIds) =>
    set((s) => {
      const drop = new Set([].concat(runIds));
      const next = s.runs.filter((e) => !drop.has(e.runId));
      write(next);
      return { runs: next };
    }),
}));

/** Track runs from a canonical Run list (experiment detail / launch). */
export const trackRuns = (runs, { datasetId, experimentId } = {}) =>
  useRunTray.getState().track(
    (runs ?? []).map((r) => ({
      runId: r.run_id,
      experimentId: r.experiment_id ?? experimentId,
      datasetId: r.dataset_id ?? datasetId,
      label: r.label,
      name: r.name,
      kind: r.kind ?? r.target_type,
    })),
  );
