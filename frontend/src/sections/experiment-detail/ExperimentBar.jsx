import {
  Box,
  Stack,
  Tab,
  Tabs,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import { useRunActivity } from "src/api/develop/r4";
import { useExperimentDetail } from "src/api/develop/runs";
import { useRunConfigs } from "src/api/develop/r2";
import {
  getRunStatus,
  pollIntervalFor,
  runProgressOf,
} from "src/sections/common/Runs/runStatus";
import React from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate, useParams } from "react-router";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import RunProvenance from "src/sections/common/Runs/RunProvenance";
import RunConnectionStrip from "src/sections/common/Connect/RunConnectionStrip";
import { agentVersionOf } from "src/sections/common/Runs/provenanceModel";
import CustomTooltip from "src/components/tooltip";
import HoverCard from "src/sections/common/ux/oneview/HoverCard";
import { runShortName, runSpokenName } from "src/sections/common/Runs/runLabel";
import { placeOf, runsWhereText } from "./ExperimentBarRightSection/rerunPlan";

/**
 * R4 lane X (MUST 3): the focused run's `What this run used` line on the
 * right of the tabs (click → the card), and the live connection strip for a
 * run on a connected machine.
 */
const LIVE_STRIP_STATES = ["running", "stopped", "waiting_local_confirm"];

function FocusedRun({ run }) {
  // Below 1440 px the strip shows only while the run is live, so a
  // finished local run does not squeeze the provenance line away (same
  // query and cache entry as the strip).
  const wide = useMediaQuery("(min-width:1440px)");
  const runId = run?.run_id ?? run?.id;
  // options-truth L7b G4 (live:run-strip): connecting a machine is absent,
  // so a finished run has no live connection to show: never the strip (no
  // Stop / Resume / Details, no activity poll); its provenance line says
  // where it ran.
  const finished = Boolean(run) && getRunStatus(run).terminal;
  const local = run?.config?.execution?.mode === "local" && !finished;
  const activity = useRunActivity(runId, { enabled: local && !wide });
  const showStrip =
    local && (wide || LIVE_STRIP_STATES.includes(activity.data?.state));
  if (!run) return null;
  const version = agentVersionOf(run);
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      data-testid="experiment-focused-run"
      sx={{ minWidth: 0, flex: 1, justifyContent: "flex-end", ml: 2 }}
    >
      {/* One line beside the tabs: the strip never wraps into a card here. */}
      {showStrip ? (
        <RunConnectionStrip
          run={run}
          sx={{
            flexShrink: 0,
            py: 0,
            whiteSpace: "nowrap",
            "& > .MuiStack-root": { flexWrap: "nowrap", minHeight: 28 },
          }}
        />
      ) : null}
      <CustomTooltip
        show
        size="small"
        title={`What ${run.label ?? "this run"} used${version ? ` · ${version.name} ${version.text}` : ""}. Click the line for every model, tool, parameter and where it ran.`}
      >
        <Typography
          typography="s2"
          fontWeight="fontWeightSemiBold"
          noWrap
          sx={{ flexShrink: 0 }}
        >
          {run.label}
        </Typography>
      </CustomTooltip>
      <Box sx={{ minWidth: 0, maxWidth: 620, display: "flex" }}>
        <RunProvenance run={run} variant="line" />
      </Box>
    </Stack>
  );
}
FocusedRun.propTypes = { run: PropTypes.object };

/** `Fix run #2G · scoring 4/13` (pure; null when the run is done). */
export function fixRunChipText(run) {
  const st = getRunStatus(run);
  if (!st.active && !st.waiting) return null;
  // L7c g5 (identity:header-fix-run-chips): the count is the rows scored
  // (runProgressOf `finished`), the count of every run chip's bar and of
  // the header pill, so a chip and the pill agree at one moment.
  const { finished, total } = runProgressOf(run);
  const phase =
    st.key === "ExperimentEvaluation"
      ? `scoring ${finished}/${total}`
      : st.key === "Queued"
        ? "queued"
        : st.waiting
          ? "waiting"
          : `running ${finished}/${total}`;
  // W1-1 kept runs: a rerun of a config (#2A·2) is not a fix.
  const noun =
    Number(run?.attempt) > 1 && run?.origin === "rerun" ? "Rerun" : "Fix run";
  return `${noun} ${run.label} · ${phase}`;
}

