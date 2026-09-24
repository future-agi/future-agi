import { useEffect, useMemo, useRef, useState } from "react";

import { subTasksFor } from "src/api/simulate-environments/_fixtures/contract";
import { deriveUseCase, groupScenarios } from "./scenarios.constants";

/**
 * The server-pagination seam.
 *
 * Today the scenarios all live client-side in `envState.scenarios`, so this
 * runs the exact search/filter/group/sort the backend will run — over that
 * array, behind a simulated network delay — and returns one page at a time.
 * Tomorrow the body of `queryPage` is replaced by a `GET` to the list endpoint;
 * every caller and the whole selection model stay unchanged. That is the value
 * of routing the demo through this hook instead of slicing the array inline:
 * the seam is where the real API lands.
 *
 * The response shape is the contract the backend must honour:
 *   rows        — this page of scenarios
 *   total       — count of ALL rows matching the query (drives "select all N")
 *   groupCounts — { [groupId]: count } across the whole match, so a group
 *                 header can read "240 scenarios" even though the page holds 25
 *   page/pageCount
 *
 * Grouping is a server *sort* (rows arrive ordered by the group key) plus these
 * counts — you cannot bucket client-side once you only hold one page. Hiding a
 * group stays purely visual (it never back-fills a short page).
 */
export const PAGE_SIZE = 25;

// Run the query the backend will run. Pure, synchronous, and the only block
// that gets deleted when the real endpoint exists.
function queryPage(all, { search, filters, groupBy, env, page, limit }) {
  const q = (search || "").trim().toLowerCase();
  const matchesFilters = (r) => {
    const uc = deriveUseCase(r).id;
    if (filters.useCase?.length && !filters.useCase.includes(uc)) return false;
    if (filters.useCase_not?.length && filters.useCase_not.includes(uc)) return false;
    const persona = r?.persona?.name || null;
    if (filters.persona?.length && !filters.persona.includes(persona)) return false;
    if (filters.persona_not?.length && filters.persona_not.includes(persona)) return false;
    if (filters.subgoal?.length || filters.subgoal_not?.length) {
      const subgoals = subTasksFor(r, env).map((s) => s.label);
      if (filters.subgoal?.length && !filters.subgoal.some((s) => subgoals.includes(s))) return false;
      if (filters.subgoal_not?.length && filters.subgoal_not.some((s) => subgoals.includes(s))) return false;
    }
    return true;
  };

  const matched = all.filter((r) => {
    if (!matchesFilters(r)) return false;
    if (!q) return true;
    const hay = `${r.name || ""} ${r.summary || ""} ${r.title || ""} ${r.task || ""} ${r.useCase || ""}`.toLowerCase();
    return hay.includes(q);
  });

  // Sort by the group key so groups never split across a page boundary, then
  // page. groupScenarios gives the same bucketing/order the UI expects.
  const grouped = groupScenarios(matched, groupBy, env);
  const ordered = grouped.flatMap((g) => g.rows);
  const groupCounts = Object.fromEntries(grouped.map((g) => [g.id, g.rows.length]));

  const total = ordered.length;
  const pageCount = Math.max(1, Math.ceil(total / limit));
  const safePage = Math.min(page, pageCount - 1);
  const rows = ordered.slice(safePage * limit, safePage * limit + limit);

  return { rows, total, groupCounts, page: safePage, pageCount, limit };
}

/**
 * @param all      the full scenario array (the future backend's table)
 * @param query    { search, filters, groupBy, env, page }
 * @param options  { limit, latencyMs } — latencyMs>0 simulates the network so
 *                 the loading state is real in the POC; set 0 in tests.
 */
export default function useScenarioPage(all, query, { limit = PAGE_SIZE, latencyMs = 250 } = {}) {
  const { search, filters, groupBy, env, page } = query;
  // With no simulated latency (the real, un-inflated path) resolve the first
  // page synchronously so there's no empty loading flash and callers/tests see
  // rows on first render. The simulated-latency demo starts in a loading state.
  const [state, setState] = useState(() =>
    latencyMs > 0
      ? { rows: [], total: 0, groupCounts: {}, page: 0, pageCount: 1, limit, loading: true }
      : { ...queryPage(all, { search, filters, groupBy, env, page, limit }), loading: false },
  );

  // Serialise the query so the effect only refetches when it truly changes, and
  // so a stale in-flight page can be discarded when a newer one supersedes it.
  const key = JSON.stringify({ search, filters, groupBy, page, limit, n: all.length });
  const reqRef = useRef(0);

  useEffect(() => {
    const req = ++reqRef.current;
    setState((s) => ({ ...s, loading: true }));
    const compute = () => {
      if (req !== reqRef.current) return; // superseded by a newer query
      setState({ ...queryPage(all, { search, filters, groupBy, env, page, limit }), loading: false });
    };
    if (latencyMs > 0) {
      const t = setTimeout(compute, latencyMs);
      return () => clearTimeout(t);
    }
    compute();
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // The current page as a shape the views already render: a single "all" group
  // when ungrouped, or the group slices that fall on this page with their
  // whole-match counts stitched back in from groupCounts.
  const pageGroups = useMemo(() => {
    if (groupBy === "none") return [{ id: "all", label: null, rows: state.rows }];
    const bucketed = groupScenarios(state.rows, groupBy, env);
    return bucketed.map((g) => ({ ...g, totalInGroup: state.groupCounts[g.id] ?? g.rows.length }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.rows, groupBy]);

  const pageIds = useMemo(() => state.rows.map((r) => r.id).filter(Boolean), [state.rows]);

  return { ...state, pageGroups, pageIds };
}
