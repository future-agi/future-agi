import React, { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import { Box, Chip, Typography, useTheme } from "@mui/material";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { NODE_TYPES, useGraphStore } from "../store/graphStore";
import Label from "src/components/label";
import NodeHeader from "./NodeHeader";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import ActionButtons from "../ActionButtons";
import { getNodeColors } from "../common";

const ConversationNode = ({ data, isConnectable, id }) => {
  const deleteNode = useGraphStore((state) => state.deleteNode);
  const duplicateNode = useGraphStore((state) => state.duplicateNode);
  const setActiveNode = useGraphStore((state) => state.setActiveNode);
  const activeNodeId = useGraphStore((state) => state.activeNodeId);

  const { backgroundColor, borderColor, hoverBorderColor } = getNodeColors(
    data?.isStart,
    data?.highlightColor,
    activeNodeId === id,
    NODE_TYPES.CONVERSATION,
  );

  const uniqueClassName = `conversation-node-${id}`;

  const theme = useTheme();

  const handleDelete = (e) => {
    e.stopPropagation();
    deleteNode(id);
  };

  const handleDuplicate = (e) => {
    e.stopPropagation();
    duplicateNode(id);
  };

  return (
    <>
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
          [`&:hover .add-node-${id}`]: {
            opacity: 1,
          },
          [`&:hover .add-node-border-${id}`]: {
            opacity: 1,
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
            borderColor: theme.palette.primary.main,
          }}
        />
        <NodeHeader
          id={id}
          type="conversation"
          title={data?.name}
          badge={
            data?.isStart ? (
              <Label color="primary" variant="soft">
                Start
              </Label>
            ) : null
          }
        />
        <ShowComponent condition={data?.isGlobal}>
          <Chip
            icon={<Iconify icon="mdi:earth" width={14} />}
            label="Global"
            size="small"
            sx={{
              alignSelf: "flex-start",
              backgroundColor: "action.hover",
              color: "primary.main",
              "& .MuiChip-icon": {
                color: "primary.main",
              },
              "&:hover": {
                backgroundColor: "action.hover",
              },
            }}
          />
        </ShowComponent>
        <Box
          sx={{
            paddingY: "12px",
            paddingX: 2,
            flexDirection: "column",
            gap: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
            backgroundColor: "background.default",
          }}
        >
          <Typography typography="s1" fontWeight="fontWeightMedium">
            Prompt
          </Typography>
          <ShowComponent condition={data?.prompt}>
            <Typography typography="s1">{data?.prompt}</Typography>
          </ShowComponent>
          <ShowComponent condition={!data?.prompt}>
            <Typography typography="s1" color="red.500">
              No Prompt Specified
            </Typography>
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
            <ShowComponent condition={!data?.isStart}>
              <ActionButtons
                onClick={handleDelete}
                icon={
                  <SvgColor
                    src="/assets/icons/custom/delete.svg"
                    sx={{ color: "text.primary" }}
                  />
                }
              />
            </ShowComponent>
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

        <Handle
          type="source"
          position={Position.Bottom}
          isConnectable={isConnectable}
          style={{
            width: 12,
            height: 12,
            background: "var(--bg-paper)",
            border: "2px solid",
            borderColor: theme.palette.primary.main,
          }}
        />
        {/* <AddNode id={id} /> */}
      </Box>
    </>
  );
};

ConversationNode.propTypes = {
  data: PropTypes.object,
  isConnectable: PropTypes.bool,
  id: PropTypes.string,
};

export default memo(ConversationNode);
