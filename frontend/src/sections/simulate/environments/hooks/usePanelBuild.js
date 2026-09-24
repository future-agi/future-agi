import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { useRuntimePreflight } from "src/api/simulate-environments/useRuntimePreflight";
import { useBuildEnvironment } from "src/api/simulate-environments/environments";
import { useEnvironmentsStore } from "../store/useEnvironmentsStore";
import { prepareSourceForBuild } from "./prepareSourceForBuild";
import { clampParallelism } from "../parallelism.constants";

/**
 * The inline build machine shared by every source panel. A panel calls:
 *   - `runPreflight(source)` when the user clicks "Run preflight" — exchanges the
 *     source's secrets, then POSTs the real preflight; the result feeds
 *     <RuntimePreflight> via the returned `status`/`checks`/`state`.
 *   - `resetPreflight()` on any form edit — a green result for one source must
 *     never build a different one.
 *   - `commitBuild()` on "Build environment" (only enabled once `readyToSubmit`)
 *     — creates the real harness job and navigates straight to the environment
 *     workspace (`/environments/:jobId`), which hosts the build experience and
 *     then becomes the live workspace. There is no separate /build page.
 */
export default function usePanelBuild() {
  const navigate = useNavigate();
  const setDraft = useEnvironmentsStore((s) => s.setDraft);
  const preflight = useRuntimePreflight();
  const build = useBuildEnvironment();
  // The create POST is a real network step, so the CTA shows a pending state
  // through it and the guard blocks a second submit until it resolves.
  const [committing, setCommitting] = useState(false);
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
  const [parallelismInput, setParallelismInput] = useState("1");
  const parallelism = clampParallelism(parallelismInput);
  const parallelismEnabled = preflight.data?.parallelism_enabled !== false;

  const runPreflight = useCallback(
    async (source) => {
      const seq = (runSeq.current += 1);
      setPreparing(true);
      let result;
      try {
        result = await prepareSourceForBuild({ ...source, parallelism });
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
    [preflight, parallelism],
  );

  const resetPreflight = useCallback(() => {
    // Invalidate any in-flight exchange so its late resolve can't re-green a
    // source that has since changed.
    runSeq.current += 1;
    setPreparing(false);
    if (preflight.data || preflight.error || preflight.isPending) preflight.reset();
    setPrepared(null);
  }, [preflight]);

  const setParallelism = useCallback(
    (value) => {
      setParallelismInput(String(value ?? ""));
      resetPreflight();
    },
    [resetPreflight],
  );

  const commitBuild = useCallback(() => {
    if (!prepared || !preflight.data?.ready_to_submit || committing) return;
    const draft = parallelismEnabled ? prepared : { ...prepared, parallelism: 1 };
    // Keep the passing draft in the persisted slot so the panel form rehydrates
    // on a back-navigation; the create call below is what actually builds it.
    setDraft(draft);
    setCommitting(true);
    build.mutate(draft, {
      onSuccess: ({ envId, skipped }) => {
        // A skipped (un-preflightable) draft never reaches here — preflight
        // rejects it before ready_to_submit — but guard rather than route to an
        // id the workspace can't resolve.
        if (skipped || !envId) {
          setCommitting(false);
          enqueueSnackbar("This source can't be built yet.", { variant: "error" });
          return;
        }
        navigate(paths.dashboard.simulate.environments.detail(envId));
      },
      // A create failure (e.g. the sandbox is unavailable) must surface, not
      // silently strand the user on the form.
      onError: (error) => {
        setCommitting(false);
        enqueueSnackbar(errorMessage(error), { variant: "error" });
      },
    });
  }, [prepared, preflight.data, committing, parallelismEnabled, setDraft, build, navigate]);

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
    committing,
    parallelism,
    parallelismInput,
    parallelismEnabled,
    admittedParallelism: preflight.data?.effective_parallelism,
    setParallelism,
    error: preflight.error,
    runPreflight,
    resetPreflight,
    commitBuild,
  };
}
