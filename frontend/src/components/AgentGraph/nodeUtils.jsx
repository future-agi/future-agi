import React from "react";
import { Handle, Position } from "@xyflow/react";
import { alpha, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { EXECUTION_STATUS } from "src/sections/agent-playground/utils/workflowExecution";
import { START_ID, END_ID } from "./layoutUtils";

// eslint-disable-next-line react-refresh/only-export-components
export const handleBaseStyle = {
  width: 8,
  height: 8,
  background: "var(--bg-paper)",
};

/**
 * @param {string} status - Node execution status
 * @param {object} theme - MUI theme
 * @param {boolean} isDark - Whether dark mode is active
 * @param {{ light: string, dark: string }} defaultColor - Default border color for idle/pending state
 */
// eslint-disable-next-line react-refresh/only-export-components
export const getStatusBorderColor = (status, theme, isDark, defaultColor) => {
  const s = status?.toLowerCase();
  switch (s) {
    case EXECUTION_STATUS.SUCCESS:
      return theme.palette.green[500];
    case EXECUTION_STATUS.RUNNING:
      return theme.palette.green[500];
    case EXECUTION_STATUS.FAILED:
    case EXECUTION_STATUS.ERROR:
      return theme.palette.red[500];
    case EXECUTION_STATUS.SKIPPED:
      return theme.palette.red[500];
    default:
      return isDark ? defaultColor.dark : defaultColor.light;
  }
};

// eslint-disable-next-line react-refresh/only-export-components
export const getStatusBackgroundColor = (status, theme, isDark) => {
  const s = status?.toLowerCase();
  switch (s) {
    case EXECUTION_STATUS.SUCCESS:
      return isDark
        ? alpha(theme.palette.green[500], 0.3)
        : theme.palette.green[50];
    case EXECUTION_STATUS.FAILED:
    case EXECUTION_STATUS.ERROR:
      return isDark
        ? alpha(theme.palette.red[700], 0.3)
        : theme.palette.red[50];
    case EXECUTION_STATUS.SKIPPED:
      return isDark
        ? alpha(theme.palette.red[500], 0.16)
        : alpha(theme.palette.red[500], 0.08);
    default:
      return null;
  }
};

// eslint-disable-next-line react-refresh/only-export-components
export const isSkippedExecutionStatus = (status) =>
  status?.toLowerCase() === EXECUTION_STATUS.SKIPPED;

/**
 * Run-canvas nodes that should not open the detail panel.
 * Start/end markers and skipped nodes are inert.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const isSelectableExecutionNode = (node) => {
  if (!node || node.id === START_ID || node.id === END_ID) return false;
  const status =
    node.data?.nodeExecution?.status ||
    node.nodeExecution?.status ||
    node.node_execution?.status;
  return !isSkippedExecutionStatus(status);
};

const PORT_LABEL_OFFSET = 14;
const PORT_LABEL_MAX_WIDTH = 200;
const PORT_LABEL_FONT_SIZE = 8;

const portLabelSx = (position, leftPct) => ({
  position: "absolute",
  [position === "top" ? "top" : "bottom"]: -PORT_LABEL_OFFSET,
  left: leftPct,
  transform: "translateX(-50%)",
  fontSize: PORT_LABEL_FONT_SIZE,
  color: "text.secondary",
  whiteSpace: "nowrap",
  maxWidth: PORT_LABEL_MAX_WIDTH,
  overflow: "hidden",
  textOverflow: "ellipsis",
  pointerEvents: "none",
});

/**
 * Renders input or output port handles with optional labels.
 * Falls back to a single centered handle when no ports exist.
 */
export function PortHandles({ ports, type, borderColor }) {
  const isInput = type === "input";
  const handleType = isInput ? "target" : "source";
  const position = isInput ? Position.Top : Position.Bottom;
  const labelPosition = isInput ? "top" : "bottom";

  if (!ports || ports.length === 0) {
    return (
      <Handle
        type={handleType}
        position={position}
        style={{
          ...handleBaseStyle,
          border: `1px solid ${borderColor}`,
        }}
        isConnectable={false}
      />
    );
  }

  return ports.map((port, i) => {
    const leftPct = `${((i + 1) / (ports.length + 1)) * 100}%`;
    const label = port.display_name || port.display_name;
    return (
      <React.Fragment key={port.id}>
        <Handle
          id={port.id}
          type={handleType}
          position={position}
          style={{
            ...handleBaseStyle,
            border: `1px solid ${borderColor}`,
            left: leftPct,
          }}
          isConnectable={false}
        />
        {label && (
          <Typography sx={portLabelSx(labelPosition, leftPct)}>
            {label}
          </Typography>
        )}
      </React.Fragment>
    );
  });
}

PortHandles.propTypes = {
  ports: PropTypes.array,
  type: PropTypes.oneOf(["input", "output"]).isRequired,
  borderColor: PropTypes.string.isRequired,
};
