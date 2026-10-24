import { Button, Stack, Typography } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";

export default function Header() {
  return (
    <Stack
      direction={"row"}
      justifyContent={"space-between"}
      alignItems={"center"}
    >
      <Stack direction={"column"} gap={0}>
        <Typography
          typography={"m2"}
          fontWeight={"fontWeightMedium"}
          color={"text.primary"}
        >
          Agent Playground
        </Typography>
        <Typography typography={"s1"} color={"text.secondary"}>
          Break down complex tasks into sequential steps that build upon each
          other
        </Typography>
      </Stack>
      <Button
        variant="outlined"
        size="small"
        sx={{
          flexShrink: 0,
        }}
        onClick={() => {
          window.open(
            "https://docs.futureagi.com/docs/agent-playground",
            "_blank",
          );
        }}
      >
        <SvgColor
          src="/assets/icons/agent/docs.svg"
          sx={{ height: 16, width: 16, mr: 1 }}
        />
        <Typography typography="s2" fontWeight="fontWeightMedium">
          View Docs
        </Typography>
      </Button>
    </Stack>
  );
}
