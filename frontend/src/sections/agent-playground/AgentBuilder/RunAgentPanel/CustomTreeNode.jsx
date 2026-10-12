import * as React from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { alpha, Stack, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import { getNodeConfig } from "./common";
import { formatMs } from "src/utils/utils";
import Iconify from "src/components/iconify";

/**
 * Custom TreeNode component that matches the existing MUI-based UI
 * @param {{
 *   node: import("../../../../TreeView/types").TreeNodeData;
 *   depth: number;
 *   isSelected: boolean;
 *   isExpanded: boolean;
 *   hasChildren: boolean;
 *   onSelect: () => void;
 *   onToggle: (e: React.MouseEvent) => void;
 * }} props
 */
export const CustomTreeNode = ({
  node,
  depth,
  isSelected,
  isExpanded,
  hasChildren,
  onSelect,
  onToggle,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const { type, name, duration, cost, tokens } = node || {};
  const config = getNodeConfig(type) || {};

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        flexGrow: 1,
        minWidth: 0,
        gap: 2,
        padding: "8px 16px",
        margin: "4px 0",
        marginLeft: `${depth * 40}px`,
        border: "1px solid",
        borderColor: isSelected ? "green.500" : "transparent",
        borderRadius: 0.5,
        backgroundColor: isSelected ? "green.o5" : "transparent",
        cursor: "pointer",
        transition: "background-color 0.2s ease",
        "&:hover": {
          backgroundColor: isSelected
            ? "green.o10"
            : isDark
              ? alpha(theme.palette.divider, 0.5)
              : "grey.100",
        },
      }}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
    >
      {/* Node Icon */}
      <Box
        sx={{
          width: 20,
          height: 20,
          borderRadius: 0.5,
          backgroundColor: "background.paper",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          border: "1px solid",
          borderColor: "divider",
          zIndex: 1,
        }}
      >
        <SvgColor
          src={config.iconSrc}
          sx={{
            width: 16,
            height: 16,
            bgcolor: config.color,
          }}
        />
      </Box>

      {/* Node Name and Metadata */}
      <Stack gap={0} sx={{ flex: 1, minWidth: 0 }}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: 1,
            width: "100%",
          }}
        >
          <Typography
            typography={"s2_1"}
            fontWeight={"fontWeightMedium"}
            color={"text.primary"}
          >
            {name}
          </Typography>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              flexShrink: 0,
            }}
          >
            {/* Duration */}
            <Box
              component="span"
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                gap: 0.5,
              }}
            >
              <SvgColor
                sx={{
                  height: 12,
                  width: 12,
                  bgcolor: "text.disabled",
                }}
                src="/assets/icons/navbar/ic_new_clock.svg"
              />
              <Typography color="text.disabled" typography={"s3"}>
                {formatMs(duration ?? 0)}
              </Typography>
            </Box>
            {/* Tokens */}
            <Box
              component="span"
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                gap: 0.5,
              }}
            >
              <SvgColor
                sx={{
                  height: 12,
                  width: 12,
                  bgcolor: "text.disabled",
                }}
                src="/assets/icons/ic_tokens.svg"
              />
              <Typography color="text.disabled" typography={"s3"}>
                {(tokens ?? 0).toLocaleString()}
              </Typography>
            </Box>

            {/* Cost */}
            <Box
              component="span"
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                gap: 0.5,
              }}
            >
              <SvgColor
                sx={{
                  height: 12,
                  width: 12,
                  bgcolor: "text.disabled",
                }}
                src="/assets/icons/components/ic_cost.svg"
              />
              <Typography color="text.disabled" typography={"s3"}>
                {(cost ?? 0).toFixed(4)}
              </Typography>
            </Box>
          </Box>
        </Box>
      </Stack>

      {/* Expand/Collapse Button */}
      {hasChildren && (
        <Box
          component="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggle(e);
          }}
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 32,
            height: 32,
            borderRadius: 1,
            border: "none",
            background: "transparent",
            cursor: "pointer",
            flexShrink: 0,
            transition: "background-color 0.2s ease",
            "&:hover": {
              backgroundColor: "action.hover",
            },
          }}
        >
          <Iconify
            icon={isExpanded ? "tabler:chevron-up" : "tabler:chevron-down"}
            width={20}
            color="text.secondary"
          />
        </Box>
      )}
    </Box>
  );
};

CustomTreeNode.propTypes = {
  node: PropTypes.object.isRequired,
  depth: PropTypes.number,
  isSelected: PropTypes.bool,
  isExpanded: PropTypes.bool,
  hasChildren: PropTypes.bool,
  onSelect: PropTypes.func.isRequired,
  onToggle: PropTypes.func,
};

