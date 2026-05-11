import React, { memo } from "react";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import { NODE_TYPE_CONFIG } from "src/sections/agent-playground/utils/constants";
import { EXECUTION_STATUS } from "src/sections/agent-playground/utils/workflowExecution";
import {
  getStatusBorderColor,
  getStatusBackgroundColor,
  PortHandles,
} from "./nodeUtils";
import "./agent-graph-animations.css";

const getDefaultColor = (theme) => ({
  dark: theme.palette.black[400],
  light: theme.palette.black[200],
});

const ExecutionNode = ({ data }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const { label, frontendNodeType, selected, nodeExecution, ports = [] } = data;
  const nodeStatus = nodeExecution?.status;
  const isPending = !nodeExecution;
  const isRunning = nodeStatus?.toLowerCase() === EXECUTION_STATUS.RUNNING;

  const config =
    NODE_TYPE_CONFIG[frontendNodeType] || NODE_TYPE_CONFIG.llm_prompt || {};

  const statusBorderColor = getStatusBorderColor(
    nodeStatus,
    theme,
    isDark,
    getDefaultColor(theme),
  );
  const borderColor = selected ? theme.palette.primary.main : statusBorderColor;
  const backgroundColor =
    getStatusBackgroundColor(nodeStatus, theme, isDark) ||
    theme.palette.background.paper;

  const inputPorts = ports.filter((p) => p.direction === "input");
  const outputPorts = ports.filter((p) => p.direction === "output");
  const evaluationResult = nodeExecution?.evaluation_result;
  const evaluationScore =
    typeof evaluationResult?.score === "number"
      ? Math.round(evaluationResult.score * 100)
      : null;

  return (
    <Box sx={{ position: "relative", opacity: isPending ? 0.4 : 1 }}>
      <PortHandles ports={inputPorts} type="input" borderColor={borderColor} />

      {/* Node body */}
      <Box
        sx={{
          minWidth: 200,
          borderRadius: 0.5,
          borderStyle: isRunning ? "none" : "solid",
          borderWidth: isRunning ? 0 : "1px",
          borderColor,
          backgroundColor,
          position: "relative",
          py: isRunning ? "9px" : 1,
          px: isRunning ? "13px" : 1.5,
          display: "flex",
          alignItems: "center",
          cursor: isPending ? "default" : "pointer",
          overflow: "visible",
          ...(selected && {
            borderColor: "primary.main",
          }),
        }}
      >
        {/* Animated dashed border for running state */}
        {isRunning && (
          <svg
            width="100%"
            height="100%"
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              pointerEvents: "none",
            }}
          >
            <rect
              x="0.75"
              y="0.75"
              width="calc(100% - 1.5px)"
              height="calc(100% - 1.5px)"
              rx="4"
              fill="none"
              stroke={theme.palette.green[500]}
              strokeWidth="1.5"
              strokeDasharray="8 4"
              strokeDashoffset="0"
              style={{
                animation: "dash-around 2s linear infinite",
              }}
            />
          </svg>
        )}

        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ flex: 1, minWidth: 0 }}
        >
          <Box
            sx={{
              width: 20,
              height: 20,
              borderRadius: 0.5,
              border: "1px solid",
              borderColor: "divider",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <SvgColor
              src={config.iconSrc ?? "/assets/icons/ic_chat_single.svg"}
              sx={{
                width: 16,
                height: 16,
                bgcolor: config.color ?? "orange.500",
              }}
            />
          </Box>
          <Stack sx={{ minWidth: 0, flex: 1 }} gap={0.25}>
            <Typography
              typography="s2_1"
              fontWeight="fontWeightMedium"
              color="text.primary"
              noWrap
            >
              {label}
            </Typography>
            {evaluationResult && (
              <Typography
                typography="s2"
                color={evaluationResult.passed ? "success.main" : "error.main"}
                noWrap
              >
                {evaluationResult.passed ? "Pass" : "Fail"}
                {evaluationScore !== null ? ` · ${evaluationScore}%` : ""}
              </Typography>
            )}
          </Stack>
        </Stack>
      </Box>

      <PortHandles
        ports={outputPorts}
        type="output"
        borderColor={borderColor}
      />
    </Box>
  );
};

ExecutionNode.propTypes = {
  data: PropTypes.shape({
    label: PropTypes.string,
    frontendNodeType: PropTypes.string,
    selected: PropTypes.bool,
    nodeExecution: PropTypes.shape({
      status: PropTypes.string,
      evaluation_result: PropTypes.object,
    }),
    ports: PropTypes.array,
  }).isRequired,
};

const MemoizedExecutionNode = memo(ExecutionNode);
MemoizedExecutionNode.displayName = "ExecutionNode";
export default MemoizedExecutionNode;
