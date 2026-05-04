import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";

import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Tooltip from "@mui/material/Tooltip";
import { alpha, styled } from "@mui/material/styles";
import ListItemButton from "@mui/material/ListItemButton";

import { RouterLink } from "src/routes/components";

import Iconify from "../../iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

// ----------------------------------------------------------------------

/**
 * @typedef {Object} NavItemProps
 * @property {string} title
 * @property {string} path
 * @property {React.ReactElement} icon
 * @property {React.ReactElement} info
 * @property {boolean} disabled
 * @property {string} caption
 * @property {string[]} roles
 * @property {boolean} open
 * @property {number} depth
 * @property {boolean} active
 * @property {boolean} hasChild
 * @property {boolean} externalLink
 * @property {string} currentRole
 * @property {function} eventTrigger
 */

/**
 * NavItem is a React component. Prop types are validated at runtime using PropTypes.
 * Linter errors about property types can be ignored in JS files.
 */
// eslint-disable-next-line react/display-name
const NavItem = forwardRef(
  (
    {
      title,
      path,
      icon,
      info,
      disabled,
      caption,
      roles,
      //
      open,
      depth,
      active,
      hasChild,
      externalLink,
      currentRole = "admin",
      eventTrigger,
      ...other
    },
    ref,
  ) => {
    const subItem = depth !== 1;

    const renderContent = (
      <StyledNavItem
        disableGutters
        ref={ref}
        disableRipple
        disabled={disabled}
        onClick={() => {
          if (eventTrigger) {
            eventTrigger();
          }
        }}
        {...other}
        sx={{
          backgroundColor: "transparent",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",

          color: active ? "primary.main" : "text.secondary",
        }}
      >
        {icon && (
          <Box className="icon">
            {React.cloneElement(icon, {
              sx: {
                height: "20px",
                width: "20px",
                color: active ? "primary.main" : "text.secondary",
              },
            })}
          </Box>
        )}

        {/* {title && (
          <Box component="span" className="label">
            {title}
          </Box>
        )} */}

        {caption && (
          <Tooltip title={caption} arrow placement="right">
            <Iconify width={16} icon="eva:info-outline" className="caption" />
          </Tooltip>
        )}

        {info && subItem && (
          <Box component="span" className="info">
            {info}
          </Box>
        )}

        {hasChild && (
          <Iconify
            width={16}
            className="arrow"
            icon="eva:arrow-ios-forward-fill"
          />
        )}
      </StyledNavItem>
    );

    // Hidden item by role
    if (roles && !roles.includes(`${currentRole}`)) {
      return null;
    }

    if (disabled) {
      return (
        <CustomTooltip title={title} show={true} placement="right" arrow>
          <Box sx={{ width: 1, minHeight: "30px", height: "30px" }}>
            {renderContent}
          </Box>
        </CustomTooltip>
      );
    }

    if (externalLink)
      return (
        <CustomTooltip title={title} show placement="right" arrow>
          <Link
            href={path}
            target="_blank"
            rel="noopener"
            color="inherit"
            underline="none"
            sx={{
              width: 1,
              minHeight: "30px",
              height: "30px",
              ...(disabled && {
                cursor: "default",
              }),
            }}
          >
            {renderContent}
          </Link>
        </CustomTooltip>
      );

    return (
      <CustomTooltip title={title} show={true} placement="right" arrow>
        <Link
          component={RouterLink}
          href={path}
          color="inherit"
          underline="none"
          sx={{
            width: 1,
            minHeight: "30px",
            height: "30px",
            ...(disabled && {
              cursor: "default",
            }),
          }}
        >
          {renderContent}
        </Link>
      </CustomTooltip>
    );
  },
);

NavItem.propTypes = {
  open: PropTypes.bool,
  active: PropTypes.bool,
  path: PropTypes.string,
  depth: PropTypes.number,
  icon: PropTypes.element,
  info: PropTypes.element,
  title: PropTypes.string,
  disabled: PropTypes.bool,
  hasChild: PropTypes.bool,
  caption: PropTypes.string,
  externalLink: PropTypes.bool,
  currentRole: PropTypes.string,
  roles: PropTypes.arrayOf(PropTypes.string),
  eventTrigger: PropTypes.func,
};

export default NavItem;

// ----------------------------------------------------------------------

const StyledNavItem = styled(ListItemButton, {
  shouldForwardProp: (prop) => prop !== "active",
})(({ active, open, depth, theme }) => {
  const subItem = depth !== 1;

  const opened = open && !active;

  const noWrapStyles = {
    width: "100%",
    maxWidth: "100%",
    display: "block",
    overflow: "hidden",
    whiteSpace: "nowrap",
    textOverflow: "ellipsis",
  };

  const baseStyles = {
    item: {
      borderRadius: 6,
      height: "32px",
      width: "32px",
      color: theme.palette.text.secondary,
    },
    icon: {
      flexShrink: 0,
      height: "16px",
      width: "16px",
    },
    label: {
      textTransform: "capitalize",
    },
    caption: {
      color: theme.palette.action.disabled,
    },
  };

  return {
    // Root item
    ...(!subItem && {
      ...baseStyles.item,
      fontSize: 10,
      lineHeight: "16px",
      textAlign: "center",
      padding: theme.spacing(1, 2),
      fontWeight: theme.typography.fontWeightBold,
      "& .icon": {
        ...baseStyles.icon,
      },
      "& .label": {
        ...noWrapStyles,
        ...baseStyles.label,
        marginTop: theme.spacing(0.5),
      },
      "& .caption": {
        ...baseStyles.caption,
        top: 11,
        left: 6,
        position: "absolute",
      },
      "& .arrow": {
        top: 11,
        right: 6,
        position: "absolute",
      },
      ...(active && {
        fontWeight: theme.typography.fontWeightBold,
        backgroundColor: alpha(theme.palette.primary.main, 0.12),
        color: theme.palette.primary.dark,
        "&:hover": {
          backgroundColor: alpha(theme.palette.primary.main, 0.2),
        },
      }),
      ...(opened && {
        color: theme.palette.text.primary,
        backgroundColor: theme.palette.action.hover,
      }),
    }),

    // Sub item
    ...(subItem && {
      ...baseStyles.item,
      ...theme.typography.body2,
      minHeight: 34,
      padding: theme.spacing(0, 1),
      fontWeight: theme.typography.fontWeightBold,
      "& .icon": {
        ...baseStyles.icon,
        marginRight: theme.spacing(0.5),
      },
      "& .label": {
        ...baseStyles.label,
        flexGrow: 1,
      },
      "& .caption": {
        ...baseStyles.caption,
        marginLeft: theme.spacing(0.75),
      },
      "& .info": {
        display: "inline-flex",
        marginLeft: theme.spacing(0.75),
      },
      "& .arrow": {
        marginLeft: theme.spacing(0.75),
        marginRight: theme.spacing(-0.5),
      },
      ...(active && {
        color: theme.palette.text.primary,
        backgroundColor: theme.palette.action.selected,
        fontWeight: theme.typography.fontWeightBold,
      }),
      ...(opened && {
        color: theme.palette.text.primary,
        backgroundColor: theme.palette.action.hover,
      }),
    }),
  };
});
