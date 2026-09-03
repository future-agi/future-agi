import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

export const MONITOR_GRAPH_CLIENT_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

export const MONITOR_GRAPH_ERROR_MESSAGE =
  "Monitor graph data is temporarily unavailable. Please retry.";

export function monitorGraphRequestConfig({ signal, dateFilter } = {}) {
  return {
    signal,
    timeout: MONITOR_GRAPH_CLIENT_TIMEOUT_MS,
    params: {
      start_date: dateFilter?.dateFilter?.[0],
      end_date: dateFilter?.dateFilter?.[1],
    },
  };
}

export function keepPreviousMonitorGraphData(previousData) {
  return previousData;
}

export function monitorGraphDisplayState({
  latestData,
  retainedData,
  isError,
}) {
  const data = latestData !== undefined ? latestData : retainedData;

  return {
    data,
    showError: Boolean(isError),
    showGraph: !isError || data !== undefined,
  };
}
