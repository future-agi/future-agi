import React, { useState } from "react";
import PropTypes from "prop-types";
import RowMedia from "src/sections/common/Media/RowMedia";
import { mediaCellKind } from "src/sections/common/Media/mediaModel";
import {
  Box,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import TextDiff, {
  TextDiffLegend,
} from "src/sections/common/ValueViewer/TextDiff";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import { fDateTime } from "src/utils/format-time";
import EvalChip from "./EvalChip";
import RunLinks from "./RunLinks";
import { formatCost, formatLatency, runEvalVerdict } from "./runsModel";
import RunCommentsSection from "src/sections/common/Collab/RunCommentsSection";

const SMALL_TOGGLE = {
  "& .MuiToggleButton-root": {
    py: 0.125,
    px: 1,
    typography: "s3",
    textTransform: "none",
    color: "text.secondary",
    borderColor: "divider",
    "&.Mui-selected": { color: "primary.main", bgcolor: "action.selected" },
  },
};

const errorText = (error) => {
  if (typeof error === "string") return error || null;
  if (error == null) return null;
  if (typeof error.message === "string" && error.message) return error.message;
  return JSON.stringify(error);
};

/**
 * The expanded run row in `Runs across experiments`: the full output (per
 * trial), `Diff vs current expected`, and each eval's reason.
 */
const RowRunDetail = ({ run, rowId, datasetId, metrics, expected }) => {
  const trials = run.trials_detail ?? [];
  const [trial, setTrial] = useState(() => trials[0]?.trial_number ?? null);
  const [view, setView] = useState("output");
  const t = trials.find((x) => x.trial_number === trial) ?? null;
  const output = t?.output;
  const hasOutput = output !== null && output !== undefined;
  const failed = t?.status === "error" || t?.status === "infra";
  // A call / media output renders through MediaValue; a word
  // diff of it against the expected text is noise, so Diff is off for it.
  const media = Boolean(mediaCellKind(output));
  const canDiff = Boolean(expected) && hasOutput && !failed && !media;
  const showDiff = view === "diff" && canDiff;
  const scoreMetrics = metrics.filter((m) => t?.scores?.[m.key]);

  return (
    <Box sx={{ px: 1.5, py: 1.25, bgcolor: "background.neutral" }}>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ mb: 1, flexWrap: "wrap" }}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={showDiff ? "diff" : "output"}
          onChange={(_, v) => v && setView(v)}
          sx={SMALL_TOGGLE}
        >
          <ToggleButton value="output">Output</ToggleButton>
          <ToggleButton value="diff" disabled={!canDiff}>
            Diff vs current expected
          </ToggleButton>
        </ToggleButtonGroup>
        {trials.length > 1 && (
          <ToggleButtonGroup
            size="small"
            exclusive
            value={trial}
            onChange={(_, v) => v !== null && setTrial(v)}
            sx={SMALL_TOGGLE}
          >
            {trials.map((x) => (
              <ToggleButton key={x.trial_number} value={x.trial_number}>
                Trial {x.trial_number}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        )}
        <Box sx={{ flex: 1 }} />
        {showDiff && <TextDiffLegend />}
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
          title={run.ran_at ? `Ran ${fDateTime(run.ran_at)}` : undefined}
        >
          Run: {formatLatency(run.latency_ms)} · {formatCost(run.cost)}
        </Typography>
        <Box
          sx={{ display: "inline-flex" }}
          onClick={(e) => e.stopPropagation()}
        >
          <RunLinks run={run} rowId={rowId} datasetId={datasetId} />
        </Box>
      </Stack>
      <Box
        sx={{
          p: 1.25,
          borderRadius: 1,
          bgcolor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        {failed && (
          <Typography typography="s2" color="error.main" sx={{ mb: 1 }}>
            {errorText(t?.error) ?? "No error details recorded for this trial."}
          </Typography>
        )}
        {!hasOutput ? (
          <Typography typography="s2" color="text.secondary">
            No output recorded for this trial.
          </Typography>
        ) : media ? (
          <RowMedia value={output} />
        ) : showDiff ? (
          <TextDiff from={expected.value} to={output} />
        ) : (
          <ValueViewer value={output} mode="formatted" />
        )}
      </Box>
      {showDiff && (
        <Typography
          typography="s3"
          color="text.disabled"
          sx={{ mt: 0.5, display: "block" }}
        >
          Against {expected.name} as it is now
          {run.row_changed_after ? " (it changed after this run)" : ""}.
        </Typography>
      )}
      <Stack gap={1} sx={{ mt: 1.25 }}>
        {!scoreMetrics.length && (
          <Typography typography="s2" color="text.secondary">
            No scores recorded for this trial.
          </Typography>
        )}
        {scoreMetrics.map((m) => {
          const score = t.scores[m.key];
          const verdict = runEvalVerdict(score, m);
          return (
            <Stack key={m.key} direction="row" gap={1} alignItems="flex-start">
              <Box sx={{ flexShrink: 0 }}>
                <EvalChip name={m.name} verdict={verdict} />
              </Box>
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ pt: 0.375 }}
              >
                {score.explanation ?? "No reason recorded."}
              </Typography>
            </Stack>
          );
        })}
      </Stack>
      <RunCommentsSection
        rowId={rowId}
        runId={run.run_id}
        runLabel={run.label ?? run.run_label}
        traceId={t?.trace_id ?? null}
      />
    </Box>
  );
};

RowRunDetail.propTypes = {
  run: PropTypes.object.isRequired,
  rowId: PropTypes.string.isRequired,
  datasetId: PropTypes.string,
  metrics: PropTypes.array.isRequired,
  expected: PropTypes.shape({ name: PropTypes.string, value: PropTypes.any }),
};

export default RowRunDetail;
