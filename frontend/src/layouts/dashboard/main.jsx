import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";

export default function Main({ children, sx, ...other }) {
  return (
    <Box
      component="main"
      sx={{
        flexGrow: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        // Content sits straight on the page surface; a 1px line on the
        // sidebar's edge is the only division.
        height: "100vh",
        minWidth: 0,
        bgcolor: "background.paper",
        ...sx,
      }}
      {...other}
    >
      {children}
    </Box>
  );
}

Main.propTypes = {
  children: PropTypes.node,
  sx: PropTypes.object,
};
