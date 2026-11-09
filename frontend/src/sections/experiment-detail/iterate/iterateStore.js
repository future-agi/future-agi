// Session state of the Iterate dock (R2-SPEC §4.3.1). The dock is mounted
// separately on the experiment and compare pages, and the auto-compare
// navigation remounts it, so what must survive lives here: whether the dock
// was opened this session (it then stays as a collapsed bar), the draft per
// parent run, and the iteration in flight. Progress is polled here (R1),
// not in a component, so a run that finishes while the user is on another
// page still ends in a toast.
import { createElement } from "react";
import { Button } from "@mui/material";
import { create } from "zustand";
import axios, { endpoints } from "src/utils/axios";
import { closeSnackbar, enqueueSnackbar } from "src/components/snackbar";
import { MATRIX_PAGE_SIZE, invalidateR2Reads } from "src/api/develop/r2";
import { summarizeIteration } from "./iterateSummary";
import { pageOf } from "./pageOf";
import { buildIterateBody } from "./iterateModel";
import {
  TERMINAL_STATUSES,
  normalizeStatus,
  runOutcomeKey,
} from "src/sections/common/Runs/runStatus";

const POLL_MS = 1000;
const DISMISS_KEY = "fa.iterate.banner.dismissed";

const FOLD_KEY = "fa.iterate.banner.fold";

const readDismissed = () => {
  try {
    return JSON.parse(window.localStorage.getItem(DISMISS_KEY) ?? "[]");
  } catch {
    return [];
  }
};

