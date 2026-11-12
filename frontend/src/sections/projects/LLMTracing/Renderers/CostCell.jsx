import {
  formatExactMoney,
  isMoneyValue,
} from "src/sections/common/format/money";
import React from "react";
import ObservedUsageValue from "./ObservedUsageValue";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

function fmt(v) {
  return formatExactMoney(v);
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
  if (data?._captureCoverage?.coverage_unknown) {
    return (
      <ObservedUsageValue
        coverage={data._captureCoverage}
        value={isMoneyValue(totalCost) ? fmt(totalCost) : "Unknown"}
      />
    );
  }
  // Token shares do not establish a billed component price. Unknown stays unknown.
  const inputCost = data?.input_cost;
  const outputCost = data?.output_cost;

  if (!isMoneyValue(totalCost)) {
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
        enterTouchDelay={0}
        classes={{ tooltip: "ds-hovercard" }}
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
          tooltip: { sx: { maxWidth: 280 } },
        }}
      >
        <Box
          component="button"
          type="button"
          aria-label="Show exact cost breakdown"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          sx={{
            border: 0,
            background: "transparent",
            color: "inherit",
            p: 0,
            minWidth: 24,
            minHeight: 24,
            display: "flex",
            cursor: "default",
            opacity: 0.4,
            "&:hover": { opacity: 1 },
          }}
        >
          <Iconify
            icon="solar:info-circle-linear"
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
