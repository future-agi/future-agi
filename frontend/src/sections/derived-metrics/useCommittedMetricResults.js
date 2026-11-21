import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const uuid = (value) =>
  typeof value === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

export class ResultIdentityError extends Error {
  constructor(mismatch) {
    super(
      "The committed result identity could not be verified. Refresh run status to retry.",
    );
    this.mismatch = mismatch;
  }
}

export function hasResultIdentity(page, resultId) {
  return uuid(resultId) && page?.result_id === resultId;
}

export function verifyResultIdentity(page, resultId) {
  if (!hasResultIdentity(page, resultId))
    throw new ResultIdentityError(uuid(resultId) && uuid(page?.result_id));
  return page;
}

/** Independent run/results reads may straddle a commit. Only a matched pair is
 * usable; a mismatching response must never enter the expected result's cache.
 */
export default function useCommittedMetricResults({
  scope,
  metricId,
  runId,
  run,
  refetchRun,
  rollup,
  after,
}) {
  const selected = run?.id === runId && run?.metric_id === metricId;
  const resultId = selected ? run.result_id : null;
  const query = useQuery({
    queryKey: ["metric-results", scope, runId, resultId, rollup, after],
    enabled: Boolean(runId) && uuid(resultId),
    queryFn: async ({ signal }) =>
      verifyResultIdentity(
        (
          await axios.get(endpoints.derivedMetrics.results(runId), {
            signal,
            params: { rollup, after, limit: 50, include_uncertainty: true },
          })
        ).data,
        resultId,
      ),
    // Identity failures are reconciled below, never blindly retried against the
    // same stale identity. Keep the application's one retry for other failures.
    retry: (count, error) =>
      !(error instanceof ResultIdentityError) && count < 1,
  });
  const candidate = useMemo(
    () =>
      selected && hasResultIdentity(query.data, resultId)
        ? { scope, metricId, runId, rollup, after, run, page: query.data }
        : null,
    [
      selected,
      query.data,
      resultId,
      scope,
      metricId,
      runId,
      rollup,
      after,
      run,
    ],
  );
  const [snapshot, setSnapshot] = useState(null);
  useEffect(() => {
    if (candidate) setSnapshot(candidate);
  }, [candidate]);
  const retained =
    snapshot?.scope === scope &&
    snapshot.metricId === metricId &&
    snapshot.runId === runId &&
    snapshot.rollup === rollup &&
    (snapshot.after === after ||
      (after === "" && snapshot.page.result_id !== resultId))
      ? snapshot
      : null;
  const matched = candidate || retained;

  // At most ONE automatic run reread until a new page is successfully verified.
  // A second race stops here; focus/reconnect/manual refresh still work normally.
  // Selection/scope changes get their own budget and cannot retain another run.
  const selection = `${scope}:${metricId}:${runId}`;
  const reconciliation = useRef({
    selection,
    attempted: false,
    verified: null,
  });
  useEffect(() => {
    if (reconciliation.current.selection !== selection)
      reconciliation.current = { selection, attempted: false, verified: null };
    const state = reconciliation.current;
    if (candidate && !query.isError && state.verified !== query.data) {
      state.verified = query.data;
      state.attempted = false;
    }
    const mismatch =
      query.error instanceof ResultIdentityError && query.error.mismatch;
    // A cursor is bound to its committed result. A commit before a cursor read
    // can be rejected by the backend before it has a page identity to return.
    const staleCursor = after && query.error?.statusCode === 400;
    if ((mismatch || staleCursor) && !state.attempted) {
      state.attempted = true;
      void refetchRun();
    }
  }, [
    selection,
    candidate,
    query.data,
    query.isError,
    query.error,
    after,
    refetchRun,
  ]);
  return {
    ...query,
    matched,
    pending: Boolean(runId) && (!candidate || query.isError || !selected),
  };
}
