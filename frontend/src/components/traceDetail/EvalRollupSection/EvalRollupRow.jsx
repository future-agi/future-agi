import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { evalCellChips } from "src/sections/projects/LLMTracing/evalCellModel";
import { ResultChip } from "src/sections/projects/LLMTracing/Renderers/EvalResultChips";
import BreakdownRow from "./BreakdownRow";
import EvalTargetGlyph from "./EvalTargetGlyph";
import { colFromEval, NAME_W, activatableProps } from "./utils";
import { evalShape } from "./shapes";

// One eval rolled up across its spans (trace scope); expands to the per-span
// breakdown. The chip renders the backend-computed `aggregate` directly.
const EvalRollupRow = ({ ev, onSelectSpan, onFixWithFalcon, showGlyph = true }) => {
  const spans = ev.spans || [];
  const erroredCount = spans.filter((s) => s.error).length;
  const chips = evalCellChips(ev.aggregate, colFromEval(ev));
  if (erroredCount) chips.push({ label: `Errored ${erroredCount}`, tone: "errored" });
  const [open, setOpen] = useState(false);
  // An eval with no spans has nothing to disclose, so it must not take a tab
  // stop or advertise a chevron that expands to nothing (same gate BreakdownRow
  // applies via `canExpand`).
  const canExpand = spans.length > 0;

  return (
    <>
      <Box
        {...activatableProps(() => setOpen((p) => !p), {
          expanded: open,
          enabled: canExpand,
        })}
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 1,
          px: 1.5,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          cursor: canExpand ? "pointer" : "default",
          minHeight: 32,
          "&:hover": { bgcolor: "rgba(0,0,0,0.02)" },
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
        <Box
          sx={{
            width: NAME_W,
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            minWidth: 0,
          }}
        >
          <Typography noWrap sx={{ fontSize: 11.5, fontWeight: 500, minWidth: 0 }}>
            {ev.eval_name}
          </Typography>
          {showGlyph && <EvalTargetGlyph rowType={ev.target_type} />}
        </Box>
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            flexWrap: "wrap",
          }}
        >
          {/* Labels are not unique: the scalar-array branch of evalCellChips
              maps a span's choice values straight through, so a repeated
              choice yields duplicate labels. Index-suffix keeps keys stable
              per position. */}
          {chips.map((c, i) => (
            <ResultChip
              key={`${c.label}-${i}`}
              label={c.label}
              tone={c.tone}
              dense
            />
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
  showGlyph: PropTypes.bool,
};

export default EvalRollupRow;
