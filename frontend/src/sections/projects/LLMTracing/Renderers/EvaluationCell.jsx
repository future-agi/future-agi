import React from "react";
import { Box, Chip, Tooltip } from "@mui/material";
import { interpolateColorTokenBasedOnScore } from "src/utils/utils";
import PropTypes from "prop-types";
import NumericCell from "../../../common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import {
  resolveEvalType,
  EVAL_TYPE,
  CHOICE_TONE,
  classifyChoice,
} from "../evalTaskMock";
import {
  isEvalRollup,
  mockPassFailRollup,
  getChoiceLabels,
} from "../evalCellMock";

// Choice chip tone → standard FAGI colour tokens, mirroring the outlined-chip
// style used in EvaluateArrayCellRenderer (green / amber / red).
const TONE_COLOR = {
  [CHOICE_TONE.GOOD]: { border: "green.500", text: "green.500" },
  [CHOICE_TONE.PARTIAL]: { border: "warning.main", text: "warning.dark" },
  [CHOICE_TONE.BAD]: { border: "red.500", text: "red.500" },
};

const ChoiceChip = ({ label, count, tone }) => {
  const c = TONE_COLOR[tone] || TONE_COLOR[CHOICE_TONE.BAD];
  return (
    <Chip
      size="small"
      label={count != null ? `${label} ${count}` : label}
      variant="outlined"
      sx={{
        borderRadius: (theme) => theme.spacing(0.5),
        borderColor: c.border,
        color: c.text,
        fontWeight: 400,
        typography: "s3",
        height: 22,
      }}
    />
  );
};

ChoiceChip.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  tone: PropTypes.string,
};

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

  if (isError) {
    return (
      <div
        style={{
          padding: "0 12px",
          display: "flex",
          alignItems: "center",
          gap: "6px",
          height: "100%",
          color: "#b91c1c",
          fontSize: "13px",
          fontWeight: 500,
        }}
        title="Eval errored"
      >
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: "14px",
            height: "14px",
            borderRadius: "50%",
            background: "#fee2e2",
            color: "#b91c1c",
            fontSize: "10px",
            fontWeight: 700,
            lineHeight: 1,
          }}
        >
          !
        </span>
        Error
      </div>
    );
  }

  const evalType = resolveEvalType(column);
  const rollup = isEvalRollup(column);

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

  // Pass/Fail type (§4.5)
  if (column?.outputType === "Pass/Fail" || evalType === EVAL_TYPE.PASS_FAIL) {
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

    // Trace row, rolled up from span evals → Fail / Errored / Pass tone chips
    // plus a muted "+N not evaluated" tail (§4.5), matching the trace-detail
    // rollup and the FAGI outlined-chip pattern.
    if (rollup) {
      const { pass, fail, errored, notEvaluated } = mockPassFailRollup(
        value,
        column,
      );
      return (
        <Box
          sx={{
            height: "100%",
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            px: 1.5,
            flexWrap: "wrap",
          }}
        >
          {fail > 0 && (
            <ChoiceChip label={`Fail ${fail}`} tone={CHOICE_TONE.BAD} />
          )}
          {errored > 0 && (
            <ChoiceChip
              label={`Errored ${errored}`}
              tone={CHOICE_TONE.PARTIAL}
            />
          )}
          {pass > 0 && (
            <ChoiceChip label={`Pass ${pass}`} tone={CHOICE_TONE.GOOD} />
          )}
          {notEvaluated > 0 && (
            <Box
              component="span"
              sx={{ fontSize: 11, color: "text.disabled", flexShrink: 0 }}
            >
              + {notEvaluated} not evaluated
            </Box>
          )}
        </Box>
      );
    }

    // Direct trace/span result → single Pass or Fail badge (unchanged).
    const isPass = !!value;
    const { bgcolor: backgroundColor, color } =
      interpolateColorTokenBasedOnScore(isPass ? 100 : 0, 100);

    return (
      <div
        style={{
          height: "100%",
          width: "100%",
          display: "flex",
          alignItems: "center",
          backgroundColor,
          padding: "0 12px",
          margin: 0,
          fontSize: "14px",
          color,
        }}
      >
        {isPass ? "Pass" : "Fail"}
      </div>
    );
  }

  // A numeric value is never a choice label — the backend splits a choice eval
  // into per-choice numeric "Avg.{choice}" columns, and those must render as
  // numbers, not chips. Only treat genuinely categorical values as choices.
  const looksNumeric =
    typeof value === "number" ||
    (typeof value === "string" &&
      value.trim() !== "" &&
      !Number.isNaN(Number(value)));

  // Choice type (§4.6 / FR-3.4) — render the occurring choice label(s) as
  // standard chips. Choice results are NEVER shown as a percentage; the chip
  // is the label only.
  if (
    (evalType === EVAL_TYPE.CHOICE || Array.isArray(value)) &&
    !looksNumeric
  ) {
    const labels = getChoiceLabels(value);
    if (isMissing || labels.length === 0) {
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
    const MAX = 3;
    const shown = labels.slice(0, MAX);
    const overflow = labels.slice(MAX);
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 1.5,
          height: "100%",
          flexWrap: "wrap",
        }}
      >
        {shown.map((label) => (
          <ChoiceChip
            key={label}
            label={label}
            tone={classifyChoice(label, column)}
          />
        ))}
        {overflow.length > 0 && (
          <Tooltip arrow title={overflow.join(", ")}>
            <Box
              component="span"
              sx={{
                fontSize: 11,
                fontWeight: 600,
                color: "text.secondary",
                flexShrink: 0,
              }}
            >
              +{overflow.length}
            </Box>
          </Tooltip>
        )}
      </Box>
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
