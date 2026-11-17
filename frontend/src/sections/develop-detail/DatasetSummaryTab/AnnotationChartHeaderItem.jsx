import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { fmtNumber, fmtScore } from "src/sections/common/format/fmt";

/** One formatter: `38.71%` → `38.7%`, `0.6249` → `0.62`, `N/A` explained. */
function display(value, missing) {
  if (!(value || value === 0)) return { text: "N/A", note: missing ?? null };
  const str = String(value);
  if (/^-?\d+(\.\d+)?%$/.test(str))
    return { text: `${fmtNumber(Math.round(parseFloat(str) * 10) / 10)}%` };
  if (isNaN(value)) return { text: str };
  const n = Number(value);
  return { text: Number.isInteger(n) ? fmtNumber(n) : fmtScore(n) };
}

const AnnotationChartHeaderItem = ({ title, value, missing }) => {
  const shown = display(value, missing);
  return (
    <Box
      display="flex"
      flexDirection={"column"}
      gap={"2px"}
      padding={1.5}
      sx={{
        backgroundColor: "background.neutral",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
      }}
    >
      <Typography
        typography={"s2_1"}
        fontWeight={"fontWeightMedium"}
        color="text.primary"
      >
        {title}
      </Typography>

      <Typography
        typography={"m1"}
        fontWeight={"fontWeightSemiBold"}
        color="text.primary"
      >
        {shown.text}
      </Typography>
      {shown.note && (
        <Typography typography="s3" color="text.secondary">
          {shown.note}
        </Typography>
      )}
    </Box>
  );
};

export default AnnotationChartHeaderItem;

AnnotationChartHeaderItem.propTypes = {
  title: PropTypes.string,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  missing: PropTypes.string,
};
