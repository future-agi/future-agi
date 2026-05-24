import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect } from "react";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import NumericCell from "src/sections/common/DevelopCellRenderer/EvaluateCellRenderer/NumericCell";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

const ExperimentEvalCellRenderer = ({ value, eGridCell, ...rest }) => {
  const column = rest?.colDef?.col;
  const reverseOutput = column?.reverseOutput;
  const isNumeric = column?.output_type === OutputTypes.NUMERIC;
  const numericValue = parseFloat(value);
  const formattedValue = reverseOutput
    ? 100 - (isNaN(numericValue) ? 0 : numericValue)
    : isNaN(numericValue)
      ? 0
      : numericValue;
  const backgroundColor = isNumeric
    ? null
    : interpolateColorBasedOnScore(formattedValue, 100, reverseOutput);

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

  return (
    <Box
      sx={{
        paddingX: 2,
        color: "text.primary",
      }}
    >
      {formattedValue}%
    </Box>
  );
};

ExperimentEvalCellRenderer.propTypes = {
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  eGridCell: PropTypes.object,
};

export default ExperimentEvalCellRenderer;
