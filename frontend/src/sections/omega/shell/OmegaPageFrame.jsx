import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";
import { Helmet } from "react-helmet-async";

import { RouterLink } from "src/routes/components";

import { OmegaIcon } from "./icons";

// The page frame every Omega tab uses: an optional back link, a title with
// one line of description, at most one primary action, then the content.
// Spacing and type follow the host's list pages (p: 2, m2 / s1).
export default function OmegaPageFrame({
  title,
  documentTitle,
  description,
  action,
  back,
  children,
  testId,
}) {
  return (
    <Box
      data-testid={testId}
      sx={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        p: 2,
      }}
    >
      <Helmet>
        <title>{documentTitle || title}</title>
      </Helmet>
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
            <OmegaIcon name="chevronLeft" size={14} />
            {back.label}
          </Link>
        )}
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
              fontWeight="fontWeightMedium"
              color="text.primary"
              sx={{ overflowWrap: "anywhere" }}
            >
              {title}
            </Typography>
            {description && (
              <Typography typography="s1" color="text.secondary">
                {description}
              </Typography>
            )}
          </Box>
          {action && <Box sx={{ flexShrink: 0 }}>{action}</Box>}
        </Box>
      </Box>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        {children}
      </Box>
    </Box>
  );
}

OmegaPageFrame.propTypes = {
  title: PropTypes.node.isRequired,
  documentTitle: PropTypes.string,
  description: PropTypes.node,
  action: PropTypes.node,
  back: PropTypes.shape({
    href: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
  }),
  children: PropTypes.node,
  testId: PropTypes.string,
};
