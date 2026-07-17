import React, { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { alpha, Box, IconButton, Stack, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import NodeSelectionPopper from "../../components/NodeSelectionPopper";
import {
  NODE_TYPE_CONFIG,
  NODE_TYPES,
  PORT_DIRECTION,
} from "../../utils/constants";
import useBaseNodeState from "./hooks/useBaseNodeState";
import useBaseNodeStyles from "./hooks/useBaseNodeStyles";
import useBaseNodeActions from "./hooks/useBaseNodeActions";
import EditableLabel from "./EditableLabel";
import StartIndicator from "./StartIndicator";
import "../agent-graph.css";

const handleBaseStyle = {
  width: 10,
  height: 10,
  background: "var(--bg-paper)",
};

const resolveThemeColor = (theme, token, fallback) => {
  if (typeof token !== "string" || token.length === 0) return fallback;
  if (token.startsWith("#") || token.startsWith("rgb")) return token;

  const [paletteKey, shade] = token.split(".");
  const paletteValue = theme.palette[paletteKey];

  return paletteValue?.[shade] ?? paletteValue?.main ?? fallback;
};

const BaseNode = ({
  id,
  type,
  data,
  isConnectable,
  selected: _UI_SELECTED_NODE,
}) => {
  const { label, preview, ports } = data;
  const outputPort = ports?.find((p) => p.direction === PORT_DIRECTION.OUTPUT);
  const outputPortLabel = outputPort?.display_name;
  const typeConfig = NODE_TYPE_CONFIG[type] ?? {};

  const state = useBaseNodeState({ id, data });
  const {
    hasIncomingEdge,
    hasOutgoingEdge,
    isRunning,
    isCompleted,
    isError,
    isWorkflowRunning,
  } = state;

  const { boxSx, borderColor, theme } = useBaseNodeStyles(state);
  const iconColor = resolveThemeColor(
    theme,
    typeConfig.color,
    theme.palette.orange?.[500] ?? theme.palette.primary.main,
  );

  const actions = useBaseNodeActions({ id, ...state });
  const {
    handleNodeClick,
    handleAddClick,
    handlePopperClose,
    handleNodeSelect,
    handleDeleteClick,
    popperOpen,
    addButtonRef,
  } = actions;

  return (
    <Box sx={{ position: "relative" }}>
      <Box onClick={handleNodeClick} sx={boxSx}>
        {/* Animated border for running state */}
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

        {/* Start indicator for nodes with no incoming edges */}
        {!hasIncomingEdge && (
          <StartIndicator
            isWorkflowRunning={isWorkflowRunning}
            isRunning={isRunning}
            isCompleted={isCompleted}
            isError={isError}
          />
        )}

        {/* Single input handle (left side) */}
        <Handle
          type="target"
          position={Position.Left}
          id="input"
          isConnectable={preview ? false : isConnectable}
          style={{
            ...handleBaseStyle,
            border: `1px solid ${borderColor}`,
            left: -5,
            top: "50%",
          }}
        />

        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{
            flex: 1,
            minWidth: 0,
            width: 200,
            maxWidth: 200,
          }}
        >
          <Box
            sx={{
              width: 20,
              height: 20,
              borderRadius: 0.5,
              border: "1px solid",
              borderColor: alpha(
                iconColor,
                theme.palette.mode === "dark" ? 0.42 : 0.28,
              ),
              backgroundColor: alpha(
                iconColor,
                theme.palette.mode === "dark" ? 0.18 : 0.08,
              ),
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <SvgColor
              src={typeConfig.iconSrc ?? "/assets/icons/ic_chat_single.svg"}
              sx={{
                width: 16,
                height: 16,
                bgcolor: iconColor,
              }}
            />
          </Box>
          <Typography
            typography="s2_1"
            fontWeight="fontWeightMedium"
            color="text.primary"
            noWrap
          >
            {label}
          </Typography>
          {!preview && !isWorkflowRunning && (
            <IconButton
              className="node-delete-btn"
              sx={{
                color: "red.500",
                bgcolor: "background.paper",
                borderRadius: 0.5,
                border: "1px solid",
                borderColor: "red.500",
                marginLeft: "auto",
                p: 0.5,
                position: "absolute",
                right: 4,
                top: "50%",
                transform: "translateY(-50%)",
                "&:hover": {
                  bgcolor: (theme) =>
                    theme.palette.mode === "dark"
                      ? alpha(theme.palette.red[800], 0.3)
                      : "red.50",
                },
              }}
              onClick={handleDeleteClick}
            >
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                sx={{
                  width: 16,
                  height: 16,
                }}
              />
            </IconButton>
          )}
        </Stack>

        {/* Single output handle (right side) */}
        <Handle
          type="source"
          position={Position.Right}
          id="output"
          isConnectable={preview ? false : isConnectable}
          style={{
            ...handleBaseStyle,
            border: `1px solid ${borderColor}`,
            right: -5,
            top: "50%",
          }}
        />
      </Box>

      {/* Editable output label — outside clickable box (hidden for agent nodes) */}
      {outputPortLabel && type !== NODE_TYPES.AGENT && (
        <EditableLabel
          nodeId={id}
          portId={outputPort?.id}
          label={outputPortLabel}
          preview={preview || isWorkflowRunning}
          sx={{
            left: "100%",
            ml: 2,
            top: "50%",
          }}
        />
      )}

      {/* Add button — only shown when node has no outgoing edges */}
      {!preview && !isWorkflowRunning && !hasOutgoingEdge && (
        <Box
          ref={addButtonRef}
          onClick={handleAddClick}
          sx={{
            position: "absolute",
            right: outputPortLabel && type !== NODE_TYPES.AGENT ? -140 : -50,
            top: "50%",
            transform: "translateY(-50%)",
            width: 24,
            height: 24,
            borderRadius: "50%",
            border: "1px solid",
            borderColor: "blue.500",
            backgroundColor: "background.paper",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            transition: "all 0.2s",
            "&:hover": {
              borderColor: "blue.600",
              borderStyle: "solid",
              bgcolor: (theme) =>
                theme.palette.mode === "dark" ? "black.800" : "blue.100",
            },
            opacity: isWorkflowRunning ? 0.6 : 1,
            pointerEvents: isWorkflowRunning ? "none" : "auto",
            zIndex: 30,
          }}
        >
          <SvgColor
            src="/assets/icons/ic_add.svg"
            sx={{
              width: 20,
              height: 20,
              bgcolor: "blue.500",
            }}
          />
        </Box>
      )}

      {!preview && (
        <NodeSelectionPopper
          open={popperOpen}
          anchorEl={addButtonRef.current}
          onClose={handlePopperClose}
          onNodeSelect={handleNodeSelect}
        />
      )}
    </Box>
  );
};

BaseNode.propTypes = {
  id: PropTypes.string.isRequired,
  type: PropTypes.string.isRequired,
  data: PropTypes.shape({
    label: PropTypes.string,
    preview: PropTypes.bool,
    config: PropTypes.object,
    ports: PropTypes.array,
  }).isRequired,
  isConnectable: PropTypes.bool,
  selected: PropTypes.bool,
};

const MemoizedBaseNode = memo(BaseNode);
export default MemoizedBaseNode;
