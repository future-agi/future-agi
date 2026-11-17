import { Box, Stack, Typography, keyframes } from "@mui/material";
import SvgColor from "src/components/svg-color";

// Rotation animation for the loading icon
const spin = keyframes`
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
`;

export default function TemplateLoadingOverlay() {
  return (
    <Box
      sx={{
        height: "100%",
        width: "100%",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        bgcolor: "background.neutral",
      }}
    >
      <Stack direction={"row"} alignItems="center" gap={1}>
        <SvgColor
          src="/assets/icons/ic_fan_loading.svg"
          sx={{
            width: 20,
            height: 20,
            color: "text.primary",
            animation: `${spin} 1s linear infinite`,
          }}
        />
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          Loading template
        </Typography>
      </Stack>
    </Box>
  );
}
