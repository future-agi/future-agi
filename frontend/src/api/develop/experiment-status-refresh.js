import { experimentReadPolicy } from "./experiment-read-policy";

const PENDING_STATUSES = new Set(["QUEUED", "RUNNING"]);

export const isExperimentPending = (status) =>
  PENDING_STATUSES.has(String(status ?? "").toUpperCase());

// Delay before the next status read while an execution is pending: it starts
// at `first` and doubles after each read, up to `max`.
export const STATUS_REFRESH_MS = Object.freeze({ first: 2_000, max: 15_000 });

// The query's dataUpdateCount when its current pending streak began.
const pendingSince = new WeakMap();

/**
 * refetchInterval for the experiment detail query (["experiment", id]). Reads
 * again only while the execution is queued or running, backing off between
 * reads; a terminal status or a failed read stops it. After a failure,
 * recovery is a deliberate refetch, as in experimentReadPolicy.
 */
export function experimentStatusRefreshInterval(query) {
  const { data, error, dataUpdateCount } = query.state;
  if (error || !isExperimentPending(data?.data?.result?.status)) {
    pendingSince.delete(query);
    return false;
  }
  if (!pendingSince.has(query)) pendingSince.set(query, dataUpdateCount);
  const reads = dataUpdateCount - pendingSince.get(query);
  return Math.min(STATUS_REFRESH_MS.first * 2 ** reads, STATUS_REFRESH_MS.max);
}

export const experimentStatusQueryOptions = Object.freeze({
  ...experimentReadPolicy,
  refetchInterval: experimentStatusRefreshInterval,
});
