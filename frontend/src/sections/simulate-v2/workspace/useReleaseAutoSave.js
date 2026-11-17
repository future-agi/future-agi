import { createElement, useEffect } from "react";
import { Button } from "@mui/material";
import { useSnackbar } from "notistack";
import { checksStateOf, runChecksPatch } from "../_mock/release";
import { designSourcesSummary } from "../_mock/designSources";

/*
  One ending for every build — scratch, connect-and-build, an agent's own
  build and a ready environment: a clean build (the five release checks pass
  and nothing is left to answer) saves itself as v1, with Undo. Anything else
  stops at Built · not saved, for Review and save. Undo means "let me review
  it": it isn't saved again.

  Used by the environment page and by the build page an agent's build runs
  on, so the build ends the same way wherever the person is watching it.
  `runChecks`: run the release checks here once the build is ready (the
  environment page runs them itself).
*/
export default function useReleaseAutoSave({
  env,
  envState,
  dispatch,
  hydrated,
  runChecks = false,
}) {
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  const built =
    !!env && !!env.builtFrom?.releaseStep && env.buildStatus === "ready";
  const checks = env ? checksStateOf(env, envState || {}) : null;
  const unchecked =
    built && !env.releaseSavedAt && (!envState?.releaseChecks || checks?.stale);

  /* The checks, as the build's last step, where nothing else runs them. */
  useEffect(() => {
    if (!runChecks || !hydrated || !unchecked) return undefined;
    const t = setTimeout(
      () =>
        dispatch({
          type: "patchEnvState",
          envId: env.id,
          patch: runChecksPatch(env, envState || {}),
        }),
      1600,
    );
    return () => clearTimeout(t);
  }, [runChecks, hydrated, unchecked, env?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const ready = !!checks?.ready;
  const versionBuilding = !!envState?.versionBuild;
  useEffect(() => {
    if (!hydrated || !built || !ready) return;
    if (env.releaseSavedAt || env.autoSaveUndone || versionBuilding) return;
    if (designSourcesSummary(env, envState || {}).needsInput) return;
    dispatch({
      type: "patchEnvironment",
      envId: env.id,
      patch: { releaseSavedAt: new Date().toISOString(), autoSaved: true },
    });
    enqueueSnackbar(
      "Saved as environment v1 — the build matches its design and all five checks passed. Private to this workspace.",
      {
        variant: "success",
        autoHideDuration: 8000,
        action: (snack) =>
          createElement(
            Button,
            {
              size: "small",
              color: "inherit",
              onClick: () => {
                closeSnackbar(snack);
                dispatch({
                  type: "patchEnvironment",
                  envId: env.id,
                  patch: {
                    releaseSavedAt: null,
                    autoSaved: false,
                    autoSaveUndone: true,
                  },
                });
              },
            },
            "Undo",
          ),
      },
    );
  }, [env?.id, built, env?.releaseSavedAt, ready, versionBuilding, hydrated]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Built but not saved — the checks are still running, one failed, or
     something needs an answer: the header says so. */
  return { unsaved: built && !env.releaseSavedAt, checking: unchecked };
}
