/* eslint-disable react/prop-types */
import React from "react";
import { Box, Typography, Stack, Chip, Divider } from "@mui/material";
import { formatDateTime } from "../utils/formatters";

const SideBySideViewer = ({ result, sourceModel, shadowModel }) => {
  const r = result;
  const sourceLatency = Number(r.source_latency_ms || 0);
  const shadowLatency = Number(r.shadow_latency_ms || 0);
  const sourceTokens = Number(r.source_tokens || 0);
  const shadowTokens = Number(r.shadow_tokens || 0);
  const sourceStatus = Number(r.source_status_code || 200);
  const shadowStatus = Number(r.shadow_status_code || 0);
  const sourceResponse = r.source_response || "";
  const shadowResponse = r.shadow_response || "";
  const shadowError = r.shadow_error || "";
  const requestId = r.request_id || "";
  const createdAt = r.created_at;

  return (
    <Box
      sx={{
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        overflow: "hidden",
      }}
    >
      {/* Header bar */}
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        sx={{ px: 2, py: 1, bgcolor: "background.default" }}
      >
        <Typography variant="caption" fontWeight={600}>
          Comparison
        </Typography>
        {requestId && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontFamily: "monospace" }}
          >
            {requestId.substring(0, 12)}...
          </Typography>
        )}
        {createdAt && (
          <Typography variant="caption" color="text.secondary">
            {formatDateTime(createdAt)}
          </Typography>
        )}
      </Stack>

      <Divider />

      {/* Two-panel layout */}
      <Stack
        direction={{ xs: "column", md: "row" }}
        divider={<Divider orientation="vertical" flexItem />}
      >
        {/* Production panel */}
        <Box sx={{ flex: 1 }}>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{
              px: 2,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography variant="caption" fontWeight={600}>
              Production ({sourceModel})
            </Typography>
            <StatusChip code={sourceStatus} />
            <Typography variant="caption" color="text.secondary">
              {sourceLatency.toLocaleString()}ms
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {sourceTokens.toLocaleString()}t
            </Typography>
          </Stack>
          <Box
            sx={{
              px: 2,
              py: 1.5,
              maxHeight: 400,
              overflow: "auto",
              fontFamily: isJSON(sourceResponse) ? "monospace" : "inherit",
              fontSize: "0.8125rem",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              lineHeight: 1.6,
            }}
          >
            {formatResponse(sourceResponse) || (
              <Typography
                variant="body2"
                color="text.disabled"
                fontStyle="italic"
              >
                No response captured
              </Typography>
            )}
          </Box>
        </Box>

        {/* Shadow panel */}
        <Box sx={{ flex: 1 }}>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{
              px: 2,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography variant="caption" fontWeight={600}>
              Shadow ({shadowModel})
            </Typography>
            <StatusChip code={shadowStatus} error={shadowError} />
            <Typography variant="caption" color="text.secondary">
              {shadowLatency.toLocaleString()}ms
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {shadowTokens.toLocaleString()}t
            </Typography>
          </Stack>
          <Box
            sx={{
              px: 2,
              py: 1.5,
              maxHeight: 400,
              overflow: "auto",
              fontFamily: isJSON(shadowResponse) ? "monospace" : "inherit",
              fontSize: "0.8125rem",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              lineHeight: 1.6,
            }}
          >
            {shadowError ? (
              <Typography variant="body2" color="error.main">
                Error: {shadowError}
              </Typography>
            ) : (
              formatResponse(shadowResponse) || (
                <Typography
                  variant="body2"
                  color="text.disabled"
                  fontStyle="italic"
                >
                  No response captured
                </Typography>
              )
            )}
          </Box>
        </Box>
      </Stack>
    </Box>
  );
};

// ── Helpers ──────────────────────────────────────────────────

const StatusChip = ({ code, error }) => {
  const ok = code === 200 && !error;
  return (
    <Chip
      label={code || "N/A"}
      size="small"
      sx={{
        bgcolor: ok ? "status.success.bg" : "status.danger.bg",
        color: ok ? "status.success.fg" : "status.danger.fg",
      }}
    />
  );
};

function isJSON(str) {
  if (!str) return false;
  const trimmed = str.trim();
  return trimmed.startsWith("{") || trimmed.startsWith("[");
}

function formatResponse(str) {
  if (!str) return "";
  if (isJSON(str)) {
    try {
      return JSON.stringify(JSON.parse(str), null, 2);
    } catch {
      return str;
    }
  }
  return str;
}

export default SideBySideViewer;
