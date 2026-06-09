import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  adaptEvalCell,
  buildChips,
} from "src/sections/projects/LLMTracing/evalCellModel";
import { ResultChip } from "src/sections/projects/LLMTracing/Renderers/EvalResultChips";
import EvalDetailExpansion from "./EvalDetailExpansion";
import { colFromTemplate, hasDetail, NAME_W } from "./utils";

// Trace-level eval — one verdict (no per-span rollup); expands to its explanation.
const TraceVerdictRow = ({ template, rollup, onFixWithFalcon }) => {
  const col = colFromTemplate(template, template.rows[0]);
  const { chips } = buildChips(adaptEvalCell(rollup, col), col);
  const row = template.rows[0] || {};
  const canExpand = hasDetail(row);
  const [open, setOpen] = useState(false);

  return (
    <>
      <Box
        onClick={() => canExpand && setOpen((p) => !p)}
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 1,
          px: 1.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          minHeight: 32,
          cursor: canExpand ? "pointer" : "default",
          "&:hover": canExpand ? { bgcolor: "rgba(0,0,0,0.02)" } : undefined,
        }}
      >
        <Box sx={{ width: 18, flexShrink: 0, display: "flex" }}>
          {canExpand && (
            <Iconify
              icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={14}
              color="text.disabled"
            />
          )}
        </Box>
        <Typography noWrap sx={{ width: NAME_W, fontSize: 11.5, fontWeight: 500 }}>
          {template.name}
        </Typography>
        <Box sx={{ flex: 1, display: "flex", gap: 0.5, flexWrap: "wrap" }}>
          {chips.map((c) => (
            <ResultChip key={c.label} label={c.label} tone={c.tone} dense />
          ))}
        </Box>
      </Box>
      {open && canExpand && (
        <EvalDetailExpansion row={row} onFixWithFalcon={onFixWithFalcon} pl={4.5} />
      )}
    </>
  );
};

TraceVerdictRow.propTypes = {
  template: PropTypes.object.isRequired,
  rollup: PropTypes.object,
  onFixWithFalcon: PropTypes.func,
};

export default TraceVerdictRow;
