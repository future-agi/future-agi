import React from "react";
import { BaseEdge, EdgeLabelRenderer, useReactFlow } from "@xyflow/react";
import { Box, Typography, useTheme } from "@mui/material";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import ActionButtons from "../ActionButtons";
import { useGraphStore } from "../store/graphStore";
import CustomTooltip from "src/components/tooltip";
import { ShowComponent } from "src/components/show";

const getStepEdgePath = (sourceX, sourceY, targetX, targetY) => {
  const centerY = (targetY - sourceY) / 2 + sourceY;
  const centerX = (targetX - sourceX) / 2 + sourceX;

  const edgePath = `M ${sourceX} ${sourceY} L ${sourceX} ${centerY} L ${targetX} ${centerY} L ${targetX} ${targetY}`;

  return {
    path: edgePath,
    labelX: centerX,
    labelY: centerY,
  };
};

const getConditionEdgeColors = (theme, highlightColor) => {
  if (highlightColor === "success") {
    return {
      stroke: theme.palette.green[600],
      strokeWidth: 2,
    };
  } else if (highlightColor === "error") {
    return {
      stroke: theme.palette.red[600],
      strokeWidth: 2,
    };
  }
  return {
    stroke: theme.palette.blue[600],
    strokeWidth: 2,
  };
};

const ConditionEdge = (props) => {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    style = {},
    data,
    markerEnd,
  } = props;
  const { setEdges } = useReactFlow();

  const theme = useTheme();

  const uniqueClassName = `condition-edge-${id}`;
  const { setActiveEdge, activeEdgeId } = useGraphStore();

  const { stroke, strokeWidth } = getConditionEdgeColors(
    theme,
    data?.highlightColor,
  );

  const { path, labelX, labelY } = getStepEdgePath(
    sourceX,
    sourceY,
    targetX,
    targetY,
  );

  const onEdgeClick = (e) => {
    e.stopPropagation();
    setEdges((edges) => edges.filter((edge) => edge.id !== id));
    if (id === activeEdgeId) {
      setActiveEdge(null);
    }
  };

  return (
    <>
      <BaseEdge
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke,
          strokeWidth,
          ...style,
        }}
      />
      {/* `hideEmptyLabel` (opt-in, read-only flows): no condition, no label —
          instead of the editor's "No Condition Specified" prompt. */}
      {!(data?.hideEmptyLabel && !data?.prompt) && (
      <EdgeLabelRenderer>
        <CustomTooltip
          show={true}
          title={data?.prompt}
          arrow
          placement="bottom"
          size="small"
        >
          <Box
            sx={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              fontSize: 12,
              pointerEvents: "all",
              display: "flex",
              alignItems: "center",
              gap: 0.5,

              [`&:hover .${uniqueClassName}`]: {
                opacity: 1,
              },
            }}
            onClick={() => setActiveEdge(id)}
          >
            <Box
              sx={{
                backgroundColor: "background.paper",
                paddingX: "12px",
                paddingY: 1,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                maxWidth: "200px",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {data?.prompt ? (
                data?.prompt
              ) : (
                <Typography typography="s1" color="status.danger.fg">
                  No Condition Specified
                </Typography>
              )}
            </Box>
            <ShowComponent condition={data?.editMode}>
              <ActionButtons
                sx={{ opacity: 0 }}
                className={uniqueClassName}
                onClick={onEdgeClick}
                icon={<SvgColor src="/assets/icons/custom/delete.svg" />}
              />
            </ShowComponent>
          </Box>
        </CustomTooltip>
      </EdgeLabelRenderer>
      )}
    </>
  );
};

ConditionEdge.propTypes = {
  id: PropTypes.string,
  sourceX: PropTypes.number,
  sourceY: PropTypes.number,
  targetX: PropTypes.number,
  targetY: PropTypes.number,
  sourcePosition: PropTypes.string,
  targetPosition: PropTypes.string,
  style: PropTypes.object,
  data: PropTypes.object,
  markerEnd: PropTypes.object,
};

export default ConditionEdge;
