import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { WORKSPACE_COPY } from "./workspace.constants";

// The green "Live" pill beside a built environment's name — the environment is
// adopted and answering, as opposed to still building.
export default function LivePill() {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 0.75,
        height: 22,
        borderRadius: 0.75,
        color: BUILD_TONES.green,
        bgcolor: (t) =>
          alpha(BUILD_TONES.green, t.palette.mode === "dark" ? 0.16 : 0.1),
        border: () => `1px solid ${alpha(BUILD_TONES.green, 0.24)}`,
      }}
    >
      <Box
        sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: BUILD_TONES.green }}
      />
      <Typography sx={{ typography: "s3", fontWeight: "fontWeightSemiBold" }}>
        {WORKSPACE_COPY.live}
      </Typography>
    </Stack>
  );
}
