import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { correctedByLine, correctedOf } from "./correctedOutputModel";

const CLAMP = 400;

/**
 * HO results R4: the `Corrected` mark on a results cell whose output a
 * reviewer corrected (R3 `corrected_output`); the tooltip shows who, when
 * and the corrected text. Nothing when the result has none. `compact` (the
 * one-line density, whose row-hover actions cover the end of the line) is
 * the icon alone at the start of the line, named for screen readers.
 */
const CorrectedOutputChip = ({ result, compact = false }) => {
  const corrected = correctedOf(result);
  if (!corrected) return null;
  const text =
    corrected.value.length > CLAMP
      ? `${corrected.value.slice(0, CLAMP).trimEnd()}…`
      : corrected.value;
  return (
    <CustomTooltip
      show
      size="small"
      placement="top"
      arrow
      title={
        <Stack gap={0.5} sx={{ maxWidth: 360 }}>
          <Typography typography="s3" fontWeight={600}>
            {correctedByLine(corrected)}
          </Typography>
          <Typography typography="s3" sx={{ whiteSpace: "pre-wrap" }}>
            {text}
          </Typography>
        </Stack>
      }
    >
      <Box
        component="span"
        data-testid="cell-corrected"
        role={compact ? "img" : undefined}
        aria-label={compact ? correctedByLine(corrected) : undefined}
        onClick={(e) => e.stopPropagation()}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 0.25,
          px: compact ? 0 : 0.5,
          width: compact ? 20 : "auto",
          height: 20,
          borderRadius: 0.5,
          border: "1px solid",
          borderColor: "green.300",
          color: "green.700",
          bgcolor: "green.o5",
          typography: "s3",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        <Iconify icon="mdi:file-document-edit-outline" width={12} />
        {compact ? null : "Corrected"}
      </Box>
    </CustomTooltip>
  );
};

CorrectedOutputChip.propTypes = {
  result: PropTypes.object,
  compact: PropTypes.bool,
};

export default CorrectedOutputChip;
