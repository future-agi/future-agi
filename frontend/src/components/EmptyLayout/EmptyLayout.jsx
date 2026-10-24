import React from "react";
import { Link } from "@mui/material";
import PropTypes from "prop-types";
import EmptyContent from "../empty-content";
import SvgColor from "../svg-color";

// Onboarding / full-area empty state with an optional docs link. Renders the
// Design System empty state (EmptyContent); kept for its link + icon-src API.
const EmptyLayout = ({
  title,
  description,
  link,
  linkText,
  action,
  icon,
  onLinkClick,
  sx = {},
  hideIcon = false,
}) => {
  return (
    <EmptyContent
      title={title}
      icon={
        icon && !hideIcon ? (
          <SvgColor src={icon} sx={{ width: 20, height: 20 }} />
        ) : undefined
      }
      description={
        description || linkText ? (
          <>
            {description}{" "}
            {linkText && (
              <Link
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                underline="always"
                color="primary.main"
                onClick={onLinkClick ? onLinkClick : () => {}}
              >
                {linkText}
              </Link>
            )}
          </>
        ) : undefined
      }
      action={action}
      sx={sx}
    />
  );
};

EmptyLayout.propTypes = {
  title: PropTypes.string,
  description: PropTypes.node,
  link: PropTypes.string,
  linkText: PropTypes.string,
  icon: PropTypes.string,
  action: PropTypes.node,
  onLinkClick: PropTypes.func,
  sx: PropTypes.object,
  hideIcon: PropTypes.bool,
};

export default EmptyLayout;
