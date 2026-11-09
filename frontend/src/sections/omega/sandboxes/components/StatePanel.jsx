import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import SandboxIcon, { SANDBOX_ICON_NAMES } from "./SandboxIcon";

// A calm, centred state (empty, unavailable, refused, not found) in the host's
// EmptyLayout proportions: a bordered tile with one glyph, a title, one line
// of body and at most one action. It never shows sample data.
export default function StatePanel({
  icon = "box",
  title,
  description,
  action,
  compact = false,
  testId,
}) {
  return (
    <Box
      role="status"
      data-testid={testId}
      sx={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        px: 2,
        py: compact ? 4 : { xs: 6, sm: 8 },
      }}
    >
      <Box
        sx={{
          maxWidth: 460,
          width: "100%",
          textAlign: "center",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 1.5,
        }}
      >
        <Box
          sx={{
            width: compact ? 44 : 56,
            height: compact ? 44 : 56,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "primary.main",
            bgcolor: "background.paper",
          }}
        >
          <SandboxIcon name={icon} size={compact ? 20 : 24} />
        </Box>
        <Box>
          <Typography
            component="h2"
            typography="m3"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            {title}
          </Typography>
          {description && (
            <Typography
              typography="s1"
              color="text.secondary"
              component="p"
              sx={{ mt: 0.5, overflowWrap: "anywhere" }}
            >
              {description}
            </Typography>
          )}
        </Box>
        {action}
      </Box>
    </Box>
  );
}

StatePanel.propTypes = {
  icon: PropTypes.oneOf(SANDBOX_ICON_NAMES),
  title: PropTypes.node.isRequired,
  description: PropTypes.node,
  action: PropTypes.node,
  compact: PropTypes.bool,
  testId: PropTypes.string,
};
