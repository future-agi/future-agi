import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { ResultChip } from "src/sections/projects/LLMTracing/Renderers/EvalResultChips";
import EvalDetailExpansion from "./EvalDetailExpansion";
import { spanResultChip, spanHasDetail, NAME_W } from "./utils";
import { evalShape } from "./shapes";

// Span scope: one eval's result for the selected span; expands to the
// explanation + error localizer.
const EvalSingleRow = ({ ev, onFixWithFalcon }) => {
  const span = (ev.spans || [])[0] || {};
  const canExpand = spanHasDetail(span, ev.output_type);
  const [open, setOpen] = useState(false);
  const chip = spanResultChip(span, ev.output_type, ev.choices_map);

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
        <Typography
          noWrap
          sx={{ width: NAME_W, fontSize: 11.5, fontWeight: 500 }}
        >
          {ev.eval_name}
        </Typography>
        <Box sx={{ flex: 1, display: "flex", gap: 0.5, flexWrap: "wrap" }}>
          <ResultChip label={chip.label} tone={chip.tone} dense />
        </Box>
      </Box>
      {open && canExpand && (
        <EvalDetailExpansion
          span={span}
          evalConfigId={ev.eval_config_id}
          evalName={ev.eval_name}
          outputType={ev.output_type}
          onFixWithFalcon={onFixWithFalcon}
          pl={4.5}
        />
      )}
    </>
  );
};

EvalSingleRow.propTypes = {
  ev: evalShape.isRequired,
  onFixWithFalcon: PropTypes.func,
};

export default EvalSingleRow;
