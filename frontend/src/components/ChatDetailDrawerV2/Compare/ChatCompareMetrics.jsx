import React from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Tooltip, Typography } from "@mui/material";
import {
  formatIfFloat,
  getChangeText,
  getPerformanceMetricsLabel,
} from "src/sections/test-detail/TestDetailDrawer/BasLineCompare/common";

// KPI strip for chat baseline-vs-replay comparison (label + value + delta chip).

const SectionLabel = ({ children }) => (
  <Typography
    sx={{
      fontSize: 12,
      fontWeight: 600,
      color: "text.secondary",
      textTransform: "none",
      letterSpacing: 0,
    }}
  >
    {children}
  </Typography>
);
SectionLabel.propTypes = { children: PropTypes.node };

const DeltaChip = ({ change, changePercent, changeText }) => {
  const isPositive = changePercent > 0;
  const isNegative = changePercent < 0;

  // Status tokens resolve per theme, so no mode check is needed.
  const fg = isPositive
    ? "status.success.fg"
    : isNegative
      ? "status.danger.fg"
      : "text.tertiary";

  const bg = isPositive
    ? "status.success.bg"
    : isNegative
      ? "status.danger.bg"
      : "action.hover";

  const sign = (n) => (n > 0 ? "+" : "");

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ flexWrap: "wrap" }}
    >
      <Box
        sx={{
          fontSize: 12,
          fontWeight: 600,
          color: fg,
          bgcolor: bg,
          px: 0.75,
          py: 0.25,
          borderRadius: "8px",
          lineHeight: 1.4,
          whiteSpace: "nowrap",
        }}
      >
        {change != null && `${sign(change)}${change}`}
        {changePercent != null && ` (${sign(changePercent)}${changePercent}%)`}
      </Box>
      {changeText && (
        <Typography
          sx={{
            fontSize: 12,
            color: "text.disabled",
            whiteSpace: "nowrap",
          }}
        >
          {changeText}
        </Typography>
      )}
    </Stack>
  );
};
DeltaChip.propTypes = {
  change: PropTypes.number,
  changePercent: PropTypes.number,
  changeText: PropTypes.string,
};

const KpiCell = ({ label, value, change, changePercent, changeText }) => {
  const hasDelta =
    change != null && changePercent != null && Number.isFinite(changePercent);
  return (
    <Stack
      sx={{
        py: 0.75,
        px: 1.25,
        gap: 0.25,
        bgcolor: "background.paper",
        borderRight: "1px solid",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Tooltip title={label} placement="top" arrow disableInteractive>
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 600,
            color: "text.secondary",
            textTransform: "none",
            letterSpacing: 0,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {label}
        </Typography>
      </Tooltip>
      <Typography
        sx={{
          fontSize: 16,
          fontWeight: 600,
          color: "text.primary",
          lineHeight: 1.2,
          fontFamily: "monospace",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {value}
      </Typography>
      {hasDelta && (
        <DeltaChip
          change={change}
          changePercent={changePercent}
          changeText={changeText}
        />
      )}
    </Stack>
  );
};
KpiCell.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node.isRequired,
  change: PropTypes.number,
  changePercent: PropTypes.number,
  changeText: PropTypes.string,
};

const ChatCompareMetrics = ({ data, isLoading }) => {
  if (isLoading) {
    return (
      <Stack gap={0.75}>
        <SectionLabel>Performance overview</SectionLabel>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            overflow: "hidden",
          }}
        >
          {[0, 1, 2, 3].map((i) => (
            <Stack
              key={i}
              sx={{
                p: 1.25,
                gap: 0.5,
                bgcolor: "background.paper",
                borderRight: "1px solid",
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Skeleton variant="text" width={60} height={12} />
              <Skeleton variant="text" width={48} height={20} />
              <Skeleton variant="text" width={80} height={12} />
            </Stack>
          ))}
        </Box>
      </Stack>
    );
  }

  const metrics = Array.isArray(data) ? data : [];
  if (metrics.length === 0) return null;

  return (
    <Stack gap={0.75}>
      <SectionLabel>Performance overview</SectionLabel>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          overflow: "hidden",
        }}
      >
        {metrics.map((m) => (
          <KpiCell
            key={m.id ?? m.metric}
            // `isVoice=false` — this view only renders for chat.
            label={getPerformanceMetricsLabel(m?.metric, false)}
            value={String(formatIfFloat(m?.value) ?? "—")}
            change={m?.change != null ? Number(formatIfFloat(m.change)) : null}
            changePercent={
              m?.percentageChange != null
                ? Number(formatIfFloat(m.percentageChange))
                : null
            }
            changeText={getChangeText(false)}
          />
        ))}
      </Box>
    </Stack>
  );
};

ChatCompareMetrics.propTypes = {
  data: PropTypes.array,
  isLoading: PropTypes.bool,
};

export default ChatCompareMetrics;
