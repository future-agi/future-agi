import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/**
 * The Debug-failures data source.
 *
 * `POST …/debug-analysis/` queues one Omega job per call of a completed run.
 * The run's evals decide which goals broke on which calls; Omega explains how,
 * and grouping clusters its explanations into the ways each goal broke. Every
 * count arrives computed, so a card's number always matches its View link.
 */

export const DEBUG_ANALYSIS_WORKING_STATES = ["pending", "running"];

// Stop waiting for clusters after this long: a report whose grouping never
// settles (e.g. grouping disabled) must not be polled or promised forever.
const GROUPING_WAIT_MS = 10 * 60 * 1000;

const groupingIsLive = (report, now = Date.now()) =>
  report?.grouping_status === "pending" &&
  now - Date.parse(report?.recorded_at ?? 0) < GROUPING_WAIT_MS;

// The eval's own pass condition, e.g. `the agent says exactly "Hi there…"`.
const passWhen = (criteria) =>
  String(criteria ?? "")
    .match(/^Pass when:\s*(.+)$/m)?.[1]
    ?.trim() || null;

const mapWay = (way) => ({
  id: way.id,
  title: way.title,
  phrase: way.phrase,
  callIds: way.call_ids ?? [],
});

export function mapDebugAnalysis(raw) {
  const status = raw?.status ?? "not_requested";
  const report = raw?.report ?? null;
  const summary = raw?.summary ?? null;
  return {
    status,
    isWorking: DEBUG_ANALYSIS_WORKING_STATES.includes(status),
    groupingPending: groupingIsLive(report),
    errorMessage: raw?.error_message ?? null,
    summary: summary && {
      measuredCalls: summary.measured_call_count,
      brokenGoals: summary.broken_goal_count,
      brokenCalls: summary.broken_call_count,
      oneOffs: summary.one_off_count,
      excludedCallIds: summary.excluded_call_ids ?? [],
      unanalyzedCallIds: summary.unanalyzed_call_ids ?? [],
    },
    goals: (raw?.goals ?? []).map((goal) => ({
      goal: goal.goal,
      label: goal.label,
      criteria: goal.criteria,
      expected: passWhen(goal.criteria),
      brokenCallIds: goal.broken_call_ids ?? [],
      testedCalls: goal.tested_call_count,
      ways: (goal.ways ?? []).map(mapWay),
      unexplainedCallIds: goal.unexplained_call_ids ?? [],
    })),
    oneOffs: (raw?.one_offs ?? []).map(mapWay),
  };
}

const CALL_ID =
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;

/**
 * Omega cites calls by id inside its prose. Swap each id for the call's
 * scenario label so a one-off never shows a raw uuid.
 *
 * @param {ReturnType<typeof mapDebugAnalysis>} analysis
 * @param {Array<{ id: string, scenario: string }>} calls  Rows of `useRunCalls`.
 */
export function withCallContext(analysis, calls = []) {
  const byId = new Map(
    calls.map((call) => [String(call.id).toLowerCase(), call]),
  );
  const label = (text) =>
    String(text || "").replace(CALL_ID, (id) => {
      const call = byId.get(id.toLowerCase());
      return call?.scenario ? `"${call.scenario}"` : `${id.slice(0, 8)}…`;
    });
  return {
    ...analysis,
    oneOffs: analysis.oneOffs.map((way) => ({
      ...way,
      title: label(way.title),
    })),
  };
}

/**
 * @param {?string} executionId
 */
export function useDebugAnalysis(executionId) {
  const queryClient = useQueryClient();
  const queryKey = ["debug-analysis", executionId];
  const query = useQuery({
    queryKey,
    queryFn: () =>
      axios
        .get(endpoints.testExecutions.debugAnalysis(executionId))
        .then((res) => res.data),
    enabled: !!executionId,
    // Findings arrive with the report; clusters land after grouping, so keep
    // polling until both have settled.
    refetchInterval: ({ state }) => {
      const data = state?.data;
      if (DEBUG_ANALYSIS_WORKING_STATES.includes(data?.status)) return 3000;
      return groupingIsLive(data?.report) ? 5000 : false;
    },
    refetchOnWindowFocus: false,
  });

  const requestMutation = useMutation({
    mutationFn: () =>
      axios
        // The route's contract is an explicit empty object, not a missing body.
        .post(endpoints.testExecutions.debugAnalysis(executionId), {})
        .then((res) => res.data),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  });

  const analysis = useMemo(() => mapDebugAnalysis(query.data), [query.data]);

  return {
    analysis,
    isLoading: !!executionId && query.isPending,
    loadError: query.error,
    request: requestMutation.mutate,
    isRequesting: requestMutation.isPending,
    requestError: requestMutation.error,
  };
}
