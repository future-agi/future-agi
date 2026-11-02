import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { BUILD_STATUS } from "../myEnvironments.constants";
import { WORKSPACE_COPY } from "./workspace.constants";

// The building banner: the derivation is still in flight. Panels show
// loading; this is the top-level signal that "still building" is why.
export default function SystemBanners({ env }) {
  const buildStatus = env.buildStatus;
  const buildProgress = env.buildProgress;
  const building = buildStatus === BUILD_STATUS.BUILDING;

  if (!building) return null;

  return (
    <Stack spacing={0}>
      {building && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            px: 3,
            py: 1.25,
            bgcolor: (t) => alpha(BUILD_TONES.accent, t.palette.mode === "dark" ? 0.12 : 0.06),
            borderBottom: "1px solid",
            borderColor: () => alpha(BUILD_TONES.accent, 0.24),
          }}
        >
          <Box
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: BUILD_TONES.accent,
              animation: "wb-pulse 1.4s ease-in-out infinite",
              "@keyframes wb-pulse": {
                "0%,100%": { opacity: 0.4 },
                "50%": { opacity: 1 },
              },
              flexShrink: 0,
            }}
          />
          <Typography
            sx={{
              typography: "s2",
              fontWeight: "fontWeightBold",
              color: BUILD_TONES.accent,
              flexShrink: 0,
            }}
          >
            {WORKSPACE_COPY.building.title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {buildProgress?.done != null && buildProgress?.total != null
              ? WORKSPACE_COPY.building.progress(buildProgress.done, buildProgress.total)
              : WORKSPACE_COPY.building.deriving}{" "}
            {WORKSPACE_COPY.building.tail}
          </Typography>
        </Stack>
      )}
    </Stack>
  );
}

SystemBanners.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    buildStatus: PropTypes.string,
    buildProgress: PropTypes.shape({ done: PropTypes.number, total: PropTypes.number }),
  }).isRequired,
};
