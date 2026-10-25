import React from "react";
import PropTypes from "prop-types";
import EmptyState from "../empty-state";
import SvgColor from "../svg-color";

// First-run empty state with an optional docs link. Renders the Design System
// EmptyState; kept for its icon-src + link/linkText API.
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
  quickstart,
  steps,
}) => (
  <EmptyState
    title={title}
    description={description}
    icon={
      icon && !hideIcon ? (
        <SvgColor src={icon} sx={{ width: 20, height: 20 }} />
      ) : undefined
    }
    action={action}
    docsHref={linkText ? link : undefined}
    docsLabel={linkText}
    onDocsClick={onLinkClick}
    quickstart={quickstart}
    steps={steps}
    sx={sx}
  />
);

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
  quickstart: PropTypes.object,
  steps: PropTypes.array,
};

export default EmptyLayout;
