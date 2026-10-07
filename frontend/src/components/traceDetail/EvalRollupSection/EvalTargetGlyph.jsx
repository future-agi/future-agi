import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";

import { getEvalTargetGlyph } from "src/sections/projects/LLMTracing/evalGlyph";

const EvalTargetGlyph = ({ targetType }) => {
  const glyph = getEvalTargetGlyph(targetType);
  if (!glyph) return null;
  return (
    <Box
      component="span"
      aria-label={targetType === "span" ? "Span evaluation" : "Trace evaluation"}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: 16,
        height: 16,
        borderRadius: 0.5,
        bgcolor: "action.selected",
        color: "text.secondary",
        fontSize: 10,
        fontWeight: 700,
        lineHeight: 1,
      }}
    >
      {glyph}
    </Box>
  );
};

EvalTargetGlyph.propTypes = {
  targetType: PropTypes.string,
};

export default EvalTargetGlyph;
