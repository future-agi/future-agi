import React, { memo } from "react";
import { Box, Stack, Tooltip, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import { NODE_TYPE_CONFIG } from "src/sections/agent-playground/utils/constants";
import {
  EXECUTION_STATUS,
  isSkippedStatus,
} from "src/sections/agent-playground/utils/workflowExecution";
import {
  getStatusBorderColor,
  getStatusBackgroundColor,
  getSkippedNodeTooltip,
  PortHandles,
} from "./nodeUtils";
import "./agent-graph-animations.css";

const getDefaultColor = (theme) => ({
  dark: theme.palette.blue[700],
  light: theme.palette.blue[200],
});

const SubgraphGroupNode = ({ data }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const { label, frontendNodeType, selected, nodeExecution, ports = [] } = data;
  const nodeStatus = nodeExecution?.status;
  const isRunning = nodeStatus?.toLowerCase() === EXECUTION_STATUS.RUNNING;
  const isSkipped = isSkippedStatus(nodeStatus);

  const config =
    NODE_TYPE_CONFIG[frontendNodeType] || NODE_TYPE_CONFIG.agent || {};

  const statusBorderColor = getStatusBorderColor(
    nodeStatus,
    theme,
    isDark,
    getDefaultColor(theme),
  );
  const borderColor = selected ? theme.palette.primary.main : statusBorderColor;
  const defaultBgColor = isDark
    ? "rgba(59, 130, 246, 0.04)"
    : "rgba(59, 130, 246, 0.03)";
  const statusBg = getStatusBackgroundColor(nodeStatus, theme, isDark);
  // Use a subtler tint for the large subgraph container
  const backgroundColor = statusBg
    ? statusBg.replace(/[\d.]+\)$/, "0.15)")
    : defaultBgColor;

  const inputPorts = ports.filter((p) => p.direction === "input");
  const outputPorts = ports.filter((p) => p.direction === "output");

  return (
    <Tooltip
      title={
        isSkipped ? getSkippedNodeTooltip(nodeExecution?.error_message) : ""
      }
      arrow
      placement="top"
    >
      <Box
        aria-disabled={isSkipped}
        sx={{
          width: "100%",
          height: "100%",
          borderRadius: 1,
          border: isRunning
            ? "none"
            : isSkipped
              ? "1.5px dashed"
              : "1.5px solid",
          borderColor,
          backgroundColor,
          position: "relative",
          opacity: isSkipped ? 0.4 : 1,
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
              zIndex: 1,
            }}
          >
            <rect
              x="0.75"
              y="0.75"
              width="calc(100% - 1.5px)"
              height="calc(100% - 1.5px)"
              rx="8"
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

        <PortHandles
          ports={inputPorts}
          type="input"
          borderColor={borderColor}
        />

        {/* Header label */}
        <Stack
          direction="row"
          spacing={0.75}
          alignItems="center"
          sx={{
            px: 1.5,
            py: 0.75,
            borderBottom: isRunning ? "none" : "1px solid",
            borderColor,
          }}
        >
          <Box
            sx={{
              width: 18,
              height: 18,
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
              src={config.iconSrc ?? "/assets/icons/navbar/ic_agents.svg"}
              sx={{
                width: 14,
                height: 14,
                bgcolor: config.color ?? "blue.600",
              }}
            />
          </Box>
          <Typography
            typography="s2_1"
            fontWeight="fontWeightMedium"
            color="text.secondary"
            noWrap
          >
            {label}
          </Typography>
        </Stack>

        <PortHandles
          ports={outputPorts}
          type="output"
          borderColor={borderColor}
        />
      </Box>
    </Tooltip>
  );
};

SubgraphGroupNode.displayName = "SubgraphGroupNode";

SubgraphGroupNode.propTypes = {
  data: PropTypes.shape({
    label: PropTypes.string,
    frontendNodeType: PropTypes.string,
    selected: PropTypes.bool,
    nodeExecution: PropTypes.shape({
      status: PropTypes.string,
      error_message: PropTypes.string,
    }),
    ports: PropTypes.array,
  }).isRequired,
};

const MemoizedSubgraphGroupNode = memo(SubgraphGroupNode);
MemoizedSubgraphGroupNode.displayName = "SubgraphGroupNode";
export default MemoizedSubgraphGroupNode;
