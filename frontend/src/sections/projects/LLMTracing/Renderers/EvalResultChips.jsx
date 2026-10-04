import React from "react";
import PropTypes from "prop-types";
import { Box, Chip } from "@mui/material";

import { buildEvalCellModel } from "../evalCellModel";

const colorForTone = (tone) => {
  if (tone === "success" || tone === "pass") return "success";
  if (tone === "error" || tone === "fail") return "error";
  if (tone === "warning") return "warning";
  return "default";
};

const EvalResultChips = ({ value, outputType, choicesMap }) => {
  const model = buildEvalCellModel(value, outputType, choicesMap);
  if (model.kind !== "counts") return null;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        minHeight: "100%",
        px: 1,
        flexWrap: "wrap",
      }}
    >
      {model.chips.map((chip) => (
        <Chip
          key={chip.key}
          size="small"
          label={chip.label}
          color={colorForTone(chip.tone)}
          variant="outlined"
        />
      ))}
    </Box>
  );
};

EvalResultChips.propTypes = {
  value: PropTypes.object,
  outputType: PropTypes.string,
  choicesMap: PropTypes.object,
};

export default EvalResultChips;
