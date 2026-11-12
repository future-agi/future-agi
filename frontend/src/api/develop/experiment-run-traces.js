import { useQuery } from "@tanstack/react-query";
import {
  experimentRunRead,
  experimentRunTracesList,
} from "src/generated/api-contracts/api";

// One experiment run (SRV-3) and its traces, joined by run id:
// GET /model-hub/experiment-runs/{run_id}/ and .../traces/. An SDK run's
// traces are stored in an Observe project, so the run page finds them by the
// run id the SDK writes on every span, never by project or project version.

export const experimentRunTraceKeys = {
  run: (runId) => ["experiment-run", runId],
  traces: (runId, limit, offset) => [
    "experiment-run",
    runId,
    "traces",
    limit,
    offset,
  ],
};

const resultOf = (response) => response?.data?.result ?? null;

export const useExperimentRunHeader = (runId, options = {}) =>
  useQuery({
    queryKey: experimentRunTraceKeys.run(runId),
    queryFn: ({ signal }) => experimentRunRead(runId, { signal }),
    select: resultOf,
    enabled: Boolean(runId),
    ...options,
  });

export const useExperimentRunTraces = (
  runId,
  { limit = 50, offset = 0 } = {},
  options = {},
) =>
  useQuery({
    queryKey: experimentRunTraceKeys.traces(runId, limit, offset),
    queryFn: ({ signal }) =>
      experimentRunTracesList(runId, { limit, offset }, { signal }),
    select: resultOf,
    enabled: Boolean(runId),
    ...options,
  });

// The header's ordinal, label and app_url are nullable (SRV-4): a run made
// before ordinals, or a deployment with no frontend URL, has none.
export const runTitle = (run) => {
  if (!run) return "";
  const name = run?.metadata?.name;
  const label = run?.label ?? (run?.ordinal ? `#${run.ordinal}` : null);
  return [label, name].filter(Boolean).join(" ") || run.id;
};

export const notJoinedReasonText = {
  not_found: "not in the span store yet",
  other_run: "its root span names another run",
  no_run_id: "its root span names no run",
};
