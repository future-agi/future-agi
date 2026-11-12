import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "src/components/snackbar";
import RunChip from "src/sections/common/Runs/RunChip";
import BaselinePicker from "src/sections/common/Runs/BaselinePicker";
import RunPickerPopover from "src/sections/common/Runs/RunPickerPopover";
import useRunActions from "src/sections/common/Runs/useRunActions";
import { HeldReason } from "src/sections/common/ExperimentRunControl/RunControlHeld";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { usePinBaseline, useUnpinBaseline } from "src/api/develop/runs";
import RunDetailsPopover from "./RunDetailsPopover";
import RunConnectionStrip from "src/sections/common/Connect/RunConnectionStrip";
import { runKindOf } from "src/sections/common/Runs/runKind";
import { canIterate } from "../iterate/iterateModel";
import { MAX_COMPARE_RUNS } from "./useResultsUrlState";
import { configKeyOf, useRunConfigs } from "src/api/develop/r2";
import {
  standInBodyOf,
  standInOf,
} from "src/sections/develop-detail/Experiment/waitingModel";
import axios, { endpoints } from "src/utils/axios";
import { useQueryClient } from "@tanstack/react-query";
import {
  byDatasetHref,
  compareSummaryHref,
  iterateHref,
} from "src/sections/common/Analysis/analysisLinks";

