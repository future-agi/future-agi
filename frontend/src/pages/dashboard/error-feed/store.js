import { useMemo } from "react";
import { create } from "zustand";

/**
 * UI-only state for the Error Feed.
 *
 * Filters / sort / pagination are mirrored here so `ErrorFeedFilters` and
 * `ErrorFeedTable` share the same source of truth. Actual cluster data is
 * fetched via React Query hooks in `src/api/errorFeed/error-feed.js`.
 */
export const useErrorFeedStore = create((set, get) => ({
  // ── List view state ────────────────────────────────────────────────────
  searchQuery: "",
  selectedProject: "",
  selectedEnvironment: "",
  selectedStatus: "",
  selectedSeverity: "",
  selectedErrorType: "",
  selectedFixLayer: "",
  selectedSource: "",
  timeRange: "7",
  sortBy: "lastSeen",
  sortDir: "desc",
  page: 0,
  pageSize: 25,

  // ── Detail view state ──────────────────────────────────────────────────
  activeTab: "overview", // "overview" | "traces" | "trends" | "analyze"
  // Per-cluster selected trace id for the Overview list + sidebar sync.
  // { [clusterId]: traceId } — null / missing means "use cluster's latest".
  selectedTraceIdByCluster: {},

  // Whether the cluster-analysis accordion (ClusterHeadlineCard) is collapsed.
  // Global toggle kept in the store so the choice survives tab switches
  // (the card only mounts on the Overview tab and would otherwise reset).
  clusterAnalysisCollapsed: false,

  // ── Analyze tab thread state (per-cluster) ─────────────────────────────
  // Shape: { [clusterId]: { messages: [...], runState: 'idle'|'streaming'|'done', startedAt } }
  // Streamed live from the cluster-RCA agent over Falcon's WebSocket
  // (see clusterAnalyzeSocket.js); ephemeral per session.
  analyzeThreadsByCluster: {},
  // Set when the headline card's "Analyze cluster" button is clicked. The
  // Analyze tab consumes this flag on mount/visibility to auto-start a run,
  // then clears it. If the user opens the Analyze tab directly without
  // going through that button, the flag is absent and the tab shows an
  // empty state instead.
  analyzePendingStartByCluster: {},

  // ── Actions ────────────────────────────────────────────────────────────
  setSearchQuery: (v) => set({ searchQuery: v, page: 0 }),
  setSelectedProject: (v) => set({ selectedProject: v, page: 0 }),
  setSelectedEnvironment: (v) => set({ selectedEnvironment: v, page: 0 }),
  setSelectedStatus: (v) => set({ selectedStatus: v, page: 0 }),
  setSelectedSeverity: (v) => set({ selectedSeverity: v, page: 0 }),
  setSelectedErrorType: (v) => set({ selectedErrorType: v, page: 0 }),
  setSelectedFixLayer: (v) => set({ selectedFixLayer: v, page: 0 }),
  setSelectedSource: (v) => set({ selectedSource: v, page: 0 }),
  setTimeRange: (v) => set({ timeRange: v, page: 0 }),
  setSortBy: (col) => {
    const { sortBy, sortDir } = get();
    if (sortBy === col) {
      set({ sortDir: sortDir === "asc" ? "desc" : "asc" });
    } else {
      set({ sortBy: col, sortDir: "desc" });
    }
  },
  setPage: (p) => set({ page: p }),
  setActiveTab: (tab) => set({ activeTab: tab }),

  toggleClusterAnalysisCollapsed: () =>
    set((s) => ({ clusterAnalysisCollapsed: !s.clusterAnalysisCollapsed })),

  setSelectedTraceId: (clusterId, traceId) =>
    set((s) => ({
      selectedTraceIdByCluster: {
        ...s.selectedTraceIdByCluster,
        [clusterId]: traceId,
      },
    })),

  setAnalyzeThread: (clusterId, thread) =>
    set((s) => ({
      analyzeThreadsByCluster: {
        ...s.analyzeThreadsByCluster,
        [clusterId]: thread,
      },
    })),

  // Drops the thread for one cluster (not a full state reset — hence
  // "remove", not "clear").
  removeAnalyzeThread: (clusterId) =>
    set((s) => {
      const next = { ...s.analyzeThreadsByCluster };
      delete next[clusterId];
      return { analyzeThreadsByCluster: next };
    }),

  setAnalyzePendingStart: (clusterId, value) =>
    set((s) => ({
      analyzePendingStartByCluster: {
        ...s.analyzePendingStartByCluster,
        [clusterId]: value,
      },
    })),
  // Drops the pending-start flag for one cluster (removes the key rather
  // than resetting the whole map — hence "remove", not "clear").
  removeAnalyzePendingStart: (clusterId) =>
    set((s) => {
      const next = { ...s.analyzePendingStartByCluster };
      delete next[clusterId];
      return { analyzePendingStartByCluster: next };
    }),
}));

// ── Sort key mapping: frontend camelCase → backend snake_case ──
const SORT_KEY_MAP = {
  lastSeen: "last_seen",
  severity: "severity",
  firstSeen: "first_seen",
  occurrences: "error_count",
  traceCount: "unique_traces",
};

/**
 * Build backend API params from the current store state.
 * Each primitive is selected individually (stable subscriptions),
 * then the params object is memoized to avoid React Query refetch loops.
 */
export const useErrorFeedApiParams = () => {
  const selectedProject = useErrorFeedStore((s) => s.selectedProject);
  const searchQuery = useErrorFeedStore((s) => s.searchQuery);
  const selectedStatus = useErrorFeedStore((s) => s.selectedStatus);
  const selectedSeverity = useErrorFeedStore((s) => s.selectedSeverity);
  const selectedFixLayer = useErrorFeedStore((s) => s.selectedFixLayer);
  const selectedSource = useErrorFeedStore((s) => s.selectedSource);
  const selectedErrorType = useErrorFeedStore((s) => s.selectedErrorType);
  const timeRange = useErrorFeedStore((s) => s.timeRange);
  const sortBy = useErrorFeedStore((s) => s.sortBy);
  const sortDir = useErrorFeedStore((s) => s.sortDir);
  const page = useErrorFeedStore((s) => s.page);
  const pageSize = useErrorFeedStore((s) => s.pageSize);

  return useMemo(() => {
    const params = {};
    if (selectedProject) params.project_id = selectedProject;
    if (searchQuery?.trim()) params.search = searchQuery.trim();
    if (selectedStatus) params.status = selectedStatus;
    if (selectedSeverity) params.severity = selectedSeverity;
    if (selectedFixLayer) params.fix_layer = selectedFixLayer;
    if (selectedSource) params.source = selectedSource;
    if (selectedErrorType) params.issue_group = selectedErrorType;
    if (timeRange) params.time_range_days = Number(timeRange);

    params.sort_by = SORT_KEY_MAP[sortBy] || "last_seen";
    params.sort_dir = sortDir || "desc";
    params.limit = pageSize;
    params.offset = page * pageSize;

    return params;
  }, [
    selectedProject,
    searchQuery,
    selectedStatus,
    selectedSeverity,
    selectedFixLayer,
    selectedSource,
    selectedErrorType,
    timeRange,
    sortBy,
    sortDir,
    page,
    pageSize,
  ]);
};
