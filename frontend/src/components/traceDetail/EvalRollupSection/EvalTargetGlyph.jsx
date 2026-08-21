import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CustomTooltip from "src/components/tooltip";
import { getGlyphMeta } from "src/sections/projects/LLMTracing/evalGlyph";

// Target-type badge for a single eval — T for trace-level, S for span-level,
// nothing for anything else. The grid header renders the same T/S from the same
// getGlyphMeta source, but styles its badge separately.
const EvalTargetGlyph = ({ rowType }) => {
  const glyph = getGlyphMeta(rowType);
  if (!glyph) return null;
  return (
    <CustomTooltip show title={glyph.label} arrow placement="top" size="small">
      <Box
        component="span"
        sx={{
          flexShrink: 0,
          px: 0.6,
          py: 0.05,
          borderRadius: "3px",
          bgcolor: (t) => alpha(t.palette.text.disabled, 0.15),
          fontSize: 9,
          fontWeight: 700,
          lineHeight: 1.6,
          color: "text.secondary",
          cursor: "default",
        }}
      >
        {glyph.code}
      </Box>
    </CustomTooltip>
  );
};

EvalTargetGlyph.propTypes = {
  rowType: PropTypes.string,
};

export default EvalTargetGlyph;
