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
import { colFromEval, evalToCell, NAME_W } from "./utils";

// One eval rolled up across its spans (trace scope); expands to the per-span
// breakdown. The chip is rendered from the backend-computed `aggregate`.
const EvalRollupRow = ({ ev, onSelectSpan, onFixWithFalcon }) => {
  const col = colFromEval(ev);
  const { chips, notEvaluated } = buildChips(adaptEvalCell(evalToCell(ev), col), col);
  const spans = ev.spans || [];
  const [open, setOpen] = useState(spans.length <= 1); // N=1 auto-expands

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
          {ev.eval_name}
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
            from {spans.length} span{spans.length === 1 ? "" : "s"}
          </Typography>
          {notEvaluated > 0 && (
            <Annotation text={`+ ${notEvaluated} not evaluated`} />
          )}
        </Box>
      </Box>
      {open &&
        spans.map((span, i) => (
          <BreakdownRow
            key={span.span_id || i}
            span={span}
            outputType={ev.output_type}
            evalConfigId={ev.eval_config_id}
            evalName={ev.eval_name}
            onSelectSpan={onSelectSpan}
            onFixWithFalcon={onFixWithFalcon}
          />
        ))}
    </>
  );
};

EvalRollupRow.propTypes = {
  ev: PropTypes.object.isRequired,
  onSelectSpan: PropTypes.func,
  onFixWithFalcon: PropTypes.func,
};

export default EvalRollupRow;
