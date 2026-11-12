import React from "react";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";
import { getColorMap } from "src/sections/workbench/createPrompt/VersionHistory/common";

/**
 * VersionTagChip — a dataset version tag (`golden-v3`), read-only, in the
 * Prompts version-history label grammar: the `PromptLabel` pill shape
 * coloured by `getColorMap` (PRODUCT-SPEC §4, §3.5).
 *
 * Props (frozen):
 * @param {string} props.name  tag name
 * @param {"small"|"medium"} [props.size="small"]  small = 20 px, medium = 24 px
 */
const VersionTagChip = ({ name, size = "small" }) => {
  const theme = useTheme();
  if (!name) return null;
  const c = getColorMap(name, theme);
  const small = size === "small";
  return (
    <Box
      component="span"
      title={name}
      sx={{
        display: "inline-flex",
        alignItems: "center",
        maxWidth: 160,
        height: small ? 20 : 24,
        px: small ? 1 : 1.5,
        borderRadius: "100px",
        bgcolor: c.backgroundColor,
        color: c.color,
        typography: small ? "s3" : "s2",
        fontWeight: 500,
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
        flexShrink: 0,
      }}
    >
      <Box
        component="span"
        sx={{ overflow: "hidden", textOverflow: "ellipsis" }}
      >
        {name}
      </Box>
    </Box>
  );
};

VersionTagChip.propTypes = {
  name: PropTypes.string,
  size: PropTypes.oneOf(["small", "medium"]),
};

export default VersionTagChip;
