import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect } from "react";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import NumericCell from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import { normalizeEvalResult } from "src/sections/develop-detail/DataTab/common";

const ExperimentEvalCellRenderer = ({ value, eGridCell, ...rest }) => {
  const column = rest?.colDef?.col;
  const reverseOutput = column?.reverseOutput;
  const outputType = column?.output_type;
  const isNumeric = outputType === OutputTypes.NUMERIC;

  const result = normalizeEvalResult(value, outputType);

  // Score percentage drives the background color tint.
  const pct =
    result.kind === "score"
      ? result.score <= 1
        ? result.score * 100
        : result.score
      : result.kind === "choices" && typeof result.score === "number"
        ? result.score * 100
        : 0;
  const backgroundColor = isNumeric
    ? null
    : interpolateColorBasedOnScore(
        reverseOutput ? 100 - pct : pct,
        100,
        reverseOutput,
      );

  useEffect(() => {
    if (eGridCell?.style) {
      eGridCell.style.backgroundColor = backgroundColor || "";
    }
  }, [eGridCell.style, backgroundColor]);

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
        return `${Math.round(pct)}%`;
      case "empty":
      default:
        return "";
    }
  };

  return (
    <Box sx={{ paddingX: 2, color: "text.primary" }}>{renderText()}</Box>
  );
};

ExperimentEvalCellRenderer.propTypes = {
  value: PropTypes.any,
  eGridCell: PropTypes.object,
};

export default ExperimentEvalCellRenderer;
