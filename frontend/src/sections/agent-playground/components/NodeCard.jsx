import { Box, Typography, Stack } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import { NODE_TYPES } from "../utils/constants";

export default function NodeCard({
  node,
  onExpandClick,
  onNodeClick,
  showExpandIcon,
  isActive,
  readOnly,
}) {
  const isPromptNode = node?.id === NODE_TYPES.LLM_PROMPT;
  const shouldShowExpand = showExpandIcon && isPromptNode;

  const handleCardClick = (e) => {
    if (shouldShowExpand && onExpandClick) {
      onExpandClick(e);
    } else if (onNodeClick) {
      onNodeClick(node?.id, node?.node_template_id);
    }
  };

  const isClickable = shouldShowExpand || onNodeClick;

  return (
    <Box
      onClick={isClickable ? handleCardClick : undefined}
      sx={{
        backgroundColor: isActive ? "action.selected" : "transparent",
        borderRadius: "4px",
        cursor: isClickable && !readOnly ? "pointer" : "inherit",
        transition: "all 0.2s",
        padding: 0.5,
        width: "100%",
        overflow: "hidden",
        "&:hover": !readOnly
          ? {
              backgroundColor: isActive ? "action.selected" : "action.hover",
            }
          : {},
        "&:active": !readOnly
          ? {
              backgroundColor: "action.selected",
            }
          : {},
      }}
    >
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <Box
          sx={{
            width: 36,
            height: 36,
            borderRadius: "4px",
            border: "1px solid",
            borderColor: "border.default",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <SvgColor
            src={node?.iconSrc}
            sx={{
              width: 20,
              height: 20,
              bgcolor: node?.color,
            }}
          />
        </Box>
        <Stack sx={{ flex: 1, overflow: "hidden" }} gap={0}>
          <Typography
            typography="s2_1"
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            {node?.title}
          </Typography>
          <CustomTooltip
            show
            title={node?.description || ""}
            size="small"
            arrow
            placement="right"
          >
            <span>
              <Typography
                typography="s2"
                color={"text.secondary"}
                fontWeight={"fontWeightRegular"}
                sx={{
                  overflow: "clip",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  display: "block",
                }}
              >
                {node?.description}
              </Typography>
            </span>
          </CustomTooltip>
        </Stack>
        {shouldShowExpand && (
          <Box
            sx={{
              p: 0.5,
              alignSelf: "center",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <SvgColor
              src="/assets/icons/custom/lucide--chevron-right.svg"
              sx={{
                width: 16,
                height: 16,
                bgcolor: "text.secondary",
              }}
            />
          </Box>
        )}
      </Stack>
    </Box>
  );
}

NodeCard.propTypes = {
  node: PropTypes.shape({
    id: PropTypes.string.isRequired,
    title: PropTypes.string.isRequired,
    description: PropTypes.string.isRequired,
    iconSrc: PropTypes.string.isRequired,
    color: PropTypes.string.isRequired,
    node_template_id: PropTypes.string,
  }).isRequired,
  onExpandClick: PropTypes.func,
  onNodeClick: PropTypes.func,
  showExpandIcon: PropTypes.bool,
  isActive: PropTypes.bool,
  readOnly: PropTypes.bool,
};

NodeCard.defaultProps = {
  showExpandIcon: false,
  isActive: false,
};