/** One chip with its menu (PRODUCT-SPEC §3.10.3). */
const RunBarChip = ({
  run,
  color,
  isBaseline,
  mode,
  datasetId,
  canRemove,
  onUseAsBaseline,
  onRemove,
  onShowDetails,
  // eslint-disable-next-line no-unused-vars
  kind,
  readOnly,
  config,
  baselineRunId,
}) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { pathname, search } = useLocation();
  const boxRef = useRef(null);
  const actions = useRunActions(run.not_found ? null : run);
  const pin = usePinBaseline(datasetId);
  const unpin = useUnpinBaseline(datasetId);
  const status = getRunStatus(run);

  if (run.not_found)
    return (
      <RunChip
        run={run}
        showName={false}
        menu={[
          {
            label: "Remove from comparison",
            icon: "mdi:close",
            onClick: onRemove,
            disabled: !canRemove,
          },
        ]}
      />
    );

  const menu = [
    {
      label: isBaseline
        ? "Baseline run for this view"
        : "Use as baseline run for this view",
      icon: "mdi:flag-outline",
      onClick: onUseAsBaseline,
      disabled: isBaseline,
    },
    run.is_dataset_baseline
      ? {
          label: "Unpin dataset baseline",
          icon: "mdi:pin-off-outline",
          onClick: () => unpin.mutate(),
          disabled: readOnly || !actions.can.edit,
        }
      : {
          label: "Pin as dataset baseline",
          icon: "mdi:pin-outline",
          onClick: () => pin.mutate(run.run_id),
          disabled: readOnly || !actions.can.edit,
        },
    {
      label: "Show run details",
      icon: "mdi:information-outline",
      onClick: () => onShowDetails(boxRef.current),
      // dataset integration FE-0: `How to run` (generated runners, connect commands)
      // is removed from the chip menu until the SDK command is real (FE-1).
      divider: true,
    },
    // R2 (L2): one dock, many doors (the dock on this page opens on it).
    // dataset integration FE-0: absent for a run Test a fix does not apply to.
    ...(canIterate(run)
      ? [
          {
            label: "Test a fix on this run…",
            icon: "mdi:source-branch",
            onClick: () =>
              navigate(
                `${pathname}${iterateHref(search, { runId: run.run_id, scope: "all" })}`,
              ),
            disabled: readOnly || !actions.can.edit,
          },
        ]
      : []),
    // the interface contract: this run's config next to the baseline's (Explore › Config).
    ...(baselineRunId && baselineRunId !== run.run_id
      ? [
          {
            label: "Compare configs…",
            icon: "mdi:file-compare",
            onClick: () =>
              navigate(
                `${compareSummaryHref({
                  datasetId,
                  runIds: [baselineRunId, run.run_id],
                  baselineId: baselineRunId,
                })}&view=workbench&wbtab=config`,
              ),
          },
        ]
      : []),
    {
      label: "Same config on other datasets",
      icon: "mdi:database-search-outline",
      onClick: () =>
        navigate(
          byDatasetHref({
            datasetId,
            runIds: [run.run_id],
            cand: run.run_id,
            configKey: configKeyOf(run),
          }),
        ),
    },
    {
      label: `Open experiment #${run.experiment_ordinal}`,
      icon: "mdi:arrow-top-right",
      onClick: () =>
        navigate(`/dashboard/develop/experiment/${run.experiment_id}/data`),
    },
    // Run control off: this run's own Stop and Resume stay, disabled, with
    // the reason under them (the experiment page's Stop still stops it).
    ...(status.active && !readOnly
      ? [
          {
            label: `Stop ${run.label}`,
            icon: "mdi:stop-circle-outline",
            onClick: () => actions.stop(),
            danger: true,
            ...(actions.runControlOff
              ? {
                  disabled: true,
                  tag: <HeldReason text={actions.held.stop} />,
                }
              : {}),
          },
        ]
      : []),
    // options-truth L7b G4: no Resume for a run its cost cap stopped.
    ...(status.key === "Cancelled" && !readOnly && actions.canResume
      ? [
          {
            // "Resume #9B · 39 left" (same count as the chip's tooltip).
            label: actions.labels.resume.replace(
              "Resume",
              `Resume ${run.label}`,
            ),
            icon: "mdi:play-circle-outline",
            onClick: () => actions.resume(),
            ...(actions.runControlOff
              ? {
                  disabled: true,
                  tag: <HeldReason text={actions.held.resume} />,
                }
              : {}),
          },
        ]
      : []),
    // L3 G5 (S2): the stand-in is named by how the run runs (your
    // terminal, the CI job, the upload) and plays the same mock path.
    ...(status.waiting && !readOnly
      ? (() => {
          const full = config ? { ...run, config } : run;
          const stand = config ? standInOf(full) : null;
          if (config && !stand) return [];
          return [
            {
              label: stand?.label ?? "Simulate the upload",
              icon:
                stand && stand.label !== "Simulate the upload"
                  ? "mdi:play-outline"
                  : "mdi:upload-outline",
              onClick: () =>
                stand && stand.label !== "Simulate the upload"
                  ? axios
                      .post(
                        endpoints.develop.r4.simulateUpload(run.run_id),
                        standInBodyOf(full, { datasetId }),
                      )
                      .finally(() => queryClient.invalidateQueries())
                  : actions.simulateUpload(),
            },
          ];
        })()
      : []),
    ...(mode === "compare"
      ? [
          {
            label: "Remove from comparison",
            icon: "mdi:close",
            onClick: onRemove,
            disabled: !canRemove,
            divider: true,
          },
        ]
      : []),
    {
      label: "Copy run ID",
      icon: "basil:copy-outline",
      onClick: () => {
        copyToClipboard(run.run_id);
        enqueueSnackbar(`${run.label} run ID copied`, { variant: "success" });
      },
      divider: mode !== "compare",
    },
  ];

  return (
    <Box ref={boxRef} sx={{ display: "inline-flex", minWidth: 0 }}>
      <RunChip
        run={run}
        color={color}
        isBaseline={isBaseline}
        isPinned={!!run.is_dataset_baseline}
        size="medium"
        menu={menu}
      />
    </Box>
  );
};

RunBarChip.propTypes = {
  run: PropTypes.object.isRequired,
  color: PropTypes.object,
  isBaseline: PropTypes.bool,
  mode: PropTypes.string,
  datasetId: PropTypes.string,
  canRemove: PropTypes.bool,
  onUseAsBaseline: PropTypes.func,
  onRemove: PropTypes.func,
  onShowDetails: PropTypes.func,
  onHowToRun: PropTypes.func,
  kind: PropTypes.string,
  config: PropTypes.object,
  readOnly: PropTypes.bool,
  baselineRunId: PropTypes.string,
};

/**
 * Run bar (PRODUCT-SPEC §3.10.3): one 40 px row of run chips, baseline
 * first; `Baseline:` picker; on the compare page `+ Add runs`. Progress,
 * errors and waiting states live on the chips.
 */
