import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";
import { Helmet } from "react-helmet-async";

import { RouterLink } from "src/routes/components";

import HubIcon from "./HubIcon";

// The frame every Hub page uses: an optional back link, a title with one line
// of description, at most one action group, then the content. It matches the
// other Omega tabs (p: 2, m2 title, s1 description).
export default function HubFrame({
  title,
  documentTitle,
  description,
  action,
  back,
  children,
  testId,
  maxWidth = 1280,
}) {
  return (
    <Box
      data-testid={testId}
      sx={{
        flex: 1,
        minWidth: 0,
        width: "100%",
        maxWidth,
        mx: "auto",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        p: { xs: 2, md: 3 },
      }}
    >
      <Helmet>
        <title>{`${documentTitle || (typeof title === "string" ? title : "Hub")} · Omega Hub`}</title>
      </Helmet>
      {(title || back) && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
          {back && (
            <Link
              component={RouterLink}
              href={back.href}
              underline="none"
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.5,
                alignSelf: "flex-start",
                typography: "s2",
                fontWeight: 500,
                color: "text.secondary",
                "&:hover": { color: "text.primary" },
              }}
            >
              <HubIcon name="chevronLeft" size={14} />
              {back.label}
            </Link>
          )}
          {title && (
            <Box
              sx={{
                display: "flex",
                alignItems: { xs: "flex-start", sm: "center" },
                justifyContent: "space-between",
                flexDirection: { xs: "column", sm: "row" },
                gap: 1.5,
              }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  component="h1"
                  typography="m2"
                  fontWeight="fontWeightSemiBold"
                  color="text.primary"
                  sx={{ overflowWrap: "anywhere" }}
                >
                  {title}
                </Typography>
                {description && (
                  <Typography typography="s1" color="text.secondary" sx={{ mt: 0.25 }}>
                    {description}
                  </Typography>
                )}
              </Box>
              {action && (
                <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", flexShrink: 0 }}>
                  {action}
                </Box>
              )}
            </Box>
          )}
        </Box>
      )}
      {children}
    </Box>
  );
}

HubFrame.propTypes = {
  title: PropTypes.node,
  documentTitle: PropTypes.string,
  description: PropTypes.node,
  action: PropTypes.node,
  back: PropTypes.shape({ href: PropTypes.string, label: PropTypes.string }),
  children: PropTypes.node,
  testId: PropTypes.string,
  maxWidth: PropTypes.number,
};
