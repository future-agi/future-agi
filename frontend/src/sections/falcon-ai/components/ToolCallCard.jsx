import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import TextBlock from "./TextBlock";

function StatusIcon({ status }) {
  if (status === "running") {
    return (
      <CircularProgress
        size={12}
        thickness={6}
        sx={{ color: "text.disabled" }}
      />
    );
  }
  if (status === "completed") {
    return (
      <Iconify
        icon="mdi:check"
        width={13}
        sx={{ color: "success.main", flexShrink: 0 }}
      />
    );
  }
  if (status === "error") {
    return (
      <Iconify
        icon="mdi:close"
        width={13}
        sx={{ color: "error.main", flexShrink: 0 }}
      />
    );
  }
  return null;
}

StatusIcon.propTypes = {
  status: PropTypes.string.isRequired,
};

// One short, clean line from a (possibly markdown) result summary — used as the
// inline hint on the collapsed row so the step reads in a single line instead
// of spilling into a wide block.
function firstLine(text) {
  if (!text) return "";
  const line =
    text
      .replace(/[#*`>|]/g, "")
      .split("\n")
      .map((s) => s.trim())
      .find(Boolean) || "";
  return line.length > 80 ? `${line.slice(0, 80)}…` : line;
}

export default function ToolCallCard({ toolCall }) {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const isDark = theme.palette.mode === "dark";

  // Support both snake_case (WebSocket streaming) and camelCase (API history)
  const tool_name = toolCall.tool_name;
  const tool_description = toolCall.tool_description;
  const params = toolCall.params;
  const status = toolCall.status;
  const result_summary = toolCall.result_summary;
  const result_full = toolCall.result_full;

  const isRunning = status === "running";
  const isError = status === "error";
  const isCompleted = status === "completed";
  const canExpand = isCompleted || isError;

  // Keep the REAL tool name visible (just swap underscores for spaces) — the
  // exact name is information users rely on.
  const label = (tool_name || "tool").replace(/_/g, " ");
  const hint = firstLine(result_summary);

  const railColor = isDark
    ? alpha(theme.palette.common.white, 0.1)
    : alpha(theme.palette.common.black, 0.09);

  return (
    <Box
      sx={{
        // A thin left rail makes consecutive tool steps read as one quiet
        // "working" group, visually subordinate to the answer text.
        borderLeft: "2px solid",
        borderColor: isRunning ? "primary.main" : railColor,
        ml: 0.25,
        my: 0.25,
      }}
    >
      {/* Collapsed header — a single compact line */}
      <Box
        onClick={() => canExpand && setExpanded((p) => !p)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          pl: 1.25,
          pr: 0.75,
          py: 0.5,
          borderRadius: "0 6px 6px 0",
          cursor: canExpand ? "pointer" : "default",
          userSelect: "none",
          transition: "background-color 0.15s ease",
          "&:hover": canExpand
            ? {
                bgcolor: isDark
                  ? alpha(theme.palette.common.white, 0.04)
                  : alpha(theme.palette.common.black, 0.03),
              }
            : undefined,
        }}
      >
        <StatusIcon status={status} />

        <Typography
          component="span"
          sx={{
            fontFamily:
              "'SF Mono', 'Fira Code', 'Fira Mono', Menlo, Consolas, monospace",
            fontWeight: 500,
            fontSize: 12,
            color: "text.secondary",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          {label}
        </Typography>

        {/* Inline one-line result hint (or running state) — truncates, never wraps */}
        {isRunning ? (
          <Typography
            component="span"
            sx={{ fontSize: 11.5, color: "text.disabled", fontStyle: "italic" }}
          >
            running…
          </Typography>
        ) : (
          hint && (
            <Typography
              component="span"
              title={hint}
              sx={{
                fontSize: 11.5,
                color: isError ? "error.main" : "text.disabled",
                flex: 1,
                minWidth: 0,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {hint}
            </Typography>
          )
        )}

        {canExpand && (
          <Iconify
            icon={expanded ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={15}
            sx={{ color: "text.disabled", flexShrink: 0, ml: "auto" }}
          />
        )}
      </Box>

      {/* Expanded details — params + result, contained and scrollable */}
      <Collapse in={expanded && canExpand} unmountOnExit>
        <Box sx={{ pl: 1.25, pr: 0.75, pb: 1, pt: 0.25 }}>
          {tool_description && (
            <Typography
              variant="caption"
              sx={{
                display: "block",
                mb: 1,
                fontSize: 11.5,
                color: "text.disabled",
                fontStyle: "italic",
                lineHeight: 1.5,
              }}
            >
              {tool_description}
            </Typography>
          )}

          {params && Object.keys(params).length > 0 && (
            <Box sx={{ mb: 1 }}>
              <Box
                onClick={(e) => {
                  e.stopPropagation();
                  setParamsExpanded((p) => !p);
                }}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                  cursor: "pointer",
                  userSelect: "none",
                  mb: 0.5,
                }}
              >
                <Iconify
                  icon={
                    paramsExpanded ? "mdi:chevron-down" : "mdi:chevron-right"
                  }
                  width={13}
                  sx={{ color: "text.disabled" }}
                />
                <Typography
                  sx={{
                    fontSize: 10.5,
                    color: "text.disabled",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.06em",
                  }}
                >
                  Parameters
                </Typography>
              </Box>
              <Collapse in={paramsExpanded}>
                <Box
                  component="pre"
                  sx={{
                    fontSize: 11.5,
                    fontFamily:
                      "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
                    bgcolor: isDark
                      ? alpha(theme.palette.common.white, 0.04)
                      : "grey.50",
                    color: "text.secondary",
                    borderRadius: "6px",
                    p: 1.25,
                    overflow: "auto",
                    maxHeight: 180,
                    m: 0,
                    border: 1,
                    borderColor: isDark
                      ? alpha(theme.palette.common.white, 0.06)
                      : alpha(theme.palette.common.black, 0.06),
                  }}
                >
                  {JSON.stringify(params, null, 2)}
                </Box>
              </Collapse>
            </Box>
          )}

          {(result_full || result_summary) && (
            <Box>
              <Typography
                sx={{
                  fontSize: 10.5,
                  color: "text.disabled",
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                  display: "block",
                  mb: 0.5,
                }}
              >
                Result
              </Typography>
              <Box
                sx={{
                  maxHeight: 280,
                  overflow: "auto",
                  borderRadius: "6px",
                  bgcolor: isDark
                    ? alpha(theme.palette.common.white, 0.04)
                    : "grey.50",
                  p: 1.25,
                  border: 1,
                  borderColor: isDark
                    ? alpha(theme.palette.common.white, 0.06)
                    : alpha(theme.palette.common.black, 0.06),
                }}
              >
                {result_full ? (
                  <TextBlock content={result_full} />
                ) : (
                  <Typography
                    sx={{
                      fontSize: 12,
                      color: isError ? "error.main" : "text.secondary",
                      lineHeight: 1.5,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    }}
                  >
                    {result_summary}
                  </Typography>
                )}
              </Box>
            </Box>
          )}
        </Box>
      </Collapse>
    </Box>
  );
}

ToolCallCard.propTypes = {
  toolCall: PropTypes.shape({
    call_id: PropTypes.string,
    tool_name: PropTypes.string,
    tool_description: PropTypes.string,
    params: PropTypes.object,
    status: PropTypes.string,
    result_summary: PropTypes.string,
    result_full: PropTypes.string,
    step: PropTypes.number,
  }).isRequired,
};