const readFold = () => {
  try {
    const v = JSON.parse(window.localStorage.getItem(FOLD_KEY) ?? "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
};

export const useIterateStore = create((set) => ({
  /** The dock was opened this session (collapsed bar from then on). */
  opened: false,
  expanded: false,
  /** Parent run the dock is on. */
  runId: null,
  /** Draft per parent run id: scope, rows, change edits, trials, … */
  drafts: {},
  /** One-shot entry (from the URL): `{runId, scope, rows, change, cause, nonce}`. */
  entry: null,
  /** The iteration in flight: I1 result + where it was started. */
  active: null,
  /** Finished iterations by run id: `{cause, noise, guardFor, from}`. */
  finished: {},
  /**
   * iterate r1 G3 (P2): the fix-run that finished last this session
   * (`{runId, baseId, datasetId, label, compareUrl, pathId}`). The result
   * banner shows it on the page it was started from; nothing navigates.
   */
  lastFinished: null,
  dismissed: readDismissed(),
  /**
   * iterate r2 G3 (P2): the result banner per run, `open` | `closed`. The
   * first view opens it and records `closed`, so later views show its
   * first line (the chevron opens it again; the choice is remembered).
   */
  bannerFold: readFold(),
  navigate: null,
  queryClient: null,

  register: (navigate, queryClient) => set({ navigate, queryClient }),

  open: ({ runId, entry } = {}) =>
    set((s) => ({
      opened: true,
      expanded: true,
      runId: runId ?? s.runId,
      entry: entry ? { ...entry, nonce: Date.now() } : s.entry,
    })),
  toggle: () =>
    set((s) =>
      s.opened ? { expanded: !s.expanded } : { opened: true, expanded: true },
    ),
  collapse: () => set({ expanded: false }),
  setRun: (runId) => set({ runId }),
  consumeEntry: () => set({ entry: null }),

  patchDraft: (runId, patch) =>
    set((s) => ({
      drafts: {
        ...s.drafts,
        [runId]: {
          ...(s.drafts[runId] ?? {}),
          ...(typeof patch === "function"
            ? patch(s.drafts[runId] ?? {})
            : patch),
        },
      },
    })),
  resetDraft: (runId) =>
    set((s) => {
      const drafts = { ...s.drafts };
      delete drafts[runId];
      return { drafts };
    }),

  dismissBanner: (runId) =>
    set((s) => {
      const dismissed = [...new Set([...s.dismissed, runId])];
      try {
        window.localStorage.setItem(DISMISS_KEY, JSON.stringify(dismissed));
      } catch {
        /* storage off: dismissed for this page only */
      }
      return { dismissed };
    }),

  setBannerFold: (runId, value) =>
    set((s) => {
      if (!runId || s.bannerFold[runId] === value) return {};
      const bannerFold = { ...s.bannerFold, [runId]: value };
      try {
        window.localStorage.setItem(FOLD_KEY, JSON.stringify(bannerFold));
      } catch {
        /* storage off: remembered for this page only */
      }
      return { bannerFold };
    }),

  /**
   * Track an I1 result until its run completes. `meta`: `{datasetId,
   * baseId, startedPath, compareUrl, cause, noise, guardFor}`.
   */
  start: (result, meta) => {
    stopPolling();
    set({
      active: {
        ...meta,
        runId: result.run_id,
        label: result.label,
        attempts: result.attempts,
        rowsToRun: result.rows_to_run,
        rowsCarried: result.rows_carried,
        trials: result.rows_to_run
          ? Math.round(result.attempts / result.rows_to_run)
          : 1,
        listed: result.listed,
        done: 0,
        status: "Queued",
        startedAt: Date.now(),
      },
      expanded: false,
      opened: true,
    });
    // S6 (iterate r2 G3): no `started` toast — the dock bar already says
    // `#2F running · 12 of 39 attempts` and the results toolbar `Live · …`.
    poll();
  },
}));

export { pageOf };

let timer = null;
function stopPolling() {
  if (timer) clearTimeout(timer);
  timer = null;
}

// IT-R5 G3 (P4): the toast closes when it is used (the page it opens has
// the result banner: one verdict on screen).
const toastAction = (url) => {
  const CompareAction = (snackbarId) =>
    createElement(
      Button,
      {
        size: "small",
        color: "inherit",
        sx: { fontWeight: 600 },
        onClick: () => {
          closeSnackbar(snackbarId);
          useIterateStore.getState().navigate?.(url);
        },
      },
      "Compare",
    );
  return CompareAction;
};

/**
 * IT-R4 G3 (P2, P7): the fix run's one progress counter, read by the dock
 * and the results toolbar: `6 rows × 3 tries · 7 of 18 tries done · ~10 s
 * left` (`short`: `7 of 18 tries · ~10 s left`).
 */
export function fixCounterText(active, { short = false } = {}) {
  if (!active) return "";
  const rows = active.rowsToRun ?? 0;
  const trials = active.trials ?? 1;
  const attempts = active.attempts ?? rows * trials;
  const done = Math.min(active.done ?? 0, attempts);
  const eta =
    Number.isFinite(active.eta) && active.eta > 0 && done < attempts
      ? ` · ~${Math.max(5, Math.round(active.eta / 5) * 5)} s left`
      : "";
  const tries = (n) => (n === 1 ? "try" : "tries");
  if (short) return `${done} of ${attempts} ${tries(attempts)}${eta}`;
  return `${rows} ${rows === 1 ? "row" : "rows"} × ${trials} ${tries(trials)} · ${done} of ${attempts} ${tries(attempts)} done${eta}`;
}

/** Attempts done from R1 progress (rows done after the carried head). */
export function attemptsDone(active, progress) {
  if (!active || !progress) return 0;
  const done = Math.max(0, (progress.done ?? 0) - (active.rowsCarried ?? 0));
  return Math.min(active.attempts, done * (active.trials ?? 1));
}

async function poll() {
  const { active } = useIterateStore.getState();
  if (!active) return;
  let run = null;
  try {
    const res = await axios.get(
      endpoints.develop.datasetRuns.list(active.datasetId),
      {
        params: {
          limit: 500,
          include: active.listed ? undefined : "quick_tests",
        },
      },
    );
    run = (res.data?.result?.results ?? []).find(
      (r) => r.run_id === active.runId,
    );
  } catch {
    run = null;
  }
  const state = useIterateStore.getState();
  if (!state.active || state.active.runId !== active.runId) return;
  const status = run?.status ?? state.active.status;
  const done = attemptsDone(state.active, run?.progress);
  // the capability contract: the fix run's REAL outcome (a run stamped Completed whose
  // rows all errored is Failed), and a partial run is terminal too.
  const outcome = iterateOutcomeKey(run, status);
  if (TERMINAL_STATUSES.includes(outcome)) {
    finish(state, run, outcome);
    return;
  }
  useIterateStore.setState({
    active: {
      ...state.active,
      done,
      status,
      eta: run?.progress?.eta_s ?? state.active.eta ?? null,
    },
  });
  timer = setTimeout(poll, POLL_MS);
}

/**
 * the capability contract: the ONE outcome key of the fix run (runStatus.runOutcomeKey),
 * from the listed run when there is one, else the last known status.
 */
export function iterateOutcomeKey(run, status) {
  return run ? runOutcomeKey(run) : normalizeStatus(status);
}

/**
 * The toast a finished fix run leaves, by its real outcome: success only for
 * a clean Completed; a partial run finished with errors (warning); a failed
 * run says Failed (error); a stopped one stopped before it finished.
 * @returns {{text: string, variant: string}}
 */
export function iterateFinishToast(label, key, summary = null) {
  const tail = summary ? `: ${summary}` : "";
  if (key === "Completed")
    return { text: `${label} finished${tail}`, variant: "success" };
  if (key === "PartialCompleted")
    return { text: `${label} finished with errors${tail}`, variant: "warning" };
  if (key === "Failed") return { text: `${label} failed`, variant: "error" };
  return { text: `${label} stopped before it finished`, variant: "warning" };
}

/** `6 fixed, 0 new failures` for the finished-while-away toast. */
async function finishedSummary(a) {
  try {
    const res = await axios.get(
      endpoints.develop.datasetRuns.results(a.datasetId),
      {
        params: {
          runs: `${a.baseId},${a.runId}`,
          baseline: a.baseId,
          shape: "matrix",
          page: 0,
          page_size: MATRIX_PAGE_SIZE,
        },
      },
    );
    const s = summarizeIteration(res.data?.result, a.baseId, a.runId);
    // IT-R5 G3 (P4): a chained or guard run is a full verdict vs the base
    // (it carries from the fix run, not from the base): the banner's words.
    return s.carried.length && !a.guardFor
      ? `${s.fixed.length} fixed, ${s.newFailures.length} new ${s.newFailures.length === 1 ? "failure" : "failures"}`
      : `${s.better.length} better, ${s.worse.length} worse`;
  } catch {
    return null;
  }
}

async function finish(state, run, status) {
  const a = state.active;
  if (state.queryClient) invalidateR2Reads(state.queryClient, a.datasetId);
  // `status` is the run's real outcome (iterateOutcomeKey). A partial run
  // still has a verdict to show; a failed or stopped one does not.
  const done = status === "Completed" || status === "PartialCompleted";
  useIterateStore.setState((s) => ({
    active: null,
    finished: {
      ...s.finished,
      [a.runId]: {
        cause: a.cause ?? null,
        noise: Boolean(a.noise),
        guardFor: a.guardFor ?? null,
        label: a.label,
        pathId: a.pathId ?? null,
      },
    },
    lastFinished: done
      ? {
          runId: a.runId,
          baseId: a.baseId ?? null,
          datasetId: a.datasetId,
          label: a.label,
          compareUrl: a.compareUrl ?? null,
          pathId: a.pathId ?? null,
          startedPath: a.startedPath ?? null,
        }
      : s.lastFinished,
  }));
  if (!done) {
    const t = iterateFinishToast(a.label, status);
    enqueueSnackbar(t.text, { variant: t.variant });
    return;
  }
  // iterate r2 G3 (P4): `Failing rows first, then the other 17` — rerun the
  // rest from the new run now; its verdict (vs the base, every row) is the
  // one the banner shows when it lands.
  if (a.then?.rows?.length && a.baseId) {
    chainRest(a);
    return;
  }
  const url = a.compareUrl;
  // P2: never leave the page on its own. On the page it started from, the
  // result banner says what happened and offers `Compare` / `Open Paths`.
  // IT-R5 G3 (P4): the side by side page has no result banner (the fix ran
  // in place there), so it gets the toast and its way to the verdict.
  const sideBySide = /\/compare\/tasks\/?$/.test(pageOf(a.startedPath ?? ""));
  if (!sideBySide && pageOf(window.location.pathname) === pageOf(a.startedPath))
    return;
  const summary = a.baseId ? await finishedSummary(a) : null;
  const t = iterateFinishToast(a.label, status, summary);
  enqueueSnackbar(t.text, {
    variant: t.variant,
    autoHideDuration: 8000,
    action: url ? toastAction(url) : undefined,
  });
}

async function chainRest(a) {
  const body = buildIterateBody({
    parentRunId: a.runId,
    scopeType: "rows",
    rowIds: a.then.rows,
    scopeLabel: `The other ${a.then.rows.length} rows, from ${a.label}`,
    trials: a.then.trials,
    carryOver: true,
    changes: [],
  });
  try {
    const res = await axios.post(
      endpoints.develop.r2.iterate(a.then.experimentId),
      body,
    );
    const result = res.data?.result;
    if (!result?.run_id) throw new Error("no run");
    useIterateStore.getState().start(result, {
      datasetId: a.datasetId,
      baseId: a.baseId,
      startedPath: a.startedPath,
      compareUrl: `/dashboard/develop/${a.datasetId}/compare/${a.then.tab ?? "data"}?runs=${a.baseId},${result.run_id}&baseline=${a.baseId}&from=iterate`,
      cause: null,
      noise: Boolean(result.script?.noise),
      guardFor: a.runId,
    });
  } catch {
    enqueueSnackbar(
      `${a.label} finished. Couldn't start the other ${a.then.rows.length} rows: use Run on the result banner.`,
      { variant: "warning" },
    );
  }
}

/** Test hook: stop the poller and clear the in-flight run. */
export function resetIterateStore() {
  stopPolling();
  useIterateStore.setState({
    opened: false,
    expanded: false,
    runId: null,
    drafts: {},
    entry: null,
    active: null,
    finished: {},
    lastFinished: null,
  });
}
