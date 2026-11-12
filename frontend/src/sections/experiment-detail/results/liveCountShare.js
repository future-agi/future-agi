// options-truth L2 finalize: one count on the experiment page. The header's
// `Running · 3/30` pill read the experiment poll while the grid, its run chip
// and `2/30 · about 60 s left` read the results summary: two polls out of
// phase, so the pill ran a row ahead of (or behind) the grid most of the
// time. The Data tab publishes the counts it shows; the header reads them
// while that tab is open and its own poll otherwise. Status words, errors
// and every action stay the header's.
import { useCallback, useLayoutEffect, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { create } from "zustand";
import {
  RESULTS_PAGE_SIZE, runResultsKey, runResultsParams,
} from "src/api/develop/run-results";

const COUNT_FIELDS = ["done", "total", "scored", "eta_s"];

/**
 * Pure: `{runId: {done, total, scored?, eta_s?}}` from the runs the grid
 * shows (each with its live `progress`). Only finite numbers are kept, so a
 * missing field never blanks the header's own value.
 * @param {object[]} runs
 * @returns {Record<string, object>}
 */
export function countsByRun(runs) {
  const out = {};
  (runs ?? []).forEach((r) => {
    const p = r?.progress;
    if (!r?.run_id || !p) return;
    const counts = {};
    COUNT_FIELDS.forEach((k) => {
      if (Number.isFinite(p[k])) counts[k] = p[k];
    });
    if (Number.isFinite(counts.done) && Number.isFinite(counts.total))
      out[r.run_id] = counts;
  });
  return out;
}

/**
 * Pure: the header's runs with the grid's counts. `status`, `errors` and
 * everything else stay the header's; a run the grid does not show keeps its
 * own progress.
 * @param {object[]} runs  the experiment poll's runs
 * @param {Record<string, object>|null} shared  countsByRun of the grid
 * @param {object|null} state  dataset/scope-tagged results availability
 * @param {string} datasetId  the header's current dataset
 */
export function withSharedCounts(runs, shared, state = null, datasetId = state?.datasetId) {
  const list = runs ?? [];
  if (state && state.datasetId !== datasetId) return list;
  // An empty override map means "no override", NOT "results unavailable".
  // Also mask examples: runProgressOf uses it when progress.total is absent.
  if (state?.blocked) return list.map((r) => ({
    ...r,
    examples: undefined,
    progress: {
      ...r.progress,
      ...Object.fromEntries(COUNT_FIELDS.map((field) => [field, undefined])),
    },
  }));
  if (!shared) return list;
  return list.map((r) => {
    const counts = shared[r?.run_id];
    if (!counts) return r;
    return { ...r, progress: { ...(r.progress ?? {}), ...counts } };
  });
}

/** Stable key of a counts map (publish only when a number moved). */
export const countsKey = (counts) =>
  Object.keys(counts ?? {})
    .sort()
    .map((id) =>
      [id, ...COUNT_FIELDS.map((k) => counts[id][k] ?? "")].join(":"),
    )
    .join("|");

export const useLiveCounts = create((set) => ({
  byExperiment: {},
  stateByExperiment: {},
  publish: (experimentId, counts, state = null) =>
    set((s) => {
      const shown = state?.blocked ? {} : counts;
      const previous = s.stateByExperiment[experimentId];
      if (experimentId in s.byExperiment &&
          countsKey(s.byExperiment[experimentId]) === countsKey(shown) &&
          previous?.datasetId === state?.datasetId &&
          previous?.scopeKey === state?.scopeKey &&
          Boolean(previous?.blocked) === Boolean(state?.blocked)) return s;
      return {
        byExperiment: { ...s.byExperiment, [experimentId]: shown },
        stateByExperiment: { ...s.stateByExperiment, [experimentId]: state },
      };
    }),
  clear: (experimentId, scopeKey) =>
    set((s) => {
      if (scopeKey != null && s.stateByExperiment[experimentId]?.scopeKey !== scopeKey) return s;
      if (!(experimentId in s.byExperiment) && !(experimentId in s.stateByExperiment)) return s;
      const next = { ...s.byExperiment };
      const states = { ...s.stateByExperiment };
      delete next[experimentId];
      delete states[experimentId];
      return { byExperiment: next, stateByExperiment: states };
    }),
}));

/** Read the current scope before either sibling's effects, including errors
 * cached before mount/navigation. This subscribes without fetching or writing
 * any store during render; the boolean snapshot is stable between changes.
 */
export function useResultsQueryBlocked(datasetId, view, enabled = true) {
  const cache = useQueryClient().getQueryCache();
  const queryKey = [...runResultsKey(datasetId), "grid", {
    ...runResultsParams(view), page: 0, page_size: RESULTS_PAGE_SIZE,
  }];
  const subscribe = useCallback((onChange) =>
    enabled ? cache.subscribe(onChange) : () => {}, [cache, enabled]);
  const snapshot = () => enabled && cache.find({ queryKey, exact: true })?.state.status === "error";
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Publish availability as well as counts, bound to the displayed query.
 * Query-cache notifications synchronously block established shared scopes.
 * The header also reads useResultsQueryBlocked for errors predating this
 * subscription. Layout publication and scoped cleanup never write in render.
 */
export function usePublishLiveCounts(experimentId, counts, { datasetId, view, blocked }) {
  const client = useQueryClient();
  const scopeKey = JSON.stringify([datasetId, view]);
  const key = countsKey(counts);
  useLayoutEffect(() => {
    if (!experimentId) return undefined;
    const cache = client.getQueryCache();
    const queryKey = [...runResultsKey(datasetId), "grid", {
      ...runResultsParams(view), page: 0, page_size: RESULTS_PAGE_SIZE,
    }];
    const currentQuery = () => cache.find({ queryKey, exact: true });
    const publish = () => useLiveCounts.getState().publish(experimentId, counts, {
      datasetId, scopeKey,
      blocked: Boolean(blocked) || currentQuery()?.state.status === "error",
    });
    const unsubscribe = cache.subscribe((event) => {
      if (event.type === "updated" && event.query === currentQuery()) publish();
    });
    publish();
    return unsubscribe;
    // view and counts are represented by their value keys, not poll identities.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, experimentId, datasetId, scopeKey, key, blocked]);
  useLayoutEffect(() => () => {
    if (experimentId) useLiveCounts.getState().clear(experimentId, scopeKey);
  }, [experimentId, scopeKey]);
}
