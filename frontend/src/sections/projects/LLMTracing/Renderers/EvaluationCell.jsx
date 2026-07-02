import React from "react";
import { Chip } from "@mui/material";
import { interpolateColorTokenBasedOnScore } from "src/utils/utils";
import PropTypes from "prop-types";
import NumericCell from "../../../common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

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

  // Pass/Fail type
  if (column?.outputType === "Pass/Fail") {
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
