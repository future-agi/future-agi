import PropTypes from "prop-types";
import { Fragment, createElement, useEffect, useRef, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import BuildStreamNote from "../environments/BuildStreamNote";
import {
  BUILD_PHASES,
  BUILD_STEP_TOTAL,
  phaseOfStep,
  stepLabel,
  versionBuildProgress,
  versionNewsOf,
} from "../_mock/versionBuild";
import { buildStopOf, isBuildStopped } from "../_mock/buildStop";
import BuildStopStrip from "./BuildStopStrip";
import { envLabelOf } from "../_mock/envVersionKinds";
import { checksStateOf, runChecksPatch } from "../_mock/release";
import { designSourcesSummary } from "../_mock/designSources";
import useVersionChange from "./useVersionChange";

/*
  A new environment version building after approved changes: what it's doing,
  step by step, above every tab. Progress is read against the clock, so it's
  the same after leaving and coming back. When it's done the strip goes and
  a note says so, with the scenarios to review. A first build that stops
  part-way stays here as BuildStopStrip until it's tried again.
*/
export default function VersionBuildStrip({
  env,
  envState,
  patch,
  onGo,
  onRetry,
  onFix,
}) {
  const build = envState.versionBuild || null;
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  const [now, setNow] = useState(Date.now());
  const finished = useRef(null);
  useEffect(() => {
    if (!build) return undefined;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [build]);

  const prog = versionBuildProgress(build, now);
  /* The moment a first build reaches the step it stops at, the stop is
     stored — the header, the run button and the list all read it. */
  useEffect(() => {
    if (!build?.first || !prog?.stopped || isBuildStopped(build)) return;
    patch?.({ versionBuild: { ...build, stoppedAt: Date.now() } });
  }, [build, prog?.stopped, patch]);
  /* Every build that ends on a release saves itself when it's clean and
     nothing is left to answer — the same rule as useReleaseAutoSave. */
  const savesItself =
    !!build?.autoSave ||
    (!!env?.builtFrom?.releaseStep &&
      !env?.autoSaveUndone &&
      !designSourcesSummary(env, envState || {}).needsInput);
  const label = build
    ? build.shownLabel || envLabelOf(env, envState, build.label)
    : "";

  useEffect(() => {
    if (!build || !prog?.done || finished.current === build.at) return;
    finished.current = build.at;
    /* A first build ran the release checks as its last step: their results
       land with it, and the next thing to do is Review and save. */
    if (build.first) {
      const checked = {
        ...runChecksPatch(env, envState),
        releaseChecksRunning: null,
      };
      patch?.({ versionBuild: null, versionNews: null, ...checked });
      const passed = checksStateOf(env, { ...envState, ...checked }).ready;
      /* A clean build saves itself (useReleaseAutoSave); the page says so
         with its own notice — this one would be a second, contradicting it. */
      if (passed && savesItself) return;
      enqueueSnackbar(
        passed
          ? `Environment ${label} is built and all five checks passed. Review and save it.`
          : `Environment ${label} is built, but a release check failed. See Overview.`,
        {
          variant: passed ? "success" : "warning",
          autoHideDuration: 8000,
          action: (snack) =>
            createElement(
              Button,
              {
                size: "small",
                color: "inherit",
                onClick: () => {
                  closeSnackbar(snack);
                  onGo?.("overview");
                },
              },
              passed ? "Review and save" : "See the checks",
            ),
        },
      );
      return;
    }
    /* What changed keeps a "New in vN" label until its tab is looked at. */
    patch?.({ versionBuild: null, versionNews: versionNewsOf(build) });
    enqueueSnackbar(`Environment ${label} is ready`, {
      variant: "success",
      autoHideDuration: 8000,
      action: (snack) => {
        const btn = (text, tab) =>
          createElement(
            Button,
            {
              key: tab,
              size: "small",
              color: "inherit",
              onClick: () => {
                closeSnackbar(snack);
                onGo?.(tab);
              },
            },
            text,
          );
        /* What changed between the versions is on History. */
        return createElement(
          Fragment,
          null,
          build.scenarios > 0 &&
            btn(
              `Review ${build.scenarios} scenario${build.scenarios === 1 ? "" : "s"}`,
              "scenarios",
            ),
          btn("See what changed", "history"),
        );
      },
    });
  }, [build, prog?.done, label, patch, enqueueSnackbar, closeSnackbar, onGo]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!build || prog.done) return null;
  if (build.first && (prog.stopped || isBuildStopped(build))) {
    const view = buildStopOf(
      isBuildStopped(build) ? build : { ...build, stoppedAt: now },
      { scenarios: envState.scenarios || [], agent: envState.agent },
    );
    return view ? (
      <BuildStopStrip view={view} onRetry={onRetry} onFix={onFix} />
    ) : null;
  }
  const secs = Math.ceil(prog.leftMs / 1000);
  /* A first build names its phase and the engine's own step number;
     older stored builds have neither. */
  const stepNow = build.steps[prog.step];
  const phase = BUILD_PHASES.find((p) => p.id === phaseOfStep(stepNow));
  const numbered = build.first && typeof stepNow?.no === "number";
  return (
    <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: "background.paper",
          px: 2,
          py: 1.5,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <BuildStreamNote
            label={
              build.first
                ? /* One line: the tabs show each part as it's written, so
                     only where the whole build is says anything new. */
                  numbered && phase
                  ? `Building environment ${label} · ${phase.label} · ${stepNow.label}`
                  : `Building environment ${label} · ${stepLabel(stepNow)}`
                : `Building environment ${label}`
            }
            count={numbered ? stepNow.no : prog.step + 1}
            total={numbered ? BUILD_STEP_TOTAL : build.steps.length}
            sx={{ mb: 0, flex: 1 }}
          />
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {build.first
              ? savesItself
                ? `Saved as v1 when the checks pass · about ${secs} s left`
                : `Then you review and save it · about ${secs} s left`
              : `Runs start once it's ready · about ${secs} s left`}
          </Typography>
        </Stack>
        {!build.first && (
          <Stack spacing={0.5} sx={{ mt: 1, pl: 0.5 }}>
            {build.steps.map((step, i) => {
              const s = stepLabel(step);
              const state =
                i < prog.step ? "done" : i === prog.step ? "now" : "next";
              return (
                <Stack key={s} direction="row" alignItems="center" spacing={1}>
                  <Iconify
                    icon={
                      state === "done"
                        ? "solar:check-circle-linear"
                        : state === "now"
                          ? "solar:refresh-linear"
                          : "solar:clock-circle-linear"
                    }
                    width={14}
                    sx={{
                      flexShrink: 0,
                      color:
                        state === "done"
                          ? "status.success.fg"
                          : state === "now"
                            ? "text.primary"
                            : "text.tertiary",
                    }}
                  />
                  <Typography
                    sx={{
                      typography: "s2",
                      color:
                        state === "next" ? "text.tertiary" : "text.primary",
                    }}
                  >
                    {s}
                  </Typography>
                </Stack>
              );
            })}
          </Stack>
        )}
      </Box>
    </Box>
  );
}
VersionBuildStrip.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  /* A stopped first build: Try again, and the change that can fix it. */
  onRetry: PropTypes.func,
  onFix: PropTypes.func,
};

/* The one line a tab shows while a new version writes its part — the
   first build's note, for the part that's new. */
const NOTE_WORDS = {
  scenarios: [
    "Scenarios from your files are written next",
    "Writing and checking scenarios from your files",
    "The build stopped before these scenarios were written and checked — try again from the strip above",
  ],
};
export function VersionChangeNote({ env, envState, kind }) {
  const { live } = useVersionChange(envState, kind);
  if (!live) return null;
  const [next, now, stopped] = NOTE_WORDS[kind];
  const label = envLabelOf(env, envState, live.label);
  return (
    <Box sx={{ px: 2, pt: 2 }}>
      <BuildStreamNote
        waiting={live.state === "next"}
        stopped={live.state === "stopped"}
        label={
          live.state === "stopped"
            ? stopped
            : `${live.state === "next" ? next : now} — ${label}`
        }
        {...(live.state === "now" && { count: live.shown, total: live.total })}
        sx={{ mb: 0 }}
      />
    </Box>
  );
}
VersionChangeNote.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  kind: PropTypes.oneOf(["scenarios"]).isRequired,
};
