import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip } from "@mui/material";
import { getEvalSourceMeta, sourceMetaFromLevel } from "../evalTaskMock";

// ---------------------------------------------------------------------------
// PRD-v3 source glyph. A small badge that tells the user where an eval result
// came from: "T" = trace-level eval, "S" = span-level eval rolled up across
// the trace's spans. Shown on eval column headers and eval cells.
//
// Pass either an eval `column` (level is resolved via the eval→task mapping)
// or an explicit `level` (used by merged choice-cluster headers that have no
// backing column object).
// ---------------------------------------------------------------------------

const EvalSourceGlyph = ({ column, level, sx }) => {
  const meta =
    level != null ? sourceMetaFromLevel(level) : getEvalSourceMeta(column);
  if (!meta) return null;
  return (
    <Tooltip arrow title={meta.label}>
      <Box
        component="span"
        sx={{
          flexShrink: 0,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          height: 16,
          minWidth: 16,
          px: 0.5,
          borderRadius: (theme) => theme.spacing(0.5),
          border: "1px solid",
          borderColor: "divider",
          color: "text.secondary",
          fontSize: 10,
          fontWeight: 700,
          lineHeight: 1,
          letterSpacing: 0.2,
          ...sx,
        }}
      >
        {meta.code}
      </Box>
    </Tooltip>
  );
};

EvalSourceGlyph.propTypes = {
  column: PropTypes.object,
  level: PropTypes.string,
  sx: PropTypes.object,
};

export default EvalSourceGlyph;
