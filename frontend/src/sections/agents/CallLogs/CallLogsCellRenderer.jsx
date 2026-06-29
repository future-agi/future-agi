import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { format, isValid } from "date-fns";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { CallLogsStatus } from "./CallLogsStatus";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

const CallLogsCellRenderer = (props) => {
  const value = props?.value ?? "";
  const columnId = props?.column?.colId;
  const rowData = props?.data;
  const createdAt = rowData?.created_at ? new Date(rowData.created_at) : null;
  const formattedDate =
    createdAt && isValid(createdAt)
      ? format(createdAt, "MM/dd/yyyy, hh:mmaaa")
      : rowData?.created_at?.split("T")[0] ?? "-";

  // Bypass the outer flex wrapper for id/phone columns — match the IPOPCell
  // structure (a single `.ipop-cell` div directly in the AG cell) so the
  // text truncates at the column boundary instead of extending past it.
  if (
    columnId === "call_id" ||
    columnId === "assistant_phone_number" ||
    columnId === "phone_number"
  ) {
    return (
      <CustomTooltip size="small" show={!!value} arrow title={value}>
        <Box
          className="ipop-cell"
          sx={{ px: 1.5, color: "text.secondary" }}
        >
          {value || "-"}
        </Box>
      </CustomTooltip>
    );
  }

  let content;

  if (columnId === "call_summary") {
    content = (
      <Typography variant="caption" color="text.primary">
        {formattedDate}
      </Typography>
    );
  } else if (columnId === "customer_name") {
    content = (
      <Typography variant="body2" fontWeight="500" noWrap>
        <CustomTooltip show={true} arrow title={value}>
          {value || "-"}
        </CustomTooltip>
      </Typography>
    );
  } else if (columnId === "duration_seconds") {
    const totalSeconds = Number(value) || 0;
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;

    const formatted = `${minutes}:${seconds.toString().padStart(2, "0")}`;

    content = (
      <Box display="flex" flexDirection="row" alignItems="center" gap={0.5}>
        <Iconify
          icon="radix-icons:clock"
          width={14}
          height={14}
          sx={{ mb: 0.2, color: "text.primary" }}
        />
        <Typography typography="s1">{formatted}</Typography>
      </Box>
    );
  } else if (columnId === "status") {
    content = <CallLogsStatus value={value} />;
  } else if (columnId === "started_at") {
    const started = rowData?.started_at ? new Date(rowData.started_at) : null;
    const fmtStarted =
      started && isValid(started)
        ? format(started, "MMM dd, yyyy HH:mm:ss")
        : "-";
    content = (
      <Typography
        variant="body2"
        sx={{ fontSize: 13, color: "text.secondary" }}
      >
        {fmtStarted}
      </Typography>
    );
  } else if (columnId === "call_type") {
    const typeColorMap = {
      inbound: "info",
      outbound: "secondary",
    };
    const palette = typeColorMap[(value || "").toLowerCase()];
    content = value ? (
      <Box
        sx={{
          display: "inline-flex",
          alignItems: "center",
          px: 1,
          py: 0.25,
          borderRadius: "4px",
          bgcolor: (theme) =>
            palette
              ? alpha(theme.palette[palette].main, 0.08)
              : theme.palette.action.hover,
          border: "1px solid",
          borderColor: (theme) =>
            palette ? alpha(theme.palette[palette].main, 0.24) : "divider",
        }}
      >
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 500,
            color: palette ? `${palette}.main` : "text.secondary",
            textTransform: "capitalize",
          }}
        >
          {value}
        </Typography>
      </Box>
    ) : (
      "-"
    );
  } else if (columnId === "ended_reason") {
    const reasonColorMap = {
      "customer-ended-call": "success",
      "customer-did-not-answer": "warning",
      "assistant-ended-call": "info",
      "silence-timed-out": "error",
      "max-duration-reached": "warning",
      error: "error",
    };
    const key = (value || "").toLowerCase();
    const reasonPalette =
      reasonColorMap[key] ||
      Object.entries(reasonColorMap).find(([k]) => key.startsWith(k))?.[1];
    const shortLabel = value
      ? value.length > 20
        ? value.slice(0, 18) + "..."
        : value
      : "-";
    content = value ? (
      <CustomTooltip show={value.length > 20} arrow title={value}>
        <Box
          sx={{
            display: "inline-flex",
            alignItems: "center",
            px: 1,
            py: 0.25,
            borderRadius: "4px",
            bgcolor: (theme) =>
              reasonPalette
                ? alpha(theme.palette[reasonPalette].main, 0.08)
                : theme.palette.action.hover,
            border: "1px solid",
            borderColor: (theme) =>
              reasonPalette
                ? alpha(theme.palette[reasonPalette].main, 0.24)
                : "divider",
          }}
        >
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 500,
              color: (theme) => {
                if (!reasonPalette) return theme.palette.text.secondary;
                // warning.main is a pale yellow that is unreadable on light bg
                if (
                  reasonPalette === "warning" &&
                  theme.palette.mode === "light"
                ) {
                  return theme.palette.warning.darker;
                }
                return theme.palette[reasonPalette].main;
              },
              whiteSpace: "nowrap",
            }}
          >
            {shortLabel}
          </Typography>
        </Box>
      </CustomTooltip>
    ) : (
      "-"
    );
  } else if (columnId === "response_time_ms") {
    const ms = Number(value) || 0;
    const formatted = ms
      ? ms < 1000
        ? `${Math.round(ms)}ms`
        : `${(ms / 1000).toFixed(1)}s`
      : "-";
    content = (
      <Typography variant="body2" sx={{ fontSize: 13 }}>
        {formatted}
      </Typography>
    );
  } else if (columnId === "turn_count") {
    const n = value != null && value !== "" ? Number(value) : NaN;
    content = (
      <Typography variant="body2" sx={{ fontSize: 13 }}>
        {Number.isFinite(n) ? String(n) : "-"}
      </Typography>
    );
  } else if (columnId === "agent_talk_percentage") {
    const n = value != null && value !== "" ? Number(value) : NaN;
    content = (
      <Typography variant="body2" sx={{ fontSize: 13 }}>
        {Number.isFinite(n) ? `${n.toFixed(1)}%` : "-"}
      </Typography>
    );
  } else if (columnId === "avg_agent_latency_ms") {
    const ms = value != null && value !== "" ? Number(value) : NaN;
    const formatted = Number.isFinite(ms)
      ? ms < 1000
        ? `${Math.round(ms)}ms`
        : `${(ms / 1000).toFixed(2)}s`
      : "-";
    content = (
      <Typography variant="body2" sx={{ fontSize: 13 }}>
        {formatted}
      </Typography>
    );
  } else if (columnId === "user_wpm" || columnId === "bot_wpm") {
    const n = value != null && value !== "" ? Number(value) : NaN;
    content = (
      <Typography variant="body2" sx={{ fontSize: 13 }}>
        {Number.isFinite(n) ? String(Math.round(n)) : "-"}
      </Typography>
    );
  } else if (
    columnId === "user_interruption_count" ||
    columnId === "ai_interruption_count"
  ) {
    const n = value != null && value !== "" ? Number(value) : NaN;
    const hasInterrupts = Number.isFinite(n) && n > 0;
    content = (
      <Typography
        variant="body2"
        sx={{
          fontSize: 13,
          color: (theme) => {
            if (!hasInterrupts) return theme.palette.text.primary;
            return theme.palette.mode === "dark"
              ? theme.palette.warning.main
              : theme.palette.warning.darker;
          },
          fontWeight: hasInterrupts ? 600 : 400,
        }}
      >
        {Number.isFinite(n) ? String(n) : "-"}
      </Typography>
    );
  } else if (!value) {
    content = "-";
  } else if (columnId === "overall_score") {
    content = <span>{value}/10</span>;
  } else {
    content = <span>{value}</span>;
  }

  return (
    <Box
      sx={{
        px: 1.5,
        py: 0.5,
        display: "flex",
        alignItems: "center",
        height: "100%",
      }}
    >
      {content}
    </Box>
  );
};

CallLogsCellRenderer.propTypes = {
  value: PropTypes.any,
  data: PropTypes.object,
  node: PropTypes.object,
  column: PropTypes.object,
  api: PropTypes.object,
  colDef: PropTypes.object,
};

export default CallLogsCellRenderer;
