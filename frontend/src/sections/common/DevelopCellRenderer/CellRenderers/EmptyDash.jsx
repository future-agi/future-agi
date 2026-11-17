import React from "react";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

/** `[]`, `{}`, blank text and their JSON strings: nothing to show. */
export function isEmptyCellValue(v) {
  if (v == null) return true;
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object") return Object.keys(v).length === 0;
  if (typeof v === "string") {
    const t = v.trim();
    return t === "" || t === "[]" || t === "{}" || t === "null";
  }
  return false;
}

/** DG#9: an empty cell is a faint dash, not `Empty` / `[0 items]`. */
export default function EmptyDash() {
  return (
    <CustomTooltip show title="Empty" size="small" placement="top">
      <Box
        component="span"
        aria-label="Empty"
        sx={{
          color: "text.disabled",
          opacity: 0.6,
          typography: "s2",
          px: 0.25,
        }}
      >
        –
      </Box>
    </CustomTooltip>
  );
}
