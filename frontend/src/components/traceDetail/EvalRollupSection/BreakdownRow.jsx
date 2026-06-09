import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { ResultChip } from "src/sections/projects/LLMTracing/Renderers/EvalResultChips";
import EvalDetailExpansion from "./EvalDetailExpansion";
import { singleResultChip, hasDetail } from "./utils";

// One span's result under a rolled-up template; expands to its explanation.
const BreakdownRow = ({ row, onSelectSpan, onFixWithFalcon }) => {
  const [open, setOpen] = useState(false);
  const chip = singleResultChip(row);
  const canExpand = hasDetail(row);

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
          {row.spanName || "unnamed"}
        </Typography>
        <Box sx={{ width: "30%", display: "flex" }}>
          <ResultChip label={chip.label} tone={chip.tone} dense />
        </Box>
        <Box
          sx={{ flex: 1, display: "flex", justifyContent: "flex-end", flexShrink: 0 }}
        >
          {row.spanId && onSelectSpan && (
            <Box
              onClick={(e) => {
                e.stopPropagation();
                onSelectSpan(row.spanId);
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
        <EvalDetailExpansion row={row} onFixWithFalcon={onFixWithFalcon} pl={6} />
      )}
    </>
  );
};

BreakdownRow.propTypes = {
  row: PropTypes.object.isRequired,
  onSelectSpan: PropTypes.func,
  onFixWithFalcon: PropTypes.func,
};

export default BreakdownRow;
