import React, { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { Box, Typography, useTheme } from "@mui/material";
import SvgColor from "src/components/svg-color";
import { NODE_TYPES, useGraphStore } from "../store/graphStore";
import PropTypes from "prop-types";
import NodeHeader from "./NodeHeader";
import { ShowComponent } from "src/components/show";
import ActionButtons from "../ActionButtons";
import { getNodeColors } from "../common";

const EndCallNode = ({ id, data, isConnectable }) => {
  const deleteNode = useGraphStore((state) => state.deleteNode);
  const setActiveNode = useGraphStore((state) => state.setActiveNode);
  const activeNodeId = useGraphStore((state) => state.activeNodeId);
  const uniqueClassName = `end-call-node-${id}`;
  const duplicateNode = useGraphStore((state) => state.duplicateNode);
  const theme = useTheme();

  const { backgroundColor, borderColor, hoverBorderColor } = getNodeColors(
    false,
    data?.highlightColor,
    activeNodeId === id,
    NODE_TYPES.END,
  );

  const handleDelete = (e) => {
    e.stopPropagation();
    deleteNode(id);
  };

  const handleDuplicate = (e) => {
    e.stopPropagation();
    duplicateNode(id);
  };

  return (
    <Box
      sx={{
        width: 400,
        borderRadius: 0.5,
        borderStyle: "solid",
        borderWidth: activeNodeId === id ? "2px" : "1px",
        borderColor: borderColor,
        backgroundColor: backgroundColor,
        position: "relative",
        paddingY: 1,
        paddingX: "12px",
        display: "flex",
        flexDirection: "column",
        gap: 1,
        "&:hover": {
          borderColor: hoverBorderColor,
        },
        [`&:hover .${uniqueClassName}`]: {
          opacity: 1,
        },
      }}
      onClick={() => setActiveNode(id)}
    >
      <Handle
        type="target"
        position={Position.Top}
        isConnectable={isConnectable}
        style={{
          width: 12,
          height: 12,
          background: "var(--bg-paper)",
          border: "2px solid",
          borderColor: data?.isHighlighted
            ? theme.palette.green[600]
            : theme.palette.red[600],
        }}
      />
      <NodeHeader type="end" title={data?.name} nodeId={id} />
      <Box>
        <Typography typography="s1" fontWeight="fontWeightMedium">
          Message
        </Typography>
        <ShowComponent condition={!data?.prompt?.length}>
          <Typography typography="s2" color="red.500">
            No Message Specified
          </Typography>
        </ShowComponent>
        <ShowComponent condition={data?.prompt?.length}>
          <Typography typography="s2">{data?.prompt}</Typography>
        </ShowComponent>
      </Box>
      <ShowComponent condition={data?.editMode}>
        <Box
          sx={{
            opacity: 0,
            position: "absolute",
            top: 0,
            right: -44,
            display: "flex",
            gap: 1,
            flexDirection: "column",
          }}
          className={uniqueClassName}
        >
          <ActionButtons
            onClick={handleDelete}
            icon={
              <SvgColor
                src="/assets/icons/custom/delete.svg"
                sx={{ color: "text.primary" }}
              />
            }
          />
          <ActionButtons
            sx={{ opacity: 0 }}
            className={uniqueClassName}
            onClick={handleDuplicate}
            icon={
              <SvgColor
                src="/assets/icons/ic_copy.svg"
                sx={{ color: "text.primary" }}
              />
            }
          />
        </Box>
      </ShowComponent>
    </Box>
  );
};

export default memo(EndCallNode);

EndCallNode.propTypes = {
  id: PropTypes.string,
  data: PropTypes.object,
  isConnectable: PropTypes.bool,
};
