import { useCallback, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { useRuntimePreflight } from "src/api/simulate-environments/useRuntimePreflight";
import { prepareSourceForBuild } from "src/sections/simulate/environments/hooks/prepareSourceForBuild";

export default function useConnectPreflight() {
  const preflight = useRuntimePreflight();
  const [preparing, setPreparing] = useState(false);
  const runSeq = useRef(0);

  const run = useCallback(
    async (source) => {
      const seq = (runSeq.current += 1);
      setPreparing(true);
      let prepared;
      try {
        prepared = await prepareSourceForBuild(source);
      } catch (e) {
        if (seq === runSeq.current) {
          setPreparing(false);
          enqueueSnackbar(errorMessage(e), { variant: "error" });
        }
        return;
      }
      if (seq !== runSeq.current) return;
      setPreparing(false);
      preflight.mutate({
        draft: prepared.draft,
        credentialValues: prepared.credentialValues,
      });
    },
    [preflight],
  );

  const reset = useCallback(() => {
    runSeq.current += 1;
    setPreparing(false);
    if (preflight.data || preflight.error || preflight.isPending) {
      preflight.reset();
    }
  }, [preflight]);

  const status =
    preparing || preflight.isPending
      ? "running"
      : preflight.error
        ? "error"
        : preflight.data
          ? "done"
          : "idle";

  return {
    status,
    result: preflight.data,
    error: preflight.error,
    ready: preflight.data?.ready_to_submit === true,
    run,
    reset,
  };
}
