import React, { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { alpha, Box, IconButton, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import NodeSelectionPopper from "../../components/NodeSelectionPopper";
import { NODE_TYPE_CONFIG, NODE_TYPES } from "../../utils/constants";
import useBaseNodeState from "./hooks/useBaseNodeState";
import useBaseNodeStyles from "./hooks/useBaseNodeStyles";
import useBaseNodeActions from "./hooks/useBaseNodeActions";
import StartIndicator from "./StartIndicator";
import "../agent-graph.css";

const handleBaseStyle = {
  width: 10,
  height: 10,
  background: "var(--bg-paper)",
};

/**
 * ConditionalNode
 *
 * Visually identical to BaseNode but exposes two output handles:
 *   - "true"  (top-right)  — taken when the condition evaluates to truthy
 *   - "false" (bottom-right) — taken when the condition evaluates to falsy
 *
 * The condition itself is configured in ConditionalNodeForm via the node drawer.
 */
const ConditionalNode = ({
  id,
  data,
  isConnectable,
  selected: _UI_SELECTED_NODE,
}) => {
  const { label, preview } = data;
  const typeConfig = NODE_TYPE_CONFIG[NODE_TYPES.CONDITIONAL] ?? {};
  const iconColor = typeConfig.color ?? "purple.500";

  const state = useBaseNodeState({ id, data });
  const {
    hasIncomingEdge,
    isRunning,
    isCompleted,
    isError,
    isWorkflowRunning,
  } = state;

  const { boxSx, borderColor, theme } = useBaseNodeStyles(state);

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
              style={{ animation: "dash-around 2s linear infinite" }}
            />
          </svg>
        )}

        {!hasIncomingEdge && (
          <StartIndicator
            isWorkflowRunning={isWorkflowRunning}
            isRunning={isRunning}
            isCompleted={isCompleted}
            isError={isError}
          />
        )}

        {/* Input handle */}
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
          sx={{ flex: 1, minWidth: 0, width: 200, maxWidth: 200 }}
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
              src={typeConfig.iconSrc ?? "/assets/icons/ic_branch.svg"}
              sx={{ width: 16, height: 16, bgcolor: iconColor }}
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
                  bgcolor: (t) =>
                    t.palette.mode === "dark"
                      ? alpha(t.palette.red[800], 0.3)
                      : "red.50",
                },
              }}
              onClick={handleDeleteClick}
            >
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                sx={{ width: 16, height: 16 }}
              />
            </IconButton>
          )}
        </Stack>

        {/* True output handle — top-right */}
        <Handle
          type="source"
          position={Position.Right}
          id="true"
          isConnectable={preview ? false : isConnectable}
          style={{
            ...handleBaseStyle,
            border: `1px solid ${borderColor}`,
            right: -5,
            top: "30%",
          }}
        />

        {/* False output handle — bottom-right */}
        <Handle
          type="source"
          position={Position.Right}
          id="false"
          isConnectable={preview ? false : isConnectable}
          style={{
            ...handleBaseStyle,
            border: `1px solid ${borderColor}`,
            right: -5,
            top: "70%",
          }}
        />
      </Box>

      {/* Branch labels */}
      <Typography
        variant="caption"
        sx={{
          position: "absolute",
          right: -40,
          top: "22%",
          color: "success.main",
          fontSize: 10,
          pointerEvents: "none",
        }}
      >
        true
      </Typography>
      <Typography
        variant="caption"
        sx={{
          position: "absolute",
          right: -42,
          top: "62%",
          color: "error.main",
          fontSize: 10,
          pointerEvents: "none",
        }}
      >
        false
      </Typography>

      {!preview && !isWorkflowRunning && (
        <Box
          ref={addButtonRef}
          onClick={handleAddClick}
          sx={{
            position: "absolute",
            right: -50,
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
              bgcolor: (t) =>
                t.palette.mode === "dark" ? "black.800" : "blue.100",
            },
            zIndex: 30,
          }}
        >
          <SvgColor
            src="/assets/icons/ic_add.svg"
            sx={{ width: 20, height: 20, bgcolor: "blue.500" }}
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

ConditionalNode.propTypes = {
  id: PropTypes.string.isRequired,
  data: PropTypes.object.isRequired,
  isConnectable: PropTypes.bool,
  selected: PropTypes.bool,
};

export default memo(ConditionalNode);
