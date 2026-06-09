import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { ResultChip } from "src/sections/projects/LLMTracing/Renderers/EvalResultChips";
import EvalDetailExpansion from "./EvalDetailExpansion";
import { singleResultChip, hasDetail, NAME_W } from "./utils";

// Span scope: one template's result(s) for the selected span; expands to the explanation.
const TemplateSingleRow = ({ template, onFixWithFalcon }) => {
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
          {template.rows.map((r, i) => {
            const chip = singleResultChip(r);
            return <ResultChip key={i} label={chip.label} tone={chip.tone} dense />;
          })}
        </Box>
      </Box>
      {open && canExpand && (
        <EvalDetailExpansion row={row} onFixWithFalcon={onFixWithFalcon} pl={4.5} />
      )}
    </>
  );
};

TemplateSingleRow.propTypes = {
  template: PropTypes.object.isRequired,
  onFixWithFalcon: PropTypes.func,
};

export default TemplateSingleRow;
