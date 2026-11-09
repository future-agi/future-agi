import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import { HEADER } from "../config-layout";

export default function Main({ children, sx, ...other }) {
  return (
    <Box
      component="main"
      sx={{
        flexGrow: 1,
        minWidth: 0,
        minHeight: 0,
        width: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "auto",
        height: {
          xs: `calc(100dvh - ${HEADER.H_MOBILE}px - var(--fa-proto-label-h, 0px))`,
          md: "calc(100dvh - var(--fa-proto-label-h, 0px))",
        },
        paddingBottom: "var(--fa-fab-gutter, 0px)",
        outline: "none",
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
