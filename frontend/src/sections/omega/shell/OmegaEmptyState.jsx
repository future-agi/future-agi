import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import SvgColor from "src/components/svg-color";

import { omegaIconSrc, OMEGA_ICON_NAMES } from "./icons";

// A calm empty state in the host's own EmptyLayout style (68 px bordered tile,
// gradient glyph, m3 title, s1 body) with at most one action. It never shows
// sample data.
export default function OmegaEmptyState({
  icon,
  title,
  description,
  action,
  headingComponent = "h2",
}) {
  return (
    <Box
      role="status"
      sx={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        px: 2,
        py: { xs: 6, sm: 8 },
      }}
    >
      <Box
        sx={{
          maxWidth: 447,
          width: "100%",
          textAlign: "center",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: "14px",
        }}
      >
        <Box
          sx={{
            width: 68,
            height: 68,
            borderRadius: "8px",
            border: "2px solid",
            borderColor: "divider",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <SvgColor
            src={omegaIconSrc(icon)}
            sx={{
              width: 36,
              height: 36,
              background: (theme) =>
                theme.palette.mode === "light"
                  ? "linear-gradient(135deg, var(--primary-main), #CF6BE8)"
                  : "linear-gradient(135deg, #FFFFFF, #E6E6E7)",
            }}
          />
        </Box>
        <Box>
          <Typography
            component={headingComponent}
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
              sx={{ mt: 0.5 }}
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

OmegaEmptyState.propTypes = {
  icon: PropTypes.oneOf(OMEGA_ICON_NAMES).isRequired,
  title: PropTypes.node.isRequired,
  description: PropTypes.node,
  action: PropTypes.node,
  /** The title's element: h2 inside a page, h1 when the state is the page. */
  headingComponent: PropTypes.oneOf(["h1", "h2", "h3"]),
};
