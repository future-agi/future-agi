import { Box, Typography } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";

const CompleteSetup = () => {
  return (
    <Box
      sx={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <Iconify
        sx={{ color: "status.success.fg" }}
        icon="nrk:media-completed"
        height={40}
        width={40}
      />
      <Typography fontSize={"24px"} lineHeight={"36px"} fontWeight={600}>
        Yay! You are all set up
      </Typography>
      <Typography
        fontSize={"16px"}
        lineHeight={"24px"}
        fontWeight={400}
        color="text.secondary"
      >
        Let’s start exploring our features
      </Typography>
    </Box>
  );
};

export default CompleteSetup;
