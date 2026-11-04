import PropTypes from "prop-types";
import { Fragment, createElement, useEffect, useRef, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import BuildStreamNote from "../environments/BuildStreamNote";
import {
  stepLabel,
  versionBuildProgress,
  versionNewsOf,
} from "../_mock/versionBuild";
import { envLabelOf } from "../_mock/envVersionKinds";
import useVersionChange from "./useVersionChange";

/*
  A new environment version building after approved changes: what it's doing,
  step by step, above every tab. Progress is read against the clock, so it's
  the same after leaving and coming back. When it's done the strip goes and
  a note says so, with the scenarios to review.
*/
export default function VersionBuildStrip({ env, envState, patch, onGo }) {
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
  const label = build ? envLabelOf(env, envState, build.label) : "";

  useEffect(() => {
    if (!build || !prog?.done || finished.current === build.at) return;
    finished.current = build.at;
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
  }, [build, prog?.done, label, patch, enqueueSnackbar, closeSnackbar, onGo]);

  if (!build || prog.done) return null;
  const secs = Math.ceil(prog.leftMs / 1000);
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
            label={`Building environment ${label}`}
            count={prog.step + 1}
            total={build.steps.length}
            sx={{ mb: 0, flex: 1 }}
          />
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`Runs start once it's ready · about ${secs} s left`}
          </Typography>
        </Stack>
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
                    color: state === "next" ? "text.tertiary" : "text.primary",
                  }}
                >
                  {s}
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      </Box>
    </Box>
  );
}
VersionBuildStrip.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onGo: PropTypes.func,
};

/* The one line a tab shows while a new version writes its part — the
   first build's note, for the part that's new. */
const NOTE_WORDS = {
  scenarios: [
    "Scenarios from your files are written next",
    "Writing and checking scenarios from your files",
  ],
};
export function VersionChangeNote({ env, envState, kind }) {
  const { live } = useVersionChange(envState, kind);
  if (!live) return null;
  const [next, now] = NOTE_WORDS[kind];
  const label = envLabelOf(env, envState, live.label);
  return (
    <Box sx={{ px: 2, pt: 2 }}>
      <BuildStreamNote
        waiting={live.state === "next"}
        label={`${live.state === "next" ? next : now} — ${label}`}
        {...(live.state !== "next" && { count: live.shown, total: live.total })}
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
