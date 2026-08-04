import { Box, Stack } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";
import MUIWithSwiper from "../Slider";

const RightSectionAuth = () => {
  return (
    <Box sx={{ width: "100%", height: "100%", padding: 4, overflowY: "auto" }}>
      <Stack direction={"row"} gap={0.75} alignItems={"center"}>
        <Box
          component={"img"}
          sx={{ height: "44px", width: "44px" }}
          src="/favicon/logo.svg"
        />

        <SvgColor
          src="/logo/future_agi_text.svg"
          sx={{ height: "20px", width: "128px", color: "text.primary" }}
        />
      </Stack>

      <MUIWithSwiper />
    </Box>
  );
};

export default RightSectionAuth;
