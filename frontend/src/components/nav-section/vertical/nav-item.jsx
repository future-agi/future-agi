import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";

import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Tooltip from "@mui/material/Tooltip";
import { alpha, styled } from "@mui/material/styles";
import ListItemButton from "@mui/material/ListItemButton";
import CustomTooltip from "src/components/tooltip";

import { RouterLink } from "src/routes/components";

import Iconify from "../../iconify";

// ----------------------------------------------------------------------

// eslint-disable-next-line react/display-name
const NavItem = forwardRef(
  (
    {
      title,
      path,
      icon,
      info,
      disabled,
      disabledTooltip,
      caption,
      roles,
      //
      open,
      depth,
      active,
      hasChild,
      externalLink,
      currentRole = "admin",
      titleIcon,
      eventTrigger,
      ...other
    },
    ref,
  ) => {
    const subItem = depth !== 1;

    const renderContent = (
      <StyledNavItem
        ref={ref}
        disableGutters
        open={open}
        depth={depth}
        active={active}
        disabled={disabled}
        disableRipple
        {...other}
        onClick={() => {
          if (eventTrigger) {
            eventTrigger();
          }
        }}
        sx={(theme) => ({
          paddingLeft: 0,
          border: "solid",
          borderWidth: active ? "1px" : "0px",
          borderColor: active
            ? alpha(theme.palette.primary.main, 0.1)
            : undefined,
          width: "100%",
          px: 1,
          color: active
            ? theme.palette.primary.dark
            : theme.palette.text.primary,
          backgroundColor: active
            ? alpha(theme.palette.primary.main, 0.1)
            : "transparent",
          "&:hover": {
            backgroundColor: active
              ? alpha(theme.palette.primary.main, 0.1)
              : undefined,
          },
        })}
      >
        {!subItem && icon && (
          <Box component="span" className="icon">
            {React.cloneElement(icon, {
              sx: {
                size: 20,
                color: active ? "primary.main" : "text.primary",
              },
            })}
          </Box>
        )}

        {subItem && icon ? (
          <Box component="span" className="icon">
            {icon}
          </Box>
        ) : (
          <Box component="span" className="sub-icon" />
        )}

        {title && (
          <Box
            component="span"
            sx={{ flex: "1 1 auto", minWidth: 0, ml: 0.75 }}
          >
            <Box
              component="span"
              className="label"
              sx={{
                color: active ? "primary.dark" : "text.primary",
                display: "flex",
                gap: 1,
                alignItems: "center",
              }}
            >
              {title}
              {titleIcon}
            </Box>

            {caption && (
              <Tooltip title={caption} placement="top-start">
                <Box component="span" className="caption">
                  {caption}
                </Box>
              </Tooltip>
            )}
          </Box>
        )}

        {info && (
          <Box component="span" className="info">
            {info}
          </Box>
        )}

        {hasChild && (
          <Iconify
            width={16}
            className="arrow"
            icon={
              open
                ? "eva:arrow-ios-downward-fill"
                : "eva:arrow-ios-forward-fill"
            }
          />
        )}
      </StyledNavItem>
    );

    // Hidden item by role
    if (roles && !roles.includes(`${currentRole}`)) {
      return null;
    }

    if (hasChild) {
      return renderContent;
    }

    if (disabled) {
      return (
        <CustomTooltip title={disabledTooltip || title} show={!!disabledTooltip} placement="right" arrow>
          {renderContent}
        </CustomTooltip>
      );
    }

    if (externalLink)
      return (
        <Link
          href={path}
          target="_blank"
          rel="noopener"
          color="inherit"
          underline="none"
          sx={{
            height: "32px",
            minHeight: "32px",
            ...(disabled && {
              cursor: "default",
            }),
          }}
        >
          {renderContent}
        </Link>
      );

    return (
      <Link
        component={RouterLink}
        href={path}
        color="inherit"
        underline="none"
        sx={{
          minHeight: "32px",
          height: "32px",
          ...(disabled && {
            cursor: "default",
          }),
        }}
      >
        {renderContent}
      </Link>
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
  title: PropTypes.oneOfType([PropTypes.string, PropTypes.node]),
  disabled: PropTypes.bool,
  disabledTooltip: PropTypes.string,
  hasChild: PropTypes.bool,
  caption: PropTypes.string,
  externalLink: PropTypes.bool,
  currentRole: PropTypes.string,
  roles: PropTypes.arrayOf(PropTypes.string),
  titleIcon: PropTypes.any,
  eventTrigger: PropTypes.func,
};

export default NavItem;

// ----------------------------------------------------------------------

const StyledNavItem = styled(ListItemButton, {
  shouldForwardProp: (prop) => prop !== "active",
})(({ active, open, depth, theme }) => {
  const subItem = depth !== 1;

  const opened = open && !active;

  const deepSubItem = Number(depth) > 2;

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
      marginBottom: 0,
      borderRadius: 6,
      color: theme.palette.text.primary,
      padding: theme.spacing(0.25, 0.75),
      transition: theme.transitions.create(["background-color", "color"], {
        duration: theme.transitions.duration.shortest,
      }),
    },
    icon: {
      width: 18,
      height: 18,
      flexShrink: 0,
      color: active ? theme.palette.primary.main : theme.palette.text.primary,
    },
    label: {
      ...noWrapStyles,
      fontSize: "13px",
      fontWeight: 500,
      textTransform: "none",
    },
    caption: {
      ...noWrapStyles,
      ...theme.typography.caption,
      color: theme.palette.action.disabled,
    },
    info: {
      display: "inline-flex",
      marginLeft: theme.spacing(0.75),
    },
    arrow: {
      flexShrink: 0,
      marginLeft: theme.spacing(0.75),
      width: 14,
      height: 14,
    },
  };

  return {
    // Root item
    ...(!subItem && {
      ...baseStyles.item,
      minHeight: 28,
      "& .icon": {
        ...baseStyles.icon,
        "& > span": {
          height: "18px",
          width: "18px",
        },
        "& svg": {
          width: "18px",
          height: "18px",
        },
      },
      "& .sub-icon": {
        display: "none",
      },
      "& .label": {
        ...baseStyles.label,
        fontWeight: active ? 600 : 500,
        color: "inherit",
        "& *": {
          color: "inherit",
        },
      },
      "& .caption": {
        ...baseStyles.caption,
      },
      "& .info": {
        ...baseStyles.info,
      },
      "& .arrow": {
        ...baseStyles.arrow,
      },
      ...(active && {
        color: theme.palette.primary.dark,
        backgroundColor: alpha(theme.palette.primary.main, 0.12),
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
      minHeight: 28,
      "& .icon": {
        ...baseStyles.icon,

        "& > span": {
          height: "24px",
          width: "24px",
        },
      },
      "& .sub-icon": {
        ...baseStyles.icon,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        "&:before": {
          content: '""',
          width: 4,
          height: 4,
          borderRadius: "50%",
          backgroundColor: theme.palette.action.disabled,
          transition: theme.transitions.create(["transform"], {
            duration: theme.transitions.duration.shorter,
          }),
          ...(active && {
            transform: "scale(2)",
            backgroundColor: theme.palette.primary.main,
          }),
        },
      },
      "& .label": {
        ...baseStyles.label,
      },
      "& .caption": {
        ...baseStyles.caption,
      },
      "& .info": {
        ...baseStyles.info,
      },
      "& .arrow": {
        ...baseStyles.arrow,
      },
      ...(active && {
        color: theme.palette.primary.main,
      }),
    }),

    // Deep sub item
    ...(deepSubItem && {
      paddingLeft: `${theme.spacing(Number(depth))} !important`,
    }),
  };
});