// At most this many in-progress chips sit beside the tabs; the rest are
// counted ("+2 more running"), so the chips never run over the tabs.
const MAX_CHIPS = 2;

/**
 * IT-R1 G1 P5: a fix run in progress (Test a fix: #2F, #2G) is its own
 * chip, read from the same polled run objects the header reads, so the
 * experiment's own runs never look queued because a fix is running.
 */
function FixRunChips({ experimentId }) {
  const { data: detail } = useExperimentDetail(experimentId, {
    refetchInterval: (q) =>
      pollIntervalFor(q.state.data?.data?.result?.runs ?? []),
  });
  const datasetId = detail?.dataset_id ?? detail?.dataset?.id ?? null;
  const { data: r1 } = useRunConfigs(datasetId, {
    enabled: Boolean(datasetId),
  });
  const fixIds = new Set(
    (r1?.results ?? [])
      .filter((r) => r.parent_run_id && r.experiment_id === experimentId)
      .map((r) => r.run_id),
  );
  const chips = (detail?.runs ?? [])
    .filter((r) => fixIds.has(r.run_id))
    .map((r) => ({ id: r.run_id, text: fixRunChipText(r) }))
    .filter((c) => c.text);
  if (!chips.length) return null;
  const shown = chips.slice(0, MAX_CHIPS);
  const more = chips.slice(MAX_CHIPS);
  return (
    <Stack
      direction="row"
      gap={0.75}
      alignItems="center"
      sx={{ flexShrink: 1, minWidth: 0, overflow: "hidden" }}
    >
      {shown.map((c) => (
        <Typography
          key={c.id}
          typography="s2"
          data-testid="fix-run-chip"
          sx={{
            px: 1,
            py: 0.25,
            borderRadius: 0.75,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
            whiteSpace: "nowrap",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {c.text}
        </Typography>
      ))}
      {more.length ? (
        <Typography
          typography="s2"
          color="text.secondary"
          title={more.map((c) => c.text).join("\n")}
          data-testid="fix-run-more"
          sx={{ whiteSpace: "nowrap" }}
        >
          {`+${more.length} more running`}
        </Typography>
      ) : null}
    </Stack>
  );
}
FixRunChips.propTypes = { experimentId: PropTypes.string };

/**
 * IT-R3 G6 P2: every run of the experiment and where it runs, before the
 * focused run's line: `3 runs · this machine` (hover lists each run), so a
 * single `#4C …` line never reads as a clipped list.
 */
function RunsWhere({ experimentId }) {
  const { data: detail } = useExperimentDetail(experimentId);
  const datasetId = detail?.dataset_id ?? null;
  const { data: r1 } = useRunConfigs(datasetId, {
    enabled: Boolean(datasetId),
  });
  const configs = new Map((r1?.results ?? []).map((r) => [r.run_id, r.config]));
  const runs = (detail?.runs ?? []).map((r) => ({
    ...r,
    config: configs.get(r.run_id) ?? r.config,
  }));
  const text = runsWhereText(runs);
  if (!text) return null;
  // IT-R5 G6 P1: the `5 runs: Support Ops Reply V1 · …` second line folds
  // in here: the chip's hover card names every run, what it is and where
  // it ran (one header row less).
  const names = [...new Set(runs.map((r) => runSpokenName(r)).filter(Boolean))];
  return (
    <HoverCard
      component="span"
      trigger={
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          data-testid="experiment-runs-where"
          sx={{ cursor: "default" }}
        >
          {text}
        </Typography>
      }
      triggerSx={{ display: "inline-flex", flexShrink: 0 }}
      title={`${runs.length} runs: ${names.join(" · ")}`}
      maxWidth={380}
    >
      <Stack gap={0.25} data-testid="experiment-runs-card">
        {runs.map((r) => (
          <Typography key={r.run_id} typography="s2">
            {`${r.label} ${runShortName(r) ?? ""} · ${placeOf(r).short}`}
          </Typography>
        ))}
      </Stack>
    </HoverCard>
  );
}
RunsWhere.propTypes = { experimentId: PropTypes.string };

// Data · Matrix · Summary (R2-SPEC §0.3). `iconify` icons render through
// Iconify, the rest through the navbar SVGs.
const TabOptions = [
  { label: "Data", value: "data", icon: "/assets/icons/navbar/ic_llm.svg" },
  { label: "Matrix", value: "matrix", iconify: "mdi:table-large" },
  { label: "Paths", value: "paths", iconify: "mdi:source-branch" }, // R4-F frozen line
  {
    label: "Summary",
    value: "summary",
    icon: "/assets/icons/navbar/ic_chartsObserve.svg",
  },
  // W5 C-H5: a run's traces (experiment page only, not compare).
  {
    label: "Traces",
    value: "traces",
    iconify: "mdi:timeline-text-outline",
    experimentOnly: true,
  },
];

// basePath: where the Data / Matrix / Summary segments live (the experiment page by
// default; the compare page passes `/dashboard/develop/<dataset>/compare`).
const ExperimentBar = ({ basePath, focusRun }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { experimentId } = useParams();
  const lastSegment = location.pathname.split("/").pop();
  // `false` while the index route redirects (the last segment is an id).
  const currentTab = TabOptions.some((t) => t.value === lastSegment)
    ? lastSegment
    : false;

  const theme = useTheme();

  return (
    <Box
      sx={{
        borderBottom: 1,
        borderColor: "divider",
        paddingX: 2.5,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <Box sx={{ flexShrink: 0 }}>
        <Tabs
          value={currentTab}
          sx={{
            minHeight: 42,
            "& .MuiTab-root": {
              margin: "0 !important",
              fontWeight: "fontWeightSemiBold",
              typography: "s1",
              padding: 1.25,
              minHeight: 42,
              color: "primary.main",
              "&:not(.Mui-selected)": {
                color: "text.disabled", // Color for unselected tabs
                fontWeight: "fontWeightMedium",
              },
            },
          }}
          TabIndicatorProps={{
            style: {
              backgroundColor: theme.palette.primary.main,
            },
          }}
          onChange={(e, value) => {
            const currentParams = new URLSearchParams(location.search);
            // Keep existing params (datasetId, payload, etc.)
            const queryString = currentParams.toString();

            const base =
              basePath ?? `/dashboard/develop/experiment/${experimentId}`;
            navigate(`${base}/${value}?${queryString}`, { replace: true });
          }}
        >
          {TabOptions.filter((tab) => !tab.experimentOnly || !basePath).map(
            (tab) => (
              <Tab
                sx={{
                  margin: 0,
                  px: "24px",
                }}
                key={tab.value}
                icon={
                  tab.iconify ? (
                    <Iconify
                      icon={tab.iconify}
                      width={20}
                      sx={{ mr: 0.5, flexShrink: 0 }}
                    />
                  ) : (
                    <SvgColor
                      src={tab.icon}
                      sx={{ height: "20px", width: "20px", mr: 0.5 }}
                    />
                  )
                }
                iconPosition="start"
                label={tab.label}
                value={tab.value}
              />
            ),
          )}
        </Tabs>
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ minWidth: 0, flex: 1, justifyContent: "flex-end" }}
      >
        {experimentId ? <FixRunChips experimentId={experimentId} /> : null}
        {experimentId ? <RunsWhere experimentId={experimentId} /> : null}
        {/* IT-R5 G6 P1: the focused run's provenance line shows on Summary
            and Paths only (Data and Matrix carry it in their run headers). */}
        {["summary", "paths"].includes(currentTab) ? (
          <FocusedRun run={focusRun} />
        ) : null}
      </Stack>
    </Box>
  );
};

ExperimentBar.propTypes = {
  basePath: PropTypes.string,
  // R4 lane X: the run whose provenance line the bar shows (R1 run + config).
  focusRun: PropTypes.object,
};

export default ExperimentBar;
