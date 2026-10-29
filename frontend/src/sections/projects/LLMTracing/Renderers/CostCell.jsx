import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { formatCost } from "../formatters";

function fmt(v) {
  if (v == null || Math.abs(v) < 0.0001) return formatCost(v);
  const abs = Math.abs(v);
  if (abs >= 1) return `$${v.toFixed(2)}`;
  if (abs >= 0.01) return `$${v.toFixed(4)}`;
  if (abs >= 0.001) return `$${v.toFixed(5)}`;
  return `$${v.toFixed(7).replace(/0+$/, "0")}`;
}

const BreakdownTooltip = ({ inputCost, outputCost, totalCost }) => (
  <Box sx={{ p: 0.5, minWidth: 180 }}>
    <Typography sx={{ fontSize: 13, fontWeight: 600, mb: 1 }}>
      Cost breakdown
    </Typography>
    <BRow label="Input cost" value={fmt(inputCost)} bold />
    <BRow label="input" value={fmt(inputCost)} />
    <Box sx={{ my: 0.75 }} />
    <BRow label="Output cost" value={fmt(outputCost)} bold />
    <BRow label="output" value={fmt(outputCost)} />
    <Box sx={{ borderTop: "1px solid rgba(255,255,255,0.15)", my: 0.75 }} />
    <BRow label="Total cost" value={fmt(totalCost)} bold />
  </Box>
);

const BRow = ({ label, value, bold }) => (
  <Box
    sx={{
      display: "flex",
      justifyContent: "space-between",
      py: 0.1,
      pl: bold ? 0 : 1.5,
      gap: 2,
    }}
  >
    <Typography
      sx={{
        fontSize: 12,
        fontWeight: bold ? 600 : 400,
        color: bold ? "inherit" : "text.secondary",
      }}
    >
      {label}
    </Typography>
    <Typography
      sx={{
        fontSize: 12,
        fontWeight: bold ? 600 : 400,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {value}
    </Typography>
  </Box>
);

BRow.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  bold: PropTypes.bool,
};
BreakdownTooltip.propTypes = {
  inputCost: PropTypes.number,
  outputCost: PropTypes.number,
  totalCost: PropTypes.number,
};

const CostCell = ({ value, data }) => {
  const totalCost = value ?? data?.cost;
  const promptTokens = data?.prompt_tokens ?? 0;
  const completionTokens = data?.completion_tokens ?? 0;
  const total = promptTokens + completionTokens || 1;
  const inputCost = data?.input_cost ?? totalCost * (promptTokens / total);
  const outputCost =
    data?.output_cost ?? totalCost * (completionTokens / total);

  if (totalCost == null || !Number.isFinite(totalCost)) {
    return (
      <Typography
        variant="body2"
        sx={{
          fontSize: 13,
          color: "text.disabled",
          px: 1.5,
          width: "100%",
          textAlign: "right",
        }}
      >
        -
      </Typography>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-end",
        width: "100%",
        px: 1.5,
        height: "100%",
        gap: 0.5,
      }}
    >
      <Typography
        variant="body2"
        sx={{
          fontSize: 13,
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
        }}
      >
        {fmt(totalCost)}
      </Typography>
      <Tooltip
        title={
          <BreakdownTooltip
            inputCost={inputCost}
            outputCost={outputCost}
            totalCost={totalCost}
          />
        }
        arrow
        placement="bottom"
        slotProps={{
          tooltip: { sx: { maxWidth: 280, bgcolor: "grey.900", p: 1 } },
        }}
      >
        <Box
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          sx={{
            display: "flex",
            cursor: "default",
            opacity: 0.4,
            "&:hover": { opacity: 1 },
          }}
        >
          <Iconify
            icon="mdi:information-outline"
            width={14}
            color="text.secondary"
          />
        </Box>
      </Tooltip>
    </Box>
  );
};

CostCell.propTypes = {
  value: PropTypes.any,
  data: PropTypes.object,
};

export default React.memo(CostCell);
