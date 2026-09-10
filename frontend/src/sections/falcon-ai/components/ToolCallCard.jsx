import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { RETRIED } from "../helpers/toolTrail";
import TextBlock from "./TextBlock";

function StatusIcon({ status }) {
  if (status === "running") {
    return (
      <CircularProgress
        size={14}
        thickness={5}
        sx={{ color: "text.disabled" }}
      />
    );
  }
  if (status === "completed") {
    return (
      <Iconify icon="mdi:check" width={14} sx={{ color: "success.main" }} />
    );
  }
  if (status === "error") {
    return <Iconify icon="mdi:close" width={14} sx={{ color: "error.main" }} />;
  }
  if (status === RETRIED) {
    return (
      <Iconify icon="mdi:refresh" width={14} sx={{ color: "text.disabled" }} />
    );
  }
  return null;
}

StatusIcon.propTypes = {
  status: PropTypes.string.isRequired,
};

export default function ToolCallCard({ toolCall }) {
  const theme = useTheme();
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const isDark = theme.palette.mode === "dark";

  // Support both snake_case (WebSocket streaming) and camelCase (API history)
  const call_id = toolCall.call_id;
  const tool_name = toolCall.tool_name;
  const tool_description = toolCall.tool_description;
  const params = toolCall.params;
  const status = toolCall.status;
  const result_summary = toolCall.result_summary;
  const result_full = toolCall.result_full;
  const step = toolCall.step;

  const isRunning = status === "running";
  const isError = status === "error";
  // A step recovered on a later call: real, readable, not a failure.
  const isRetried = status === RETRIED;
  const isCompleted = status === "completed" || isRetried;

  return (
    <Box
      sx={{
        border: 1,
        borderColor: isDark
          ? alpha(theme.palette.common.white, 0.08)
          : alpha(theme.palette.common.black, 0.08),
        borderRadius: "10px",
        overflow: "hidden",
        mb: 1,
        transition: "border-color 0.2s ease",
        "&:hover": {
          borderColor: isDark
            ? alpha(theme.palette.common.white, 0.16)
            : alpha(theme.palette.common.black, 0.16),
        },
      }}
    >
      {/* Header row */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1.5,
          py: 1,
          cursor: isCompleted || isError ? "pointer" : "default",
          userSelect: "none",
        }}
        onClick={() => {
          if (isCompleted || isError) {
            setDetailsExpanded((prev) => !prev);
          }
        }}
      >
        <StatusIcon status={status} />

        <Typography
          variant="body2"
          sx={{
            fontFamily:
              "'SF Mono', 'Fira Code', 'Fira Mono', Menlo, Consolas, monospace",
            fontWeight: 500,
            fontSize: 12.5,
            flex: 1,
            color: "text.secondary",
          }}
        >
          {tool_name}
        </Typography>

        {isRunning && (
          <Typography
            variant="caption"
            sx={{
              fontSize: 11,
              color: "text.disabled",
              fontStyle: "italic",
            }}
          >
            Running...
          </Typography>
        )}

        {(isCompleted || isError) && (
          <Iconify
            icon={detailsExpanded ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={16}
            sx={{ color: "text.disabled" }}
          />
        )}
      </Box>

      {/* Summary line */}
      {(isCompleted || isError) && result_summary && !detailsExpanded && (
        <Box sx={{ px: 1.5, pb: 1 }}>
          <Typography
            variant="caption"
            sx={{
              fontSize: 12,
              color: isError ? "error.main" : "text.disabled",
              lineHeight: 1.4,
              wordBreak: "break-word",
              overflowWrap: "break-word",
            }}
            component="div"
          >
            {result_summary}
          </Typography>
        </Box>
      )}

      {/* Expandable details */}
      <Collapse in={detailsExpanded}>
        <Box
          sx={{
            px: 1.5,
            pb: 1.5,
            borderTop: 1,
            borderColor: isDark
              ? alpha(theme.palette.common.white, 0.06)
              : alpha(theme.palette.common.black, 0.06),
          }}
        >
          {/* Tool description */}
          {tool_description && (
            <Typography
              variant="caption"
              sx={{
                display: "block",
                mt: 1,
                mb: 1,
                fontSize: 12,
                color: "text.disabled",
                fontStyle: "italic",
              }}
            >
              {tool_description}
            </Typography>
          )}

          {/* Params section */}
          {params && Object.keys(params).length > 0 && (
            <Box sx={{ mt: 1 }}>
              <Box
                onClick={(e) => {
                  e.stopPropagation();
                  setParamsExpanded((prev) => !prev);
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
                  width={14}
                  sx={{ color: "text.disabled" }}
                />
                <Typography
                  variant="caption"
                  sx={{
                    fontSize: 11,
                    color: "text.disabled",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}
                >
                  Parameters
                </Typography>
              </Box>
              <Collapse in={paramsExpanded}>
                <Box
                  component="pre"
                  sx={{
                    fontSize: 12,
                    fontFamily:
                      "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
                    bgcolor: isDark
                      ? alpha(theme.palette.common.white, 0.04)
                      : "grey.50",
                    color: "text.secondary",
                    borderRadius: "8px",
                    p: 1.5,
                    overflow: "auto",
                    maxHeight: 200,
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

          {/* Result summary in expanded view */}
          {result_summary && (
            <Box sx={{ mt: 1 }}>
              <Typography
                variant="caption"
                sx={{
                  fontSize: 11,
                  color: "text.disabled",
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  display: "block",
                  mb: 0.5,
                }}
              >
                Result
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  fontSize: 12,
                  color: isError ? "error.main" : "text.secondary",
                  lineHeight: 1.5,
                  wordBreak: "break-word",
                  overflowWrap: "break-word",
                }}
                component="div"
              >
                {result_summary}
              </Typography>
            </Box>
          )}

          {/* Full result */}
          {result_full && (
            <Box sx={{ mt: 1 }}>
              <Typography
                variant="caption"
                sx={{
                  fontSize: 11,
                  color: "text.disabled",
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  display: "block",
                  mb: 0.5,
                }}
              >
                Full output
              </Typography>
              <Box
                sx={{
                  maxHeight: 300,
                  overflow: "auto",
                  borderRadius: "8px",
                  bgcolor: isDark
                    ? alpha(theme.palette.common.white, 0.04)
                    : "grey.50",
                  p: 1.5,
                  border: 1,
                  borderColor: isDark
                    ? alpha(theme.palette.common.white, 0.06)
                    : alpha(theme.palette.common.black, 0.06),
                }}
              >
                <TextBlock content={result_full} />
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
