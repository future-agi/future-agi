import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { useRuntimePreflight } from "src/api/simulate-environments/useRuntimePreflight";
import { useEnvironmentsStore } from "../store/useEnvironmentsStore";
import { prepareSourceForBuild } from "./prepareSourceForBuild";

/**
 * The inline build machine shared by every source panel. A panel calls:
 *   - `runPreflight(source)` when the user clicks "Run preflight" — exchanges the
 *     source's secrets, then POSTs the real preflight; the result feeds
 *     <RuntimePreflight> via the returned `status`/`checks`/`state`.
 *   - `resetPreflight()` on any form edit — a green result for one source must
 *     never build a different one.
 *   - `commitBuild()` on "Build environment" (only enabled once `readyToSubmit`)
 *     — stages the passing draft as a one-shot ticket and navigates to /build.
 */
export default function usePanelBuild() {
  const navigate = useNavigate();
  const beginBuild = useEnvironmentsStore((s) => s.beginBuild);
  const preflight = useRuntimePreflight();
  // The redacted + exchanged draft of the last successful run — reused verbatim
  // at commit so the built job is the one that passed preflight.
  const [prepared, setPrepared] = useState(null);
  // The secret exchange inside prepareSourceForBuild is a real network call, so
  // there is a window between the click and the mutation. `preparing` disables
  // the trigger through it (no double-exchange); `runSeq` supersedes a run whose
  // exchange is still in flight when the user edits or re-runs — without it a
  // stale exchange would resolve and mutate the source the user just changed.
  const [preparing, setPreparing] = useState(false);
  const runSeq = useRef(0);

  const runPreflight = useCallback(
    async (source) => {
      const seq = (runSeq.current += 1);
      setPreparing(true);
      let result;
      try {
        result = await prepareSourceForBuild(source);
      } catch (e) {
        if (seq === runSeq.current) {
          setPreparing(false);
          enqueueSnackbar(errorMessage(e), { variant: "error" });
        }
        return;
      }
      // Superseded by an edit (resetPreflight) or a newer run while we exchanged.
      if (seq !== runSeq.current) return;
      setPreparing(false);
      // Only the redacted draft is staged for build; the raw credentialValues
      // ride the preflight request (write-only probe) and are never persisted.
      setPrepared(result.draft);
      preflight.mutate({ draft: result.draft, credentialValues: result.credentialValues });
    },
    [preflight],
  );

  const resetPreflight = useCallback(() => {
    // Invalidate any in-flight exchange so its late resolve can't re-green a
    // source that has since changed.
    runSeq.current += 1;
    setPreparing(false);
    if (preflight.data || preflight.error || preflight.isPending) preflight.reset();
    setPrepared(null);
  }, [preflight]);

  const commitBuild = useCallback(() => {
    if (!prepared || !preflight.data?.ready_to_submit) return;
    beginBuild({ draft: prepared, preflight: preflight.data });
    navigate(paths.dashboard.simulate.environments.build);
  }, [prepared, preflight.data, beginBuild, navigate]);

  const status = preparing || preflight.isPending
    ? "running"
    : preflight.error
      ? "error"
      : preflight.data
        ? "done"
        : "idle";

  return {
    status,
    checks: preflight.data?.checks,
    state: preflight.data?.state,
    readyToSubmit: !!preflight.data?.ready_to_submit,
    error: preflight.error,
    runPreflight,
    resetPreflight,
    commitBuild,
  };
}
