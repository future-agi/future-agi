import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import { alpha } from "@mui/material/styles";
import ListItemText from "@mui/material/ListItemText";
import ListItemButton from "@mui/material/ListItemButton";

import Label from "src/components/label";

// ----------------------------------------------------------------------

const renderParts = (parts, baseColor) =>
  parts.map((part, index) => (
    <Box
      key={index}
      component="span"
      sx={{
        color: part.highlight ? "primary.main" : baseColor,
      }}
    >
      {part.text}
    </Box>
  ));

export default function ResultItem({ title, path, groupLabel, onClickItem }) {
  return (
    <ListItemButton
      onClick={onClickItem}
      sx={{
        borderWidth: 1,
        borderStyle: "dashed",
        borderColor: "transparent",
        borderBottomColor: (theme) => theme.palette.divider,
        "&:hover": {
          borderRadius: 1,
          borderColor: (theme) => theme.palette.primary.main,
          backgroundColor: (theme) =>
            alpha(
              theme.palette.primary.main,
              theme.palette.action.hoverOpacity,
            ),
        },
      }}
    >
      <ListItemText
        primaryTypographyProps={{
          typography: "subtitle2",
          sx: { textTransform: "capitalize" },
        }}
        secondaryTypographyProps={{ typography: "caption" }}
        primary={renderParts(title, "text.primary")}
        secondary={renderParts(path, "text.secondary")}
      />

      {groupLabel && <Label color="info">{groupLabel}</Label>}
    </ListItemButton>
  );
}

ResultItem.propTypes = {
  groupLabel: PropTypes.string,
  onClickItem: PropTypes.func,
  path: PropTypes.array,
  title: PropTypes.array,
};
