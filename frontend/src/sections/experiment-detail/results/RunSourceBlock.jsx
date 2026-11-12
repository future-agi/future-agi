import React from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "src/components/snackbar";
import { fToNowStrict } from "src/utils/format-time";
import { useCodeSession, useRunConfigs } from "src/api/develop/r2";
import { useExperimentDetail } from "src/api/develop/runs";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { runKindOf } from "src/sections/common/Runs/runKind";
import { harnessLiveLine, sourceLine, tracesLine } from "./runSourceLines";

const FactLine = ({ text, secondary }) => (
  <CustomTooltip show title={text} placement="top" size="small" arrow>
    <Typography
      typography="s2"
      color={secondary ? "text.secondary" : "text.primary"}
      noWrap
      sx={{ minWidth: 0 }}
    >
      {text}
    </Typography>
  </CustomTooltip>
);
FactLine.propTypes = { text: PropTypes.string, secondary: PropTypes.bool };

/**
 * The run with its R1 `include=config` fields (kind, source, config) and the
 * dataset id: `{full, datasetId}`.
 */
export function useRunWithConfig(run) {
  const { data: detail } = useExperimentDetail(run?.experiment_id, {
    enabled: Boolean(run?.experiment_id) && !run?.dataset_id,
  });
  const datasetId = run?.dataset_id ?? detail?.dataset_id ?? null;
  const { data: r1 } = useRunConfigs(datasetId, {
    enabled: Boolean(datasetId),
  });
  const full = {
    ...run,
    ...((r1?.results ?? []).find((r) => r.run_id === run?.run_id) ?? {}),
  };
  return { full, datasetId };
}

/**
 * Run details `Source` block (R2-SPEC §5.3.7): what produced the run, where
 * its traces live, `Copy run id`. Reads R1 `include=config` for the run's
 * kind and config. dataset integration FE-0: `How to run` is gone (it printed generated
 * runners and connect commands that do not exist).
 */
// eslint-disable-next-line no-unused-vars
const RunSourceBlock = ({ run, onHowToRun }) => {
  const { full, datasetId } = useRunWithConfig(run);
  const kind = runKindOf(full);
  const { kind: datasetKind, dataset } = useDatasetKind(datasetId);
  const isCode = kind === "code";
  const { data: session } = useCodeSession(
    { experimentId: full.experiment_id, runId: full.run_id },
    { enabled: isCode && full.config?.code?.mode !== "sdk_preview" },
  );
  const ctx = {
    taskSuite: datasetKind === "task_suite",
    datasetName: dataset?.name,
    lastUploadText: session?.last_heartbeat_at
      ? fToNowStrict(session.last_heartbeat_at)
      : null,
    matchedRows: kind === "traces" ? Number(full.progress?.done) || 0 : null,
    totalRows:
      Number(full.version_rows ?? full.examples ?? full.progress?.total) ||
      null,
  };
  if (!full.config && !full.source && !full.kind) return null;
  return (
    <Stack spacing={0.5} sx={{ minWidth: 0 }} data-testid="run-source-block">
      <FactLine text={sourceLine(full, ctx)} />
      {harnessLiveLine(full) ? (
        <Stack direction="row" alignItems="center" gap={0.75}>
          <Iconify
            icon="svg-spinners:180-ring"
            width={12}
            sx={{ color: "primary.main", flexShrink: 0 }}
          />
          <FactLine text={harnessLiveLine(full)} />
        </Stack>
      ) : null}
      <FactLine text={tracesLine(full, ctx)} secondary />
      <Stack direction="row" alignItems="center" gap={1} sx={{ pt: 0.25 }}>
        <Button
          size="small"
          variant="text"
          startIcon={<Iconify icon="basil:copy-outline" width={14} />}
          onClick={() => {
            copyToClipboard(full.run_id);
            enqueueSnackbar("Run id copied", { variant: "success" });
          }}
          sx={{ typography: "s2", px: 0.75, minWidth: 0 }}
        >
          Copy run id
        </Button>
      </Stack>
    </Stack>
  );
};

RunSourceBlock.propTypes = {
  run: PropTypes.object,
  onHowToRun: PropTypes.func,
};

export default RunSourceBlock;
