import { Box, Divider, Typography } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";
import { GRAPH_NODES } from "./common";
import PropTypes from "prop-types";
import { LoadingButton } from "@mui/lab";

const GraphBuilderLeftBar = ({ onSave, saveLoading, agentType }) => {
  return (
    <Box
      sx={{
        width: "271px",
        paddingY: "12px",
        paddingX: 2,
        borderRight: "1px solid",
        borderColor: "divider",
        gap: "10px",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box>
        <Typography typography="m3" fontWeight="fontWeightMedium">
          Basic Information
        </Typography>
        <Typography typography="s2_1" color="text.secondary">
          Configure the basic settings for your agent
        </Typography>
      </Box>
      <Box>
        <LoadingButton
          size="small"
          color="primary"
          variant="contained"
          onClick={onSave}
          startIcon={
            <SvgColor
              src="/assets/prompt/saveDefault.svg"
              sx={{ width: "16px", height: "16px" }}
            />
          }
          loading={saveLoading}
        >
          Save flow
        </LoadingButton>
      </Box>
      <Divider sx={{ color: "common.white" }} />
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        {GRAPH_NODES?.reduce((acc, node) => {
          if (node?.agentType !== "all" && node?.agentType !== agentType) {
            return acc;
          }

          const { type, name, description, icon, color, backgroundColor } =
            node;

          const onDragStart = (event, nodeType) => {
            event.dataTransfer.setData("application/reactflow", nodeType);
            event.dataTransfer.effectAllowed = "move";
          };

          acc.push(
            <Box
              key={type}
              draggable
              onDragStart={(event) => onDragStart(event, type)}
              sx={{
                padding: 1,
                borderRadius: "2px",
                border: "1px solid",
                borderColor: "divider",
                display: "flex",
                gap: 1,
                alignItems: "center",
                cursor: "grab",
                "&:hover": {
                  backgroundColor: "background.default",
                },
                "&:active": {
                  cursor: "grabbing",
                },
              }}
            >
              <Box
                sx={{
                  backgroundColor,
                  padding: 1,
                  borderRadius: "2px",
                  width: "24px",
                  height: "24px",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <SvgColor
                  src={icon}
                  sx={{
                    width: "16px",
                    height: "16px",
                    color,
                    flexShrink: 0,
                  }}
                />
              </Box>
              <Box>
                <Typography typography="s2" fontWeight="fontWeightMedium">
                  {name}
                </Typography>
                <Typography typography="s3" color="text.secondary">
                  {description}
                </Typography>
              </Box>
            </Box>,
          );

          return acc;
        }, [])}
      </Box>
    </Box>
  );
};

GraphBuilderLeftBar.propTypes = {
  onSave: PropTypes.func,
  saveLoading: PropTypes.bool,
  agentType: PropTypes.string,
};

export default GraphBuilderLeftBar;
