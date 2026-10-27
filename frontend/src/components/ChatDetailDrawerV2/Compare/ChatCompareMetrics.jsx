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
      fontSize: 10,
      fontWeight: 600,
      color: "text.secondary",
      textTransform: "uppercase",
      letterSpacing: "0.06em",
    }}
  >
    {children}
  </Typography>
);
SectionLabel.propTypes = { children: PropTypes.node };

const DeltaChip = ({ change, changePercent, changeText }) => {
  const isPositive = changePercent > 0;
  const isNegative = changePercent < 0;

  // Theme-aware colors so the chip is readable on the translucent
  // overlay background in both modes. See
  // memory/feedback_use_theme_palette_mode.md.
  const fg = (theme) => {
    if (isPositive) {
      return theme.palette.mode === "dark"
        ? theme.palette.success.main
        : theme.palette.success.darker;
    }
    if (isNegative) {
      return theme.palette.mode === "dark"
        ? theme.palette.error.main
        : theme.palette.error.darker;
    }
    return theme.palette.text.disabled;
  };

  const bg = (theme) => {
    if (isPositive) {
      return theme.palette.mode === "dark"
        ? theme.palette.success.darker
        : theme.palette.success.lighter;
    }
    if (isNegative) {
      return theme.palette.mode === "dark"
        ? theme.palette.error.darker
        : theme.palette.error.lighter;
    }
    return theme.palette.action.hover;
  };

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
          fontSize: 10,
          fontWeight: 600,
          color: fg,
          bgcolor: bg,
          px: 0.75,
          py: 0.25,
          borderRadius: "10px",
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
            fontSize: 10,
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
            fontSize: 10.5,
            fontWeight: 600,
            color: "text.secondary",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
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
          fontWeight: 700,
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
