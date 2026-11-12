import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { RocketMascot } from "../../components/rocket-mascot";

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
        gap: 2.5,
        ...sx,
      }}
    >
      <RocketMascot variant="launching" size={160} />
      <Stack direction="column" gap={0.5} alignItems="center">
        <Typography
          typography="m3"
          color="text.primary"
          fontWeight="fontWeightMedium"
          fontFamily="IBM Plex Sans"
        >
          Loading{"\u2026"}
        </Typography>
      </Stack>
    </Box>
  );
}

LoadingTemplate.propTypes = {
  sx: PropTypes.object,
};
