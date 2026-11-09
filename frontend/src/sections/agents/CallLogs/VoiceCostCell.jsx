import {
  formatExactMoney,
  multiplyMoneyValues,
} from "src/sections/common/format/money";
import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

function fmt(v) {
  return formatExactMoney(v, {
    minimumFractionDigits:
      Number(v) === 0
        ? 0
        : Math.abs(Number(v)) >= 1
          ? 2
          : Math.abs(Number(v)) >= 0.01
            ? 4
            : 6,
  });
}

const BreakdownTooltip = ({ breakdown, totalCents }) => {
  const total = multiplyMoneyValues(totalCents, "0.01");
  if (!breakdown && !total) return null;
  return (
    <Box sx={{ p: 0.5, minWidth: 180 }}>
      <Typography sx={{ fontSize: 13, fontWeight: 600, mb: 1 }}>
        Cost breakdown
      </Typography>
      {breakdown?.stt != null && (
        <BRow label="STT" value={fmt(breakdown.stt)} />
      )}
      {breakdown?.llm != null && (
        <BRow label="LLM" value={fmt(breakdown.llm)} />
      )}
      {breakdown?.tts != null && (
        <BRow label="TTS" value={fmt(breakdown.tts)} />
      )}
      {breakdown?.vapi != null && (
        <BRow label="Platform" value={fmt(breakdown.vapi)} />
      )}
      {breakdown?.transport != null && (
        <BRow label="Transport" value={fmt(breakdown.transport)} />
      )}
      <Box sx={{ borderTop: "1px solid rgba(255,255,255,0.15)", my: 0.75 }} />
      <BRow label="Total" value={fmt(breakdown?.total ?? total)} bold />
    </Box>
  );
};

const BRow = ({ label, value, bold }) => (
  <Box
    sx={{ display: "flex", justifyContent: "space-between", py: 0.1, gap: 2 }}
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
  breakdown: PropTypes.object,
  totalCents: PropTypes.number,
};

const VoiceCostCell = (params) => {
  const data = params?.data;
  const costCents = data?.costCents ?? data?.cost_cents;
  const breakdown = data?.costBreakdown ?? data?.cost_breakdown;

  if (costCents == null && breakdown?.total == null) {
    return (
      <Typography
        variant="body2"
        sx={{ fontSize: 13, color: "text.disabled", px: 2 }}
      >
        -
      </Typography>
    );
  }

  const totalDollars =
    costCents != null
      ? multiplyMoneyValues(costCents, "0.01")
      : breakdown?.total;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.5,
        px: 2,
        height: "100%",
      }}
    >
      <Typography
        variant="body2"
        sx={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}
      >
        {fmt(totalDollars)}
      </Typography>
      <Tooltip
        enterTouchDelay={0}
        title={
          <BreakdownTooltip breakdown={breakdown} totalCents={costCents} />
        }
        arrow
        placement="bottom"
        slotProps={{
          tooltip: { sx: { maxWidth: 280, bgcolor: "grey.900", p: 1 } },
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
            opacity: 0.4,
            "&:hover": { opacity: 1 },
            cursor: "default",
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

VoiceCostCell.propTypes = { data: PropTypes.object };

export default React.memo(VoiceCostCell);
