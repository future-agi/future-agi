// R4 stub placeholder. Each shared stub renders this box, sized
// like the real component, until its owner lane replaces the stub's body:
// a dashed `background.neutral` box reading `Not built yet · lane <id> ·
// <component>`. No controls, no console output.
import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

export default function R4StubBox({
  lane,
  name,
  height,
  minHeight,
  width,
  inline = false,
  sx,
}) {
  return (
    <Box
      data-r4-stub={lane}
      data-r4-stub-name={name}
      sx={{
        display: inline ? "inline-flex" : "flex",
        alignItems: "center",
        justifyContent: "center",
        boxSizing: "border-box",
        width: width ?? (inline ? "auto" : "100%"),
        height,
        minHeight,
        px: inline ? 1 : 2,
        py: inline ? 0.25 : 1,
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: inline ? 0.75 : 1,
        bgcolor: "background.neutral",
        maxWidth: "100%",
        ...sx,
      }}
    >
      <Typography
        component="span"
        typography={inline ? "s3" : "s2"}
        color="text.disabled"
        noWrap
      >
        {`Not built yet · lane ${lane} · ${name}`}
      </Typography>
    </Box>
  );
}

R4StubBox.propTypes = {
  lane: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  minHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  width: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  inline: PropTypes.bool,
  sx: PropTypes.object,
};
