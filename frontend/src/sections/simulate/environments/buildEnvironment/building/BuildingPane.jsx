import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Tab, Typography } from "@mui/material";
import { CustomTabs } from "src/components/tabs/tabs";

import { ENV_TABS_SX } from "../../environmentOptions";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "../../workspace/overview/overview.constants";
import WorkspacePanels from "../../workspace/WorkspacePanels";
import { gapsByTab, counts as countsFor } from "../../workspace/helpers/workspaceGaps";
import { DERIVING_LABEL, BUILDING_TABS, ENDED_BUILD_COPY } from "../build.constants";
import { pipelineStatus } from "../buildPipeline.constants";
import DerivingAnimation from "./DerivingAnimation";
import PipelineChecks from "./PipelineChecks";

// The deriving copy climbs a four-rung ladder off the builder's `done` set:
// each milestone that has landed swaps the line for the next thing the engine
// is working on, and once all three are in we fall through to the "loading the
// editor" line. Verbatim from the designer's DerivedPanels loading branch.
const derivingLabel = (done = []) => {
  if (!done.includes("understand")) return DERIVING_LABEL.understand;
  if (!done.includes("build")) return DERIVING_LABEL.build;
  if (!done.includes("scenarios")) return DERIVING_LABEL.scenarios;
  return DERIVING_LABEL.loading;
};

/**
 * The right pane while the environment is building, and the workspace once it is
 * built.
 *
 * Until the derivation completes and the store is primed, a muted tab rail sits
 * on top so you can see what's coming — the same tabs the finished environment
 * will carry — with the hero illustration and the setup timeline filling the
 * body underneath. The rail is visible but inert: dimmed and pointer-dead.
 *
 * At 7/7, once the environment has been adopted into the store (`primed`), the
 * body swaps in place for the live workspace: the muted rail becomes the
 * interactive rail and the hero gives way to the tab bodies. This is the
 * designer's DerivedPanels isLoading → panels swap, on the same build page.
 */
export default function BuildingPane({
  done = [],
  running = false,
  failure = null,
  env,
  envState,
  patch,
  primed = false,
  source,
  world = null,
  onStartNewBuild,
}) {
  const [tab, setTab] = useState("overview");

  const isLoading = !done.includes("scenarios") || !primed;

  if (!isLoading) {
    return (
      <WorkspacePanels
        env={env}
        envState={envState}
        patch={patch}
        tab={tab}
        onTabChange={setTab}
        buildMode
        gapsByTab={gapsByTab(env, envState)}
        counts={running ? null : countsFor(envState)}
      />
    );
  }

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      {/* muted rail — visible so you know what's coming, not competing for attention */}
      <Box
        sx={{
          flexShrink: 0, borderBottom: "1px solid", borderColor: "divider",
          opacity: 0.5, pointerEvents: "none",
        }}
      >
        <CustomTabs
          value={BUILDING_TABS[0].id}
          onChange={() => {}}
          variant="scrollable"
          scrollButtons={false}
          sx={{ ...ENV_TABS_SX, boxShadow: "none" }}
        >
          {BUILDING_TABS.map((t) => (
            <Tab key={t.id} value={t.id} label={t.label} />
          ))}
        </CustomTabs>
      </Box>

      {/* body */}
      <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, overflow: "auto", px: 2 }}>
        <DerivingAnimation
          label={
            failure
              ? (failure.canceled ? DERIVING_LABEL.canceled : DERIVING_LABEL.failed)
              : derivingLabel(done)
          }
          source={source}
          world={world}
          failed={!!failure}
        />
        <PipelineChecks pipeline={pipelineStatus(done, running, "setup", failure)} />
        {/* A failed or canceled build is not a dead end: say what to do next
            and offer the new build right under the step that stopped it. */}
        {failure && onStartNewBuild && (
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 3, pb: 3 }}>
            <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
              {failure.canceled
                ? ENDED_BUILD_COPY.canceled
                : failure.retryable
                  ? ENDED_BUILD_COPY.retryable
                  : ENDED_BUILD_COPY.failed}
            </Typography>
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={onStartNewBuild}
              sx={{ flexShrink: 0 }}
            >
              {ENDED_BUILD_COPY.startNew}
            </Button>
          </Stack>
        )}
      </Box>
    </Stack>
  );
}

BuildingPane.propTypes = {
  done: PropTypes.arrayOf(PropTypes.string),
  running: PropTypes.bool,
  failure: PropTypes.shape({
    stepId: PropTypes.string,
    title: PropTypes.string,
    detail: PropTypes.string,
    retryable: PropTypes.bool,
    canceled: PropTypes.bool,
  }),
  env: ENV_SHAPE,
  envState: ENV_STATE_SHAPE,
  patch: PropTypes.func,
  primed: PropTypes.bool,
  source: PropTypes.string,
  world: PropTypes.object,
  // Shown with the failed/canceled step: leaves for a new build of this source.
  onStartNewBuild: PropTypes.func,
};
