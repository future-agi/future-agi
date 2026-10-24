import React, { useState } from "react";
import ButtonBase from "@mui/material/ButtonBase";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import Iconify from "src/components/iconify";
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
      <Iconify
        icon="solar:check-linear"
        width={14}
        sx={{ color: "success.main" }}
      />
    );
  }
  if (status === "error") {
    return (
      <Iconify
        icon="solar:close-linear"
        width={14}
        sx={{ color: "error.main" }}
      />
    );
  }
  return null;
}

StatusIcon.propTypes = {
  status: PropTypes.string.isRequired,
};

export default function ToolCallCard({ toolCall }) {
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [paramsExpanded, setParamsExpanded] = useState(false);

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
  const isCompleted = status === "completed";

  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
        mb: 1,
        transition: "border-color 0.2s ease",
        "&:hover": {
          borderColor: "border.default",
        },
      }}
    >
      {/* Header row */}
      <ButtonBase
        component="div"
        sx={{
          justifyContent: "flex-start",
          textAlign: "left",
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
            fontSize: 13,
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
              fontSize: 12,
              color: "text.disabled",
              fontStyle: "italic",
            }}
          >
            Running...
          </Typography>
        )}

        {(isCompleted || isError) && (
          <Iconify
            icon={
              detailsExpanded
                ? "solar:alt-arrow-up-linear"
                : "solar:alt-arrow-down-linear"
            }
            width={16}
            sx={{ color: "text.disabled" }}
          />
        )}
      </ButtonBase>

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
            borderColor: "divider",
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
              <ButtonBase
                component="div"
                onClick={(e) => {
                  e.stopPropagation();
                  setParamsExpanded((prev) => !prev);
                }}
                sx={{
                  justifyContent: "flex-start",
                  textAlign: "left",
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
                    paramsExpanded
                      ? "solar:alt-arrow-down-linear"
                      : "solar:alt-arrow-right-linear"
                  }
                  width={14}
                  sx={{ color: "text.disabled" }}
                />
                <Typography
                  variant="caption"
                  sx={{
                    fontSize: 12,
                    color: "text.disabled",
                    fontWeight: 600,
                    textTransform: "none",
                    letterSpacing: 0,
                  }}
                >
                  Parameters
                </Typography>
              </ButtonBase>
              <Collapse in={paramsExpanded}>
                <Box
                  component="pre"
                  sx={{
                    fontSize: 12,
                    fontFamily:
                      "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
                    bgcolor: "background.neutral",
                    color: "text.secondary",
                    borderRadius: "8px",
                    p: 1.5,
                    overflow: "auto",
                    maxHeight: 200,
                    m: 0,
                    border: 1,
                    borderColor: "divider",
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
                  fontSize: 12,
                  color: "text.disabled",
                  fontWeight: 600,
                  textTransform: "none",
                  letterSpacing: 0,
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
                  fontSize: 12,
                  color: "text.disabled",
                  fontWeight: 600,
                  textTransform: "none",
                  letterSpacing: 0,
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
                  bgcolor: "background.neutral",
                  p: 1.5,
                  border: 1,
                  borderColor: "divider",
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
