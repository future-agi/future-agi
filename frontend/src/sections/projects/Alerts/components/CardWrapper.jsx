import { Box, Divider, Stack, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";

export default function CardWrapper({
  order = 0,
  title = "",
  children,
  icon,
  iconColor,
  hideOrder = false,
  bgColor = "background.neutral",
  textColor = "text.disabled",
  titleSx = {},
  sx = {},
}) {
  const theme = useTheme();

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        ...sx,
      }}
    >
      <Stack
        sx={{
          padding: 0.5,
          ...(hideOrder && { px: 1.5 }),
        }}
        flexDirection={"row"}
        alignItems={"center"}
        gap={1}
      >
        <ShowComponent condition={!hideOrder}>
          <Box
            sx={{
              height: theme.spacing(3.5),
              width: theme.spacing(3.5),
              backgroundColor: bgColor,
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              padding: 0.5,
            }}
          >
            {icon ? (
              <SvgColor
                src={icon}
                sx={{
                  height: 16,
                  width: 16,
                  ...(iconColor && { bgcolor: iconColor }),
                }}
              />
            ) : (
              <Typography
                color={textColor}
                variant="s3"
                fontWeight={"fontWeightSemiBold"}
              >
                {order + 1}
              </Typography>
            )}
          </Box>
        </ShowComponent>
        <Typography
          variant="s3"
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
          sx={{
            ...titleSx,
          }}
        >
          {title}
        </Typography>
      </Stack>
      <Divider
        sx={{
          color: "common.white",
          borderColor: "divider",
        }}
      />
      {children}
    </Box>
  );
}

CardWrapper.displayName = "CardWrapper";

CardWrapper.propTypes = {
  order: PropTypes.number,
  title: PropTypes.string,
  children: PropTypes.node,
  icon: PropTypes.string,
  iconColor: PropTypes.string,
  hideOrder: PropTypes.bool,
  bgColor: PropTypes.string,
  textColor: PropTypes.string,
  titleSx: PropTypes.object,
  sx: PropTypes.object,
};
