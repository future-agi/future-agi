import { useMemo } from "react";

import { useHarnessScenarios } from "src/api/simulate-environments/scenariosHooks";

/**
 * The server-pagination seam.
 *
 * The list is read one page at a time from the harness scenarios endpoint via
 * useHarnessScenarios; search, filters, grouping and page are all query params,
 * so the server does the filtering, grouping, counting and searching and this
 * hook just reshapes the answer for the views. keepPreviousData holds the
 * current page on screen through a page/filter change, so only the very first
 * load shows the loading state.
 *
 * The return contract the views render against:
 *   rows        — this page of scenarios (mapped shape)
 *   total       — count of ALL rows matching the query (drives "select all N")
 *   pageCount   — total pages for the current query
 *   groupCounts — { [groupName]: whole-suite total } for the group headers
 *   pageGroups  — this page split into its consecutive group runs
 *   pageIds     — the visible row ids
 *   fields / groupings / groupBy / scenarioEditing — from the server response
 */
export const PAGE_SIZE = 25;

export default function useScenarioPage({
  jobId,
  search,
  filters,
  groupBy,
  ordering,
  page = 0,
  pageSize = PAGE_SIZE,
} = {}) {
  const query = useHarnessScenarios({ jobId, page, pageSize, search, groupBy, ordering, filters });
  const data = query.data;

  const rows = useMemo(() => data?.rows ?? [], [data]);
  const groups = useMemo(() => data?.groups ?? [], [data]);
  const total = data?.total ?? 0;
  const pageCount = data?.pageCount ?? 1;
  const appliedGroupBy = data?.groupBy ?? "";
  const groupings = data?.groupings ?? [];
  const fields = data?.fields ?? [];
  const scenarioEditing = data?.scenarioEditing ?? null;

  // Whole-suite total per section name, so a header reads "240 scenarios" even
  // when the page holds only a slice of that group.
  const groupCounts = useMemo(
    () => Object.fromEntries(groups.map((g) => [g.name, g.total])),
    [groups],
  );

  // The page split into the group runs the server ordered it into: a new
  // section wherever a row's `group` differs from the row above it. Ungrouped
  // is a single label-less section.
  const pageGroups = useMemo(() => {
    if (!appliedGroupBy) return [{ id: "all", label: null, rows }];
    const out = [];
    for (const row of rows) {
      const name = row.group ?? "";
      const last = out[out.length - 1];
      if (last && last.name === name) last.rows.push(row);
      else out.push({ id: `${appliedGroupBy}:${name}`, name, label: name, rows: [row] });
    }
    return out.map((g) => ({ ...g, totalInGroup: groupCounts[g.name] ?? g.rows.length }));
  }, [rows, appliedGroupBy, groupCounts]);

  const pageIds = useMemo(() => rows.map((r) => r.id).filter(Boolean), [rows]);

  return {
    rows,
    total,
    pageCount,
    limit: pageSize,
    loading: query.isPending,
    groupCounts,
    pageGroups,
    pageIds,
    fields,
    groupings,
    groupBy: appliedGroupBy,
    scenarioEditing,
  };
}
