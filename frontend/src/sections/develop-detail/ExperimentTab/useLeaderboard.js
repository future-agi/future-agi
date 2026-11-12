// The leaderboard's view: URL params (q, group, baseline, filters, version,
// agg; §2.3, written with replace), the R1 read and the client-side search
// and filters. The toolbar and the grid each call it; React Query shares
// the request and the URL keeps them in step.
import { useCallback, useMemo } from "react";
import { useParams } from "react-router";
import { useUrlState } from "src/routes/hooks/use-url-state";
import { useDatasetRuns } from "src/api/develop/runs";
import { pollIntervalFor } from "src/sections/common/Runs/runStatus";
import { runMatches } from "src/sections/common/Runs/runMatch";
import { usePendingDeletes } from "./useLeaderboardActions";
import {
  filterRuns,
  isCompleteFilter,
  normalizeGroup,
} from "./leaderboardModel";
import { EVERY_ALL, parseEvery, searchRuns } from "./attemptsModel";

/** URL keys the Experiments tab owns (dropped when the tab unmounts). */
export const LEADERBOARD_URL_KEYS = [
  "q",
  "group",
  "baseline",
  "filters",
  "version",
  "agg",
  // R3 (P3): `charts=1|0` overrides the mode default (Simple hides charts).
  "charts",
  // W1-1 kept runs: `every=all` or the configs whose earlier runs show.
  "every",
];

const asText = (v) => (v == null ? "" : String(v));

export function useLeaderboardView() {
  const { dataset } = useParams();
  const [qRaw, setQRaw, removeQ] = useUrlState("q", "");
  const [groupRaw, setGroupRaw, removeGroup] = useUrlState(
    "group",
    "experiment",
  );
  const [baselineRaw, setBaselineParam, removeBaselineParam] = useUrlState(
    "baseline",
    "",
  );
  const [filtersRaw, setFiltersParam, removeFiltersParam] = useUrlState(
    "filters",
    [],
  );
  const [versionRaw, setVersionRaw, removeVersion] = useUrlState(
    "version",
    "all",
  );
  const [aggRaw, setAggRaw, removeAgg] = useUrlState("agg", "avg");
  const [chartsRaw, setChartsRaw, removeCharts] = useUrlState("charts", "");
  const [everyRaw, setEveryRaw, removeEvery] = useUrlState("every", "");
  const setChartsParam = useCallback(
    (v) =>
      v === true || v === false ? setChartsRaw(v ? "1" : "0") : removeCharts(),
    [setChartsRaw, removeCharts],
  );
  // Defaults leave the URL clean; only a changed view is written (replace).
  const setQ = useCallback(
    (v) => (v ? setQRaw(v) : removeQ()),
    [setQRaw, removeQ],
  );
  const setGroup = useCallback(
    (v) => (v && v !== "experiment" ? setGroupRaw(v) : removeGroup()),
    [setGroupRaw, removeGroup],
  );
  const setVersion = useCallback(
    (v) => (v && v !== "all" ? setVersionRaw(String(v)) : removeVersion()),
    [setVersionRaw, removeVersion],
  );
  const setAgg = useCallback(
    (v) => (v === "median" ? setAggRaw(v) : removeAgg()),
    [setAggRaw, removeAgg],
  );

  const q = asText(qRaw);
  const group = normalizeGroup(asText(groupRaw));
  const baselineParam = asText(baselineRaw);
  const version = asText(versionRaw) || "all";
  const agg = asText(aggRaw) === "median" ? "median" : "avg";
  const filters = useMemo(
    () => (Array.isArray(filtersRaw) ? filtersRaw : []),
    [filtersRaw],
  );

  // R4 (lane Rb, D26): `config` too, so names carry the agent and version
  // (agentVersionOf) and the provenance line reads the run's config.
  const query = useDatasetRuns(dataset, {
    include: "metrics,config",
    baseline: baselineParam || undefined,
    versionId: version && version !== "all" ? String(version) : undefined,
    refetchInterval: (q) =>
      pollIntervalFor(q.state.data?.data?.result?.results ?? []),
    refetchOnWindowFocus: false,
    placeholderData: (prev) => prev,
  });
  const data = query.data;

  const metricDefs = useMemo(() => data?.metric_defs ?? [], [data]);
  const defsByKey = useMemo(
    () => Object.fromEntries(metricDefs.map((d) => [d.key, d])),
    [metricDefs],
  );
  // EL#6: experiments in their Undo window are gone from every view.
  const hidden = usePendingDeletes((st) => st.byDataset[dataset]);
  const allRuns = useMemo(() => {
    const list = data?.results ?? [];
    return hidden?.length
      ? list.filter((r) => !hidden.includes(r.experiment_id))
      : list;
  }, [data, hidden]);
  const activeFilters = useMemo(
    () => filters.filter(isCompleteFilter),
    [filters],
  );
  // W1-1 kept runs: the newest run of each config, unless its chip or the
  // View switch shows every run.
  const every = asText(everyRaw);
  const runs = useMemo(
    () =>
      filterRuns(
        searchRuns(allRuns, every, q, runMatches),
        activeFilters,
        defsByKey,
      ),
    [allRuns, every, q, activeFilters, defsByKey],
  );
  const everyAll = parseEvery(every).all;
  const setEveryAll = useCallback(
    (on) => (on ? setEveryRaw(EVERY_ALL) : removeEvery()),
    [setEveryRaw, removeEvery],
  );

  const baseline = data?.baseline ?? null;
  const baselineRun = data?.baseline_run ?? null;

  const setFilters = useCallback(
    (next) => {
      const value = typeof next === "function" ? next(filters) : next;
      if (!value?.length) removeFiltersParam();
      else setFiltersParam(value);
    },
    [filters, setFiltersParam, removeFiltersParam],
  );

  const setBaseline = useCallback(
    (runId) => {
      if (!runId || runId === data?.pinned_run_id) removeBaselineParam();
      else setBaselineParam(runId);
    },
    [data?.pinned_run_id, setBaselineParam, removeBaselineParam],
  );

  return {
    datasetId: dataset,
    query,
    q,
    setQ,
    group,
    setGroup,
    version,
    setVersion,
    agg,
    setAgg,
    filters,
    activeFilters,
    setFilters,
    baseline,
    baselineRun,
    pinnedRunId: data?.pinned_run_id ?? null,
    // IT-R4 G2 P8: voice calls by caller and scenario.
    rowLabels: data?.row_labels ?? null,
    setBaseline,
    metricDefs,
    defsByKey,
    allRuns,
    runs,
    versions: data?.versions ?? [],
    // "1" / "0" / "" (mode default)
    chartsParam: asText(chartsRaw),
    setChartsParam,
    isFiltered: Boolean(q) || activeFilters.length > 0,
    everyAll,
    setEveryAll,
  };
}
