import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import { getGlyphMeta } from "src/sections/projects/LLMTracing/evalTaskGrouping";

// Task group header — tasks icon + name, with the shared T/S glyph (getGlyphMeta)
// at the right end, matching the trace grid's EvalTaskGroupHeader.
const TaskHeader = ({ name, rowType, showGlyph = true }) => {
  const glyph = getGlyphMeta(rowType);
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 0.75,
        px: 1.5,
        py: 0.5,
        bgcolor: "background.default",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, minWidth: 0 }}>
        <SvgColor
          src="/assets/icons/navbar/ic_dash_tasks.svg"
          sx={{ width: 14, height: 14, color: "text.secondary", flexShrink: 0 }}
        />
        <Typography noWrap sx={{ fontSize: 11.5, fontWeight: 600 }}>
          {name}
        </Typography>
      </Box>
      {showGlyph && glyph && (
        <CustomTooltip show title={glyph.label} arrow placement="left" size="small">
          <Box
            sx={{
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
      )}
    </Box>
  );
};

TaskHeader.propTypes = {
  name: PropTypes.string,
  rowType: PropTypes.string,
  showGlyph: PropTypes.bool,
};

export default TaskHeader;
