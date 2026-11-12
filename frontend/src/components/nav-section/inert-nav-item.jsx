import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";

// Information, not a disabled control. Deliberately accept only
// display props: no link/button, handlers, tabIndex, MUI Tooltip or child
// routes. The mini rail adds only a native title attribute (hover text).
//
// Every withheld row is laid out the same way: the title on one line, and
// the info line ("Not available in this deployment") on its own line under
// it, free to wrap. Set beside the title, a one-line info took the open
// sidebar's width and squeezed the title to one letter per line.
export default function InertNavItem({ title, icon, info, mini = false }) {
  // The 48px rail shows both strings too, but must break words to fit them;
  // its hover text repeats them whole.
  const railTooltip =
    mini && title
      ? typeof info === "string"
        ? `${title}: ${info}`
        : title
      : undefined;
  return (
    <Box
      title={railTooltip}
      sx={{
        display: "flex",
        boxSizing: "border-box",
        flexDirection: mini ? "column" : "row",
        // Open sidebar: the icon sits on the title's line, not centred
        // across the title and the line under it.
        alignItems: mini ? "center" : "flex-start",
        gap: mini ? 0.5 : 0.75,
        px: mini ? 0.25 : 1,
        py: 0.75,
        mb: "2px",
        width: 1,
        minWidth: 0,
        maxWidth: mini ? 48 : "100%",
        color: "text.secondary",
        cursor: "default",
      }}
    >
      {icon && (
        <Box
          component="span"
          aria-hidden
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            width: 20,
            height: 20,
          }}
        >
          {icon}
        </Box>
      )}
      <Box
        component="span"
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: mini ? "center" : "stretch",
          gap: mini ? 0.5 : 0,
          flexGrow: mini ? 0 : 1,
          width: mini ? 1 : "auto",
          minWidth: 0,
          maxWidth: "100%",
        }}
      >
        <Box
          component="span"
          sx={{
            display: "block",
            minWidth: 0,
            maxWidth: "100%",
            textAlign: mini ? "center" : "left",
            // The 52px mini rail must show BOTH strings without hover,
            // clipping or ellipsis, so there the title may wrap.
            ...(mini
              ? {
                  fontSize: 11,
                  fontWeight: 500,
                  lineHeight: "16px",
                  overflowWrap: "anywhere",
                }
              : {
                  // One line, like every other sidebar row.
                  fontSize: 12,
                  fontWeight: 400,
                  lineHeight: "20px",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }),
          }}
        >
          {title}
        </Box>
        {info && (
          <Box
            component="span"
            className="inert-nav-info"
            sx={{
              display: "block",
              maxWidth: "100%",
              fontSize: 11,
              fontWeight: 400,
              lineHeight: mini ? "14px" : "16px",
              textAlign: mini ? "center" : "left",
              textTransform: "none",
              // Readable in full: the line wraps, never clips or truncates.
              whiteSpace: "normal",
              overflowWrap: mini ? "anywhere" : "break-word",
              // An element passed as info (a Label, say) wraps here too
              // instead of forcing one line.
              "& > *": {
                maxWidth: "100%",
                height: "auto",
                lineHeight: "inherit",
                whiteSpace: "normal",
                overflowWrap: "inherit",
                textAlign: "inherit",
              },
            }}
          >
            {info}
          </Box>
        )}
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