const RunBar = ({
  datasetId,
  mode,
  runs,
  colors,
  baselineId,
  pinnedRunId,
  restrictTo,
  metrics,
  onBaselineChange,
  onRemoveRun,
  onAddRuns,
  readOnly,
}) => {
  const [details, setDetails] = useState(null);
  const { data: cfg } = useRunConfigs(datasetId);
  const fullById = Object.fromEntries(
    (cfg?.results ?? []).map((r) => [r.run_id, r]),
  );
  const live = runs.filter((r) => !r.not_found);
  const canRemove = mode === "compare" && runs.length > 1;
  const showBaseline = live.length > 1;

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ minHeight: 40, minWidth: 0 }}
    >
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ mr: 0.25, flexShrink: 0 }}
      >
        {mode === "compare" ? "Runs" : live.length > 1 ? "Runs" : "Run"}
      </Typography>
      {/* One line that scrolls sideways: five runs never wrap into a second
          row and push the grid down. The padding keeps each chip's hover
          copy button inside the scroll box. */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        data-qa-runbar-scroll=""
        sx={{
          flex: "1 1 auto",
          minWidth: 0,
          overflowX: "auto",
          overflowY: "hidden",
          pt: "8px",
          pb: "6px",
          my: "-7px",
          // The last 24 px fade: a chip cut by the edge reads as "scroll for
          // more"; at the scroll end the fade sits on this padding.
          pr: 3,
          maskImage:
            "linear-gradient(to right, #000 calc(100% - 24px), transparent)",
          scrollbarWidth: "thin",
          "& > *": { flexShrink: 0 },
        }}
      >
        {runs.map((run) => (
          <RunBarChip
            key={run.run_id}
            run={run}
            color={colors[run.run_id]}
            isBaseline={run.run_id === baselineId && live.length > 1}
            mode={mode}
            datasetId={datasetId}
            canRemove={canRemove}
            readOnly={readOnly}
            onUseAsBaseline={() => onBaselineChange(run.run_id)}
            onRemove={() => onRemoveRun?.(run.run_id)}
            onShowDetails={(anchor) => setDetails({ run, anchor })}
            kind={
              fullById[run.run_id] ? runKindOf(fullById[run.run_id]) : undefined
            }
            config={fullById[run.run_id]?.config ?? run.config ?? null}
            baselineRunId={live.length > 1 ? baselineId : null}
          />
        ))}
        {mode === "compare" && runs.length < MAX_COMPARE_RUNS && (
          <RunPickerPopover
            datasetId={datasetId}
            excludeIds={runs.map((r) => r.run_id)}
            max={MAX_COMPARE_RUNS}
            groupBySameTarget
            onPick={onAddRuns}
            label="Add runs"
          />
        )}
      </Stack>
      {/* C13: the live strip of a run on a connected machine (Stop /
          Resume, waiting for the owner, offline). Null for other runs. */}
      {/* options-truth L7b G4 (live:run-strip): never for a finished run
          (connecting a machine is absent; the run's provenance says where
          it ran). */}
      {(() => {
        const local = runs
          .map((r) => fullById[r.run_id] ?? r)
          .find(
            (r) =>
              r?.config?.execution?.mode === "local" &&
              !getRunStatus(r).terminal,
          );
        return local ? (
          <RunConnectionStrip
            run={local}
            placement="results"
            sx={{ minWidth: 0, flexShrink: 1 }}
          />
        ) : null;
      })()}
      {showBaseline && (
        <BaselinePicker
          datasetId={datasetId}
          value={baselineId}
          pinnedRunId={pinnedRunId}
          runsInView={live}
          restrictTo={restrictTo}
          onChange={(runId) => onBaselineChange(runId)}
        />
      )}
      <RunDetailsPopover
        anchorEl={details?.anchor ?? null}
        run={details?.run}
        color={details ? colors[details.run.run_id] : undefined}
        metrics={metrics}
        onClose={() => setDetails(null)}
      />
    </Stack>
  );
};

RunBar.propTypes = {
  datasetId: PropTypes.string.isRequired,
  mode: PropTypes.oneOf(["experiment", "compare"]).isRequired,
  runs: PropTypes.array.isRequired,
  colors: PropTypes.object.isRequired,
  baselineId: PropTypes.string,
  pinnedRunId: PropTypes.string,
  restrictTo: PropTypes.arrayOf(PropTypes.string),
  metrics: PropTypes.array,
  onBaselineChange: PropTypes.func.isRequired,
  onRemoveRun: PropTypes.func,
  onAddRuns: PropTypes.func,
  readOnly: PropTypes.bool,
};

export default RunBar;
