import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import {
  RocketMascot,
  LOADING_MESSAGES,
  pickRandom,
} from "../../components/rocket-mascot";

export default function LoadingTemplate({ sx }) {
  const message = useMemo(() => pickRandom(LOADING_MESSAGES), []);

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
          Just a moment
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          fontWeight="fontWeightRegular"
          fontFamily="IBM Plex Sans"
          fontStyle="italic"
          textAlign="center"
          maxWidth={360}
        >
          {message}
        </Typography>
      </Stack>
    </Box>
  );
}

LoadingTemplate.propTypes = {
  sx: PropTypes.object,
};
