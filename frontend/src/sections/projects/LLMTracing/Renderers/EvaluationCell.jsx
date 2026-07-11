import React from "react";
import { Chip } from "@mui/material";
import { interpolateColorTokenBasedOnScore } from "src/utils/utils";
import PropTypes from "prop-types";
import NumericCell from "../../../common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import EvalStatusIndicator from "src/components/eval/EvalStatusIndicator";
import { getEvalNonScoreStatusFromValue } from "src/utils/evalStatus";

const EvaluationCell = ({ value, column }) => {
  const shouldReverse = column?.reverseOutput;

  // No eval value (missing / not yet evaluated) — render dash so callers
  // can distinguish "no data" from an actual Pass/Fail/score.
  const isMissing = value === null || value === undefined || value === "";

  // Backend marks errored evals as { error: true } so we can distinguish
  // them from "no eval run" and from a real Pass/Fail/score.
  const isError =
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    value.error === true;

  // Non-score states (queued/running/skipped) and errored all render through
  // the shared EvalStatusIndicator so every surface stays visually unified.
  // The wrapper fills the cell so the "Evaluating…" skeleton covers it like a
  // real result does.
  const indicatorStatus =
    getEvalNonScoreStatusFromValue(value) || (isError ? "errored" : null);
  if (indicatorStatus) {
    return (
      <div style={{ width: "100%", height: "100%" }}>
        <EvalStatusIndicator
          status={indicatorStatus}
          skippedReason={value?.skipped_reason}
        />
      </div>
    );
  }

  if (column?.outputType === OutputTypes.NUMERIC) {
    if (isMissing) {
      return (
        <div
          style={{
            padding: "0 12px",
            display: "flex",
            alignItems: "center",
            height: "100%",
          }}
        >
          -
        </div>
      );
    }
    return <NumericCell value={value} sx={{ padding: "0 12px" }} />;
  }

  // Pass/Fail columns carry the averaged pass rate (0-100) in the list — a trace
  // with 2 of 3 spans passing arrives as 66.67 — so they render through the
  // numeric-percentage path below ("66.67%"). Collapsing to a binary
  // "Pass"/"Fail" label would drop the average across the trace's spans.

  // Array of values
  if (Array.isArray(value)) {
    return (
      <div
        style={{
          display: "flex",
          gap: "8px",
          flexWrap: "wrap",
          padding: "10px 12px",
          height: "100%",
          overflow: "auto",
        }}
      >
        {value.map((each) => (
          <Chip
            size="small"
            key={each}
            label={each}
            variant="outlined"
            color="primary"
          />
        ))}
      </div>
    );
  }

  // Parse safely — if value is missing/non-numeric, render a dash
  // instead of "0.00%" to distinguish no-data from an actual zero score.
  if (isMissing) {
    return (
      <div
        style={{
          padding: "0 12px",
          display: "flex",
          alignItems: "center",
          height: "100%",
        }}
      >
        -
      </div>
    );
  }
  const numericValue = parseFloat(value);
  if (isNaN(numericValue)) {
    return (
      <div
        style={{
          padding: "0 12px",
          display: "flex",
          alignItems: "center",
          height: "100%",
        }}
      >
        -
      </div>
    );
  }
  const safeValue = numericValue;

  // Numeric score
  const score = shouldReverse
    ? (100 - safeValue).toFixed(2)
    : safeValue.toFixed(2);

  // Colors
  const { bgcolor: backgroundColor = "", color = "" } =
    interpolateColorTokenBasedOnScore(safeValue, 100) || {};

  return (
    <div
      style={{
        backgroundColor,
        color,
        paddingInline: "12px",
        fontWeight: 500,
        fontSize: "13px",
        height: "100%",
        display: "flex",
        alignItems: "center",
      }}
    >
      {score}%
    </div>
  );
};

export default EvaluationCell;

EvaluationCell.propTypes = {
  value: PropTypes.any,
  column: PropTypes.object,
};
