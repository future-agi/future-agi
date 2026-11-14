import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Breadcrumbs from "@mui/material/Breadcrumbs";

import LinkItem from "./link-item";
import Iconify from "../iconify/iconify";

// ----------------------------------------------------------------------

export default function CustomBreadcrumbs({
  links,
  action,
  heading,
  moreLink,
  activeLast,
  sx,
  ...other
}) {
  const lastLink = links[links.length - 1].name;

  return (
    <Box
      sx={{
        backgroundColor: "background.paper",
        paddingX: (theme) => theme.spacing(1),
        minHeight: "30px",
        borderRadius: (theme) => theme.spacing(0.5),
        // paddingY: "4px",
        border: "1px solid",
        borderColor: "action.hover",
        ...sx,
      }}
    >
      <Stack direction="row" alignItems="center">
        <Box sx={{ flexGrow: 1 }}>
          {/* HEADING */}
          {heading && (
            <Typography variant="m1" fontWeight={600} gutterBottom>
              {heading}
            </Typography>
          )}

          {/* BREADCRUMBS */}
          {!!links.length && (
            <Breadcrumbs separator={<Separator />} {...other}>
              {links.map((link) => (
                <LinkItem
                  key={link.name || ""}
                  link={link}
                  activeLast={activeLast}
                  disabled={link.name === lastLink}
                  lastLink={lastLink}
                />
              ))}
            </Breadcrumbs>
          )}
        </Box>

        {action && <Box sx={{ flexShrink: 0 }}> {action} </Box>}
      </Stack>

      {/* MORE LINK */}
      {!!moreLink && (
        <Box sx={{ mt: 2 }}>
          {moreLink.map((href) => (
            <Link
              key={href}
              href={href}
              variant="body2"
              target="_blank"
              rel="noopener"
              sx={{ display: "table" }}
            >
              {href}
            </Link>
          ))}
        </Box>
      )}
    </Box>
  );
}

CustomBreadcrumbs.propTypes = {
  sx: PropTypes.object,
  action: PropTypes.node,
  links: PropTypes.array,
  heading: PropTypes.string,
  moreLink: PropTypes.array,
  activeLast: PropTypes.bool,
};

// ----------------------------------------------------------------------

function Separator() {
  return (
    <Iconify
      icon="solar:alt-arrow-right-linear"
      sx={{ color: "text.disabled" }}
    />
  );
}
