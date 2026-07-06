import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

export default function LoadingTemplate({ sx }) {
  return (
    <Box
      sx={{
        height: "100%",
        width: "100%",
        bgcolor: "background.paper",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        flexDirection: "column",
        gap: 2,
        ...sx,
      }}
    >
      <Box
        component={"img"}
        src={"/assets/login_signup/futureagi.png"}
        sx={{
          height: "32px",
          width: "32px",
          flexShrink: 0,
        }}
      />
      <Stack direction={"column"} gap={0} alignItems={"center"}>
        <Typography
          typography={"m3"}
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
        >
          Just a moment
        </Typography>
        <Typography
          typography={"s2"}
          color={"text.secondary"}
          fontWeight={"fontWeightMedium"}
        >
          We are loading your template...
        </Typography>
      </Stack>
      <LinearProgress
        sx={{
          width: 1,
          maxWidth: 360,
          "& .MuiLinearProgress-bar": {
            backgroundColor: "action.selected",
          },
          backgroundColor: "action.hover",
        }}
      />
    </Box>
  );
}

LoadingTemplate.propTypes = {
  sx: PropTypes.object,
};
