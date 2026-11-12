import React from "react";
import PropTypes from "prop-types";
import { forwardRef } from "react";

import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Tooltip from "@mui/material/Tooltip";
import { styled } from "@mui/material/styles";
import ListItemButton from "@mui/material/ListItemButton";
import CustomTooltip from "src/components/tooltip";

import { RouterLink } from "src/routes/components";

import Iconify from "../../iconify";

// ----------------------------------------------------------------------

// One visible focus ring per nav item: 2 px in the primary colour on the
// link (keyboard focus only), drawn inside so the scroll area never clips it.
const NAV_LINK_FOCUS = {
  outline: "none",
  "&:focus-visible": {
    outline: (theme) => `2px solid ${theme.palette.primary.main}`,
    outlineOffset: "-2px",
  },
};

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
      titleTooltip,
      eventTrigger,
      onClick,
      "aria-current": ariaCurrent,
      ...other
    },
    ref,
  ) => {
    const subItem = depth !== 1;
    // A leaf item is wrapped in a link: the link is its one tab stop, so the
    // inner row is not focusable and has no role of its own (a11y #4).
    const inLink = !hasChild && !disabled;

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
        {...(inLink
          ? { tabIndex: -1, role: "presentation" }
          : hasChild
            ? { "aria-expanded": Boolean(open) }
            : {})}
        onClick={(event) => {
          // NavList owns child expansion; preserve its handler for Enter too.
          onClick?.(event);
          if (eventTrigger) {
            eventTrigger();
          }
        }}
        sx={(theme) => ({
          width: "100%",
          px: 1,
          // Labels read in the main text colour, like a dense app sidebar;
          // the active row is told apart by its fill and weight.
          color: theme.palette.text.primary,
          backgroundColor: active
            ? theme.palette.action.selected
            : "transparent",
          "&:hover": {
            color: theme.palette.text.primary,
            backgroundColor: active
              ? theme.palette.action.selected
              : theme.palette.action.hover,
          },
        })}
      >
        {!subItem && icon && (
          <Box component="span" className="icon">
            {React.cloneElement(icon, {
              sx: {
                size: 14,
                color: active ? "text.primary" : "action.active",
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
          <Box component="span" sx={{ flex: "1 1 auto", minWidth: 0, ml: 1 }}>
            <Box
              component="span"
              className="label"
              sx={{
                color: "inherit",
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
                ? "solar:alt-arrow-down-linear"
                : "solar:alt-arrow-right-linear"
            }
          />
        )}
      </StyledNavItem>
    );

    // A title cut off by the sidebar's width shows whole beside the row, on
    // hover and on keyboard focus. The control keeps its own text as its
    // name; the tooltip describes it.
    const withTitleTooltip = (control) => (
      <CustomTooltip
        show={!!titleTooltip}
        title={titleTooltip}
        placement="right"
        describeChild
        arrow
      >
        {control}
      </CustomTooltip>
    );

    // Hidden item by role
    if (roles && !roles.includes(`${currentRole}`)) {
      return null;
    }

    if (hasChild) {
      return withTitleTooltip(renderContent);
    }

    if (disabled) {
      return (
        <CustomTooltip
          title={disabledTooltip || title}
          show={!!disabledTooltip}
          placement="right"
          arrow
        >
          {renderContent}
        </CustomTooltip>
      );
    }

    if (externalLink)
      return withTitleTooltip(
        <Link
          href={path}
          target="_blank"
          rel="noopener"
          color="inherit"
          underline="none"
          sx={{
            height: "24px",
            minHeight: "24px",
            display: "block",
            borderRadius: "6px",
            ...NAV_LINK_FOCUS,
            ...(disabled && {
              cursor: "default",
            }),
          }}
        >
          {renderContent}
        </Link>,
      );

    return withTitleTooltip(
      <Link
        component={RouterLink}
        href={path}
        aria-current={ariaCurrent || (active ? "page" : undefined)}
        color="inherit"
        underline="none"
        sx={{
          minHeight: "24px",
          height: "24px",
          display: "block",
          borderRadius: "6px",
          ...NAV_LINK_FOCUS,
          ...(disabled && {
            cursor: "default",
          }),
        }}
      >
        {renderContent}
      </Link>,
    );
  },
);

NavItem.propTypes = {
  "aria-current": PropTypes.string,
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
  // The whole title, given only while the row cuts it off.
  titleTooltip: PropTypes.string,
  eventTrigger: PropTypes.func,
  onClick: PropTypes.func,
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
      padding: theme.spacing(0, 1),
      transition: theme.transitions.create(["background-color", "color"], {
        duration: theme.transitions.duration.shortest,
      }),
    },
    icon: {
      width: 14,
      height: 14,
      flexShrink: 0,
      color: active ? theme.palette.text.primary : theme.palette.action.active,
    },
    label: {
      ...noWrapStyles,
      fontSize: "12px",
      lineHeight: "20px",
      fontWeight: 400,
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
    // Keyboard focus on a toggle row (items with children) shows the same
    // 2 px ring as a link item.
    "&.Mui-focusVisible": {
      outline: `2px solid ${theme.palette.primary.main}`,
      outlineOffset: "-2px",
    },
    // Root item
    ...(!subItem && {
      ...baseStyles.item,
      minHeight: 24,
      "& .icon": {
        ...baseStyles.icon,
        "& > span": {
          height: "14px",
          width: "14px",
        },
        "& svg": {
          width: "14px",
          height: "14px",
        },
      },
      "& .sub-icon": {
        display: "none",
      },
      "& .label": {
        ...baseStyles.label,
        // Regular weight, medium only when selected: light text on a dark
        // canvas reads heavier than its size, so the nav stays at 400.
        fontWeight: active ? 500 : 400,
        color: "inherit",
        "& *": {
          color: "inherit",
          fontWeight: "inherit",
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
        color: theme.palette.text.primary,
        backgroundColor: theme.palette.action.selected,
        "&:hover": {
          backgroundColor: theme.palette.action.selected,
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
      minHeight: 24,
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
            backgroundColor: theme.palette.text.primary,
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
        color: theme.palette.text.primary,
      }),
    }),

    // Deep sub item
    ...(deepSubItem && {
      paddingLeft: `${theme.spacing(Number(depth))} !important`,
    }),
  };
});
