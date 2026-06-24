import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { evalCellChips } from "src/sections/projects/LLMTracing/evalCellModel";
import { ResultChip } from "src/sections/projects/LLMTracing/Renderers/EvalResultChips";
import BreakdownRow from "./BreakdownRow";
import { colFromEval, NAME_W } from "./utils";
import { evalShape } from "./shapes";

// One eval rolled up across its spans (trace scope); expands to the per-span
// breakdown. The chip renders the backend-computed `aggregate` directly.
const EvalRollupRow = ({ ev, onSelectSpan, onFixWithFalcon }) => {
  const spans = ev.spans || [];
  const erroredCount = spans.filter((s) => s.error).length;
  const chips = evalCellChips(ev.aggregate, colFromEval(ev));
  if (erroredCount) chips.push({ label: `Errored ${erroredCount}`, tone: "errored" });
  const [open, setOpen] = useState(false);

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
        </Box>
      </Box>
      {open &&
        spans.map((span, i) => (
          <BreakdownRow
            key={span.span_id || i}
            span={span}
            outputType={ev.output_type}
            choicesMap={ev.choices_map}
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
  ev: evalShape.isRequired,
  onSelectSpan: PropTypes.func,
  onFixWithFalcon: PropTypes.func,
};

export default EvalRollupRow;
