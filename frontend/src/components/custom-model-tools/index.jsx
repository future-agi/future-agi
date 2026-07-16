import React, { useState } from "react";
import { Box, IconButton, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import ShowModelTools from "./ShowModelTools";
import PropTypes from "prop-types";
import ToolsHoverState from "./ToolsHoverState";
import SvgColor from "../svg-color";

const CustomModelTools = ({
  isModalContainer = false,
  handleApply,
  tools,
  disableHover = false,
  disableClick = false,
  viewOnly = false,
  hoverPlacement = "bottom",
  onClick = () => {},
  label,
}) => {
  const [showModelTool, setShowModelTool] = useState(false);

  const handleOnClose = () => {
    setShowModelTool(false);
  };

  const handleOpenDropdown = () => {
    // View-only (e.g. version history): the button stays enabled so the
    // read-only tools hover renders, but clicking must not open the editor.
    if (viewOnly) return;
    if (!showModelTool) {
      onClick?.();
      setShowModelTool(true);
    }
  };

  return (
    <Box sx={{ height: "24px" }}>
      <CustomTooltip
        show={true}
        title={<ToolsHoverState tools={tools} disableHover={disableHover} />}
        placement={!disableHover ? hoverPlacement : "bottom"}
        arrow={disableHover}
        enterDelay={100}
        size={"small"}
        enterNextDelay={100}
        sx={{
          ...(!disableHover && {
            "& .MuiTooltip-tooltip": {
              padding: 0,
              width: "170px",
            },
          }),
        }}
      >
        <IconButton
          onClick={handleOpenDropdown}
          disabled={disableClick}
          disableRipple={viewOnly}
          sx={{
            borderRadius: "4px",
            backgroundColor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            padding: "4px",
            height: "32px",
            width: label ? "auto" : "44px",
            marginTop: isModalContainer ? "-4px" : 0,
            color: "text.primary",
            ...(viewOnly && { cursor: "default" }),
          }}
        >
          <SvgColor
            src="/assets/prompt/tools.svg"
            sx={{
              height: "20px",
              width: "20px",
              flexShrink: 0,
            }}
          />
          {tools?.length > 0 && (
            <Typography
              variant="s3"
              fontWeight={"fontWeightMedium"}
              color="text.primary"
              sx={{
                fontFamily: (theme) => theme.typography.fontFamily,
                marginLeft: 0.5,
              }}
            >
              ({tools?.length})
            </Typography>
          )}
          {label && (
            <Typography
              typography="s2_1"
              fontWeight={"fontWeightMedium"}
              sx={{
                ml: 0.5,
              }}
            >
              {label}
            </Typography>
          )}
        </IconButton>
      </CustomTooltip>
      <ShowModelTools
        open={showModelTool && !disableClick}
        onClose={handleOnClose}
        handleApply={handleApply}
        tools={tools}
      />
    </Box>
  );
};

export default CustomModelTools;

CustomModelTools.propTypes = {
  isModalContainer: PropTypes.bool,
  handleApply: PropTypes.func,
  tools: PropTypes.array,
  disableHover: PropTypes.bool,
  disableClick: PropTypes.bool,
  viewOnly: PropTypes.bool,
  hoverPlacement: PropTypes.string,
  onClick: PropTypes.func,
  label: PropTypes.string,
};
