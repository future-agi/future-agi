import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  adaptEvalCell,
  buildChips,
} from "src/sections/projects/LLMTracing/evalCellModel";
import {
  ResultChip,
  Annotation,
} from "src/sections/projects/LLMTracing/Renderers/EvalResultChips";
import BreakdownRow from "./BreakdownRow";
import { colFromTemplate, NAME_W } from "./utils";

// Span-level eval rolled up across N spans; expands to the per-span breakdown.
const TemplateRollupRow = ({ template, rollup, onSelectSpan, onFixWithFalcon }) => {
  const col = colFromTemplate(template, template.rows[0]);
  const { chips, notEvaluated } = buildChips(adaptEvalCell(rollup, col), col);
  const evaluated = rollup?.span_level?.evaluated_count ?? template.rows.length;
  const isN1 = template.rows.length <= 1;
  const [open, setOpen] = useState(isN1); // N=1 auto-expands

  return (
    <>
      <Box
        onClick={() => setOpen((p) => !p)}
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 1,
          px: 1.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          cursor: "pointer",
          minHeight: 32,
          "&:hover": { bgcolor: "rgba(0,0,0,0.02)" },
        }}
      >
        <Box sx={{ width: 18, flexShrink: 0, display: "flex" }}>
          <Iconify
            icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
            width={14}
            color="text.disabled"
          />
        </Box>
        <Typography noWrap sx={{ width: NAME_W, fontSize: 11.5, fontWeight: 500 }}>
          {template.name}
        </Typography>
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            flexWrap: "wrap",
          }}
        >
          {chips.map((c) => (
            <ResultChip key={c.label} label={c.label} tone={c.tone} dense />
          ))}
          <Typography sx={{ fontSize: 10.5, color: "text.disabled", ml: 0.5 }}>
            from {evaluated} span{evaluated === 1 ? "" : "s"}
          </Typography>
          {notEvaluated > 0 && (
            <Annotation text={`+ ${notEvaluated} not evaluated`} />
          )}
        </Box>
      </Box>
      {open &&
        template.rows.map((row, i) => (
          <BreakdownRow
            key={row.spanId ? `${row.spanId}-${row.eval_config_id}` : i}
            row={row}
            onSelectSpan={onSelectSpan}
            onFixWithFalcon={onFixWithFalcon}
          />
        ))}
    </>
  );
};

TemplateRollupRow.propTypes = {
  template: PropTypes.object.isRequired,
  rollup: PropTypes.object,
  onSelectSpan: PropTypes.func,
  onFixWithFalcon: PropTypes.func,
};

export default TemplateRollupRow;
