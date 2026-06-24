import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { evalCellChips } from "../evalCellModel";

const TONE_TO_PALETTE = {
  pass: "success",
  fail: "error",
  neutral: "warning",
  errored: "error",
};

export const ResultChip = ({ label, tone, dense = false }) => {
  const theme = useTheme();
  const palette =
    theme.palette[TONE_TO_PALETTE[tone] || "info"] || theme.palette.info;
  const color = palette.main;
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        flexShrink: 0,
        px: dense ? "6px" : "8px",
        py: 0,
        borderRadius: dense ? "9px" : "12px",
        border: `1px solid ${alpha(color, 0.5)}`,
        backgroundColor: alpha(color, 0.08),
        color,
        fontSize: dense ? "10.5px" : "12px",
        fontWeight: 600,
        lineHeight: dense ? "16px" : "18px",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </Box>
  );
};

ResultChip.propTypes = {
  label: PropTypes.string,
  tone: PropTypes.oneOf(["pass", "fail", "neutral", "errored", "plain"]),
  dense: PropTypes.bool,
};

export const Annotation = ({ text }) => (
  <Typography
    component="span"
    title={text}
    sx={{
      fontSize: "12px",
      fontStyle: "italic",
      color: "text.secondary",
      whiteSpace: "nowrap",
      overflow: "hidden",
      textOverflow: "ellipsis",
      flexShrink: 1,
      minWidth: 0,
    }}
  >
    {text}
  </Typography>
);

Annotation.propTypes = { text: PropTypes.string };

// AG-Grid cell renderer for an eval-results cell.
const EvalResultChips = (params) => {
  const col = params?.colDef?.context?.sourceColumn;
  const raw = params?.value;
  const chips = evalCellChips(raw, col);
  if (!chips.length && raw == null) return null;
  const nothingRan = chips.length === 0;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-start",
        flexWrap: "nowrap",
        gap: "4px",
        px: "12px",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      {chips.map((c) => (
        <ResultChip key={c.label} label={c.label} tone={c.tone} />
      ))}
      {nothingRan && <Annotation text="Not evaluated" />}
    </Box>
  );
};

export default EvalResultChips;
