import React, { useId } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

// One part of a Hub page: the flat bordered surface HubSection draws, with a
// real h2 (so a page's headings are in order), one line saying what the part
// is for, and at most one action at the top right. `dense` is a card of a
// narrow side column, such as the entry page's: a smaller heading, closer
// rows, and the action kept on the heading's line.
export default function HubPanel({
  title,
  description,
  action,
  children,
  testId,
  dense = false,
}) {
  const id = useId();
  const titleId = `${id}-title`;
  return (
    <Box
      component="section"
      aria-labelledby={titleId}
      data-testid={testId}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: dense ? 1.25 : 2,
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: dense ? "center" : "flex-start",
          justifyContent: "space-between",
          flexWrap: dense ? "nowrap" : "wrap",
          gap: dense ? 1 : 1.5,
          minWidth: 0,
        }}
      >
        <Box sx={{ flex: dense ? "1 1 auto" : "1 1 260px", minWidth: 0 }}>
          <Typography
            id={titleId}
            component="h2"
            typography={dense ? "s1" : "m3"}
            fontWeight="fontWeightSemiBold"
            color="text.primary"
          >
            {title}
          </Typography>
          {description && (
            <Typography
              typography="s1"
              color="text.secondary"
              sx={{ mt: 0.5, maxWidth: 760 }}
            >
              {description}
            </Typography>
          )}
        </Box>
        {action && (
          <Box sx={{ display: "flex", gap: 1, flexShrink: 0 }}>{action}</Box>
        )}
      </Box>
      {children}
    </Box>
  );
}

HubPanel.propTypes = {
  title: PropTypes.node.isRequired,
  description: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  testId: PropTypes.string,
  dense: PropTypes.bool,
};
