import { Box } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";

const EIGHT_STARS = "********";

// A list cell shows exactly the masked hint the server sent, which always
// holds a star. Any other value shows eight stars: the list never builds a
// hint of its own and never shows a full secret. A list cell has no copy
// action; the full secret is copied only in the one-time reveal after create.
const listText = (value) =>
  typeof value === "string" && value.includes("*") ? value : EIGHT_STARS;

const SecretKeyRenderer = ({ value }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      height: "100%",
      minWidth: 0,
    }}
  >
    <Box
      component="span"
      sx={{
        minWidth: 0,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {listText(value)}
    </Box>
  </Box>
);

SecretKeyRenderer.propTypes = {
  value: PropTypes.string,
};

export default SecretKeyRenderer;
