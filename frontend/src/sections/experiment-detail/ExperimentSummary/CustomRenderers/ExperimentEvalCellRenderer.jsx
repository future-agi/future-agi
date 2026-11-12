import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect } from "react";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import NumericCell from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";

const ExperimentEvalCellRenderer = ({ value, eGridCell, ...rest }) => {
  const column = rest?.colDef?.col;
  const reverseOutput = column?.reverse_output ?? column?.reverseOutput;
  const outputType = column?.output_type;
  const isNumeric = outputType === OutputTypes.NUMERIC;

  // stats/ sends each eval column's average on a 0-100 scale for every output
  // type (pass rate for Pass/Fail), so a number is a score, not a label.
  const result =
    typeof value === "number"
      ? { kind: "score", score: value }
      : normalizeEvalResult(value, outputType);

  // Numeric stats averages are already percentages, including 0.5% and 1%.
  // Only raw evaluation envelopes carry normalized 0-1 scores.
  const pct =
    typeof value === "number"
      ? value
      : (result.kind === "score" || result.kind === "choices") &&
          typeof result.score === "number"
        ? result.score * 100
        : 0;
  // interpolateColorBasedOnScore inverts the scale itself when reverse is set.
  const backgroundColor = isNumeric
    ? null
    : interpolateColorBasedOnScore(pct, 100, reverseOutput);

  useEffect(() => {
    if (eGridCell?.style) {
      eGridCell.style.backgroundColor = backgroundColor || "";
    }
  }, [eGridCell?.style, backgroundColor]);

  if (isNumeric) {
    return (
      <NumericCell
        value={value}
        sx={{
          paddingX: 2,
          height: "100%",
          display: "flex",
          alignItems: "center",
        }}
      />
    );
  }

  const renderText = () => {
    switch (result.kind) {
      case "choices":
        return result.items.join(", ");
      case "passfail":
        return result.label;
      case "score":
        return `${Math.round(pct * 100) / 100}%`;
      case "empty":
      default:
        return "";
    }
  };

  return <Box sx={{ paddingX: 2, color: "text.primary" }}>{renderText()}</Box>;
};

ExperimentEvalCellRenderer.propTypes = {
  value: PropTypes.any,
  eGridCell: PropTypes.object,
};

export default ExperimentEvalCellRenderer;
