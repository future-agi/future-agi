import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { ResultChip } from "src/sections/projects/LLMTracing/Renderers/EvalResultChips";
import EvalDetailExpansion from "./EvalDetailExpansion";
import { spanResultChip, spanHasDetail } from "./utils";
import { evalSpanShape } from "./shapes";

// One span's result under a rolled-up eval; expands to its explanation/localizer.
const BreakdownRow = ({
  span,
  outputType,
  choicesMap,
  evalConfigId,
  evalName,
  onSelectSpan,
  onFixWithFalcon,
}) => {
  const [open, setOpen] = useState(false);
  const chip = spanResultChip(span, outputType, choicesMap);
  const canExpand = spanHasDetail(span);

  return (
    <>
      <Box
        onClick={() => canExpand && setOpen((p) => !p)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          pl: 4.3,
          pr: 1.5,
          py: 0.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          minHeight: 30,
          cursor: canExpand ? "pointer" : "default",
          "&:hover": { bgcolor: "rgba(0,0,0,0.02)" },
        }}
      >
        <Box sx={{ width: 14, flexShrink: 0, display: "flex" }}>
          {canExpand && (
            <Iconify
              icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
              width={13}
              color="text.disabled"
            />
          )}
        </Box>
        <Typography
          noWrap
          sx={{ width: "45%", fontSize: 11, color: "text.secondary" }}
        >
          {span.span_name || "unnamed"}
        </Typography>
        <Box sx={{ width: "30%", display: "flex" }}>
          <ResultChip label={chip.label} tone={chip.tone} dense />
        </Box>
        <Box
          sx={{ flex: 1, display: "flex", justifyContent: "flex-end", flexShrink: 0 }}
        >
          {span.span_id && onSelectSpan && (
            <Box
              onClick={(e) => {
                e.stopPropagation();
                onSelectSpan(span.span_id);
              }}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: "3px",
                fontSize: 10,
                color: "text.disabled",
                cursor: "pointer",
                flexShrink: 0,
                whiteSpace: "nowrap",
                "&:hover": { color: "primary.main" },
              }}
            >
              <Iconify icon="mdi:eye-outline" width={12} />
              <span>View span</span>
            </Box>
          )}
        </Box>
      </Box>
      {open && canExpand && (
        <EvalDetailExpansion
          span={span}
          evalConfigId={evalConfigId}
          evalName={evalName}
          outputType={outputType}
          onFixWithFalcon={onFixWithFalcon}
          pl={6}
        />
      )}
    </>
  );
};

BreakdownRow.propTypes = {
  span: evalSpanShape.isRequired,
  outputType: PropTypes.string,
  choicesMap: PropTypes.object,
  evalConfigId: PropTypes.string,
  evalName: PropTypes.string,
  onSelectSpan: PropTypes.func,
  onFixWithFalcon: PropTypes.func,
};

export default BreakdownRow;
