import { useCallback, useEffect, useState } from "react";

export default function usePausedResults({ datasetId, view, liveData, isError }) {
  const [snapshot, setSnapshot] = useState(null);
  const pauseKey = JSON.stringify(view);
  const resultsBlocked = isError;

  // Invalidate storage as well as reads, so recovery cannot revive a snapshot.
  useEffect(() => setSnapshot(null), [datasetId, pauseKey, resultsBlocked]);

  const setPaused = useCallback((next) => {
    if (resultsBlocked) return;
    setSnapshot((previous) => {
      const current = previous?.datasetId === datasetId && previous.pauseKey === pauseKey
        ? previous.value : null;
      const value = typeof next === "function" ? next(current) : next;
      return value == null ? null : { datasetId, pauseKey, value };
    });
  }, [datasetId, pauseKey, resultsBlocked]);

  // A passive reset is too late for the first render of another dataset/view.
  const paused = !resultsBlocked && snapshot?.datasetId === datasetId && snapshot.pauseKey === pauseKey
    ? snapshot.value : null;
  const data = resultsBlocked ? null : (paused ?? liveData);

  return { data, paused, setPaused, resultsBlocked };
}
