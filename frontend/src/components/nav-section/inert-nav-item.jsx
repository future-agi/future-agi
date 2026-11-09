import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

// RL 1808: information, not a disabled control. Deliberately accept only
// display props: no link/button, handlers, tabIndex, tooltip or child routes.
export default function InertNavItem({ title, icon, info, mini = false }) {
  return (
    <Box
      sx={{
        display: "flex",
        boxSizing: "border-box",
        flexDirection: mini ? "column" : "row",
        alignItems: "center",
        gap: mini ? 0.5 : 0.75,
        px: mini ? 0.25 : 1,
        py: 0.75,
        mb: "2px",
        width: 1,
        minWidth: 0,
        maxWidth: mini ? 48 : "100%",
        color: "text.secondary",
        cursor: "default",
        // The 52px mini rail must show BOTH strings without hover, clipping
        // or ellipsis. Its standard Label may wrap instead of forcing nowrap.
        ...(mini && {
          "& > .inert-nav-info > *": {
            maxWidth: "100%",
            height: "auto",
            minHeight: 24,
            px: 0.25,
            fontSize: 11,
            lineHeight: "14px",
            whiteSpace: "normal",
            overflowWrap: "anywhere",
            textAlign: "center",
          },
        }),
      }}
    >
      {icon && (
        <Box
          component="span"
          aria-hidden
          sx={{ display: "flex", flexShrink: 0, width: 20, height: 20 }}
        >
          {icon}
        </Box>
      )}
      <Box
        component="span"
        sx={{
          minWidth: 0,
          maxWidth: "100%",
          flexGrow: mini ? 0 : 1,
          fontSize: mini ? 11 : 13,
          fontWeight: 500,
          lineHeight: "16px",
          textAlign: mini ? "center" : "left",
          overflowWrap: "anywhere",
        }}
      >
        {title}
      </Box>
      <Box
        component="span"
        className="inert-nav-info"
        sx={{ display: "flex", maxWidth: "100%" }}
      >
        {info}
      </Box>
    </Box>
  );
}

InertNavItem.propTypes = {
  title: PropTypes.string,
  icon: PropTypes.node,
  info: PropTypes.node,
  mini: PropTypes.bool,
};
