import { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { useExperimentDetail } from "src/api/develop/runs";
import { isMissingExperimentError, useRunTray } from "./runTrayStore";

/** Poll one experiment and share its read state with both tray presentations. */
export default function RunTrayWatcher({
  experimentId,
  runIds,
  onRuns,
  onMissing,
}) {
  const setCheck = useRunTray((s) => s.setCheck);
  const lastErrorWasMissing = useRef(false);
  const {
    data,
    error,
    errorUpdatedAt = 0,
    dataUpdatedAt = 0,
    isFetching,
    refetch,
  } = useExperimentDetail(experimentId, {
    refetchInterval: (q) =>
      isMissingExperimentError(q?.state?.error) ? false : 4000,
    retry: (n, err) => !isMissingExperimentError(err) && n < 2,
    meta: { errorHandled: true },
  });
  // A cold-cache fetch clears error, but not its timestamp. Keep the failed
  // read visible through polling/refocus until a newer successful read.
  const readFailed = Boolean(error) || errorUpdatedAt > dataUpdatedAt;
  const missing =
    readFailed &&
    (error ? isMissingExperimentError(error) : lastErrorWasMissing.current);
  useEffect(() => {
    if (error) lastErrorWasMissing.current = isMissingExperimentError(error);
  }, [error]);

  useEffect(() => {
    setCheck(experimentId, {
      failed: readFailed && !missing,
      fetching: Boolean(isFetching),
      retry: refetch,
    });
  }, [experimentId, readFailed, missing, isFetching, refetch, setCheck]);
  useEffect(() => () => setCheck(experimentId, null), [experimentId, setCheck]);
  useEffect(() => {
    if (missing) onMissing(experimentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missing, experimentId]);

  const key = (data?.runs ?? [])
    .filter((r) => runIds.includes(r.run_id))
    .map((r) => `${r.run_id}:${r.status}:${r.progress?.done}`)
    .join("|");
  useEffect(() => {
    // A failed refresh can retain cached data, including a terminal status.
    if (!readFailed && data?.runs)
      onRuns(data.runs.filter((r) => runIds.includes(r.run_id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, readFailed]);
  return null;
}

RunTrayWatcher.propTypes = {
  experimentId: PropTypes.string.isRequired,
  runIds: PropTypes.array.isRequired,
  onRuns: PropTypes.func.isRequired,
  onMissing: PropTypes.func.isRequired,
};
