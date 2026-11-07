import React from "react";
import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Link from "@mui/material/Link";

import { RouterLink } from "src/routes/components";

// ----------------------------------------------------------------------

export default function BreadcrumbsLink({
  link,
  activeLast,
  disabled,
  lastLink,
}) {
  const isLastLink = lastLink === link.name;

  const styles = {
    typography: "s1",
    alignItems: "center",
    display: "inline-flex",
    color: "text.primary",
    fontWeight: "fontWeightRegular",

    ...(disabled &&
      !activeLast && {
        cursor: "default",
        pointerEvents: "none",
        color: "text.disabled",
      }),
  };

  const renderContent = (
    <>
      {link.icon && (
        <Box
          component="span"
          sx={{
            mr: (theme) => theme.spacing(1),
            display: "inherit",
            "& svg": { width: 20, height: 20 },
          }}
        >
          {link.icon}
        </Box>
      )}

      {link.name}
    </>
  );

  if (link?.href) {
    return (
      <Link component={RouterLink} href={link.href} sx={styles}>
        <Box
          sx={{
            fontWeight: isLastLink ? "fontWeightSemiBold" : "fontWeightMedium",
            color: isLastLink ? "primary.main" : "text.default",
          }}
        >
          {renderContent}
        </Box>
      </Link>
    );
  }

  if (link?.onClick) {
    return (
      <Link component={RouterLink} onClick={link?.onClick} sx={styles}>
        <Box
          sx={{
            fontWeight: isLastLink ? "fontWeightSemiBold" : "fontWeightMedium",
            color: isLastLink ? "primary.main" : "text.default",
          }}
        >
          {renderContent}
        </Box>
      </Link>
    );
  }

  return <Box sx={styles}> {renderContent} </Box>;
}

BreadcrumbsLink.propTypes = {
  activeLast: PropTypes.bool,
  disabled: PropTypes.bool,
  link: PropTypes.shape({
    href: PropTypes.string,
    icon: PropTypes.node,
    name: PropTypes.string,
    onClick: PropTypes.func,
  }),
  lastLink: PropTypes.string,
};
