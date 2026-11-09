import React from "react";
import { Box, Typography, Link, useTheme } from "@mui/material";
import SvgColor from "../svg-color";
import PropTypes from "prop-types";
import { ShowComponent } from "../show";

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
  titleComponent = "div",
}) => {
  const theme = useTheme();
  return (
    <Box
      display="flex"
      justifyContent="center"
      alignItems="center"
      sx={{
        height: "100%",
        ...sx,
      }}
    >
      <Box
        sx={{
          maxWidth: 447,
          width: "100%",
          textAlign: "center",
          borderRadius: 2,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: "14px",
        }}
      >
        <ShowComponent condition={!hideIcon}>
          <Box
            sx={{
              width: 68,
              height: 68,
              borderRadius: "8px",
              border: "2px solid",
              borderColor: "divider",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <SvgColor
              src={icon}
              sx={{
                width: "36px",
                height: "36px",
                background:
                  theme.palette.mode === "light"
                    ? "linear-gradient(135deg, var(--primary-main), #CF6BE8)"
                    : "linear-gradient(135deg, #FFFFFF, #E6E6E7)",
              }}
            />
          </Box>
        </ShowComponent>
        <Box>
          <Typography
            fontWeight={"fontWeightMedium"}
            typography="m3"
            color={"text.primary"}
            component={titleComponent}
          >
            {title}
          </Typography>

          <Typography
            fontWeight={"fontWeightRegular"}
            typography="s1"
            color="text.primary"
            component="span"
          >
            {description}{" "}
            <Link
              href={link}
              target="_blank"
              rel="noopener noreferrer"
              underline="always"
              color={"primary.main"}
              onClick={onLinkClick ? onLinkClick : () => {}}
            >
              {linkText}
            </Link>
          </Typography>
        </Box>
        {action}
      </Box>
    </Box>
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
  // The title's element: a div unless the page needs it as a heading (e.g.
  // "h2" beside sibling h2 sections). Typography is the same either way.
  titleComponent: PropTypes.elementType,
};

export default EmptyLayout;
