import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";
import { Helmet } from "react-helmet-async";

import { RouterLink } from "src/routes/components";

import DeployIcon from "./DeployIcon";

// The frame every Omega tab uses (shell/OmegaPageFrame): an optional back
// link, a title with one line of description, at most one action group, then
// the content. Spacing and type match the host list pages (p: 2, m2 / s1).
export default function DeploymentsFrame({
  title,
  documentTitle,
  description,
  meta,
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
        gap: 2,
        p: 2,
      }}
    >
      <Helmet>
        <title>
          {documentTitle || (typeof title === "string" ? title : "Deployments")}
        </title>
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
            <DeployIcon name="chevronLeft" size={14} />
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
            {meta && (
              <Box
                sx={{
                  mt: 0.75,
                  display: "flex",
                  flexWrap: "wrap",
                  alignItems: "center",
                  gap: 1,
                }}
              >
                {meta}
              </Box>
            )}
          </Box>
          {action && (
            <Box
              sx={{ flexShrink: 0, display: "flex", flexWrap: "wrap", gap: 1 }}
            >
              {action}
            </Box>
          )}
        </Box>
      </Box>
      <Box
        sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
      >
        {children}
      </Box>
    </Box>
  );
}

DeploymentsFrame.propTypes = {
  title: PropTypes.node.isRequired,
  documentTitle: PropTypes.string,
  description: PropTypes.node,
  meta: PropTypes.node,
  action: PropTypes.node,
  back: PropTypes.shape({
    href: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
  }),
  children: PropTypes.node,
  testId: PropTypes.string,
};
