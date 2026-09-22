import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { WORKSPACE_COPY } from "./workspace.constants";

// The status pill beside an environment's name. Two states keyed on the env's
// build status: green "Live" once the environment is adopted and answering, and
// an amber, pulsing "Building" while it is still being derived — you cannot
// correct a world that isn't done being built.
export default function LivePill({ env, building }) {
  const isBuilding = building ?? env?.buildStatus === "building";
  const tone = isBuilding ? BUILD_TONES.amber : BUILD_TONES.green;

  return (
    <CustomTooltip
      show
      title={isBuilding ? WORKSPACE_COPY.buildingTooltip : WORKSPACE_COPY.liveTooltip}
      size="small"
      arrow
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          px: 0.75,
          height: 22,
          borderRadius: 0.75,
          cursor: "default",
          color: tone,
          bgcolor: (t) => alpha(tone, t.palette.mode === "dark" ? 0.16 : 0.1),
          border: () => `1px solid ${alpha(tone, 0.24)}`,
        }}
      >
        <Box
          sx={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: tone,
            animation: isBuilding ? "env-pulse 1.4s ease-in-out infinite" : undefined,
            "@keyframes env-pulse": {
              "0%,100%": { opacity: 0.4 },
              "50%": { opacity: 1 },
            },
          }}
        />
        <Typography sx={{ typography: "s3", fontWeight: "fontWeightSemiBold" }}>
          {isBuilding ? WORKSPACE_COPY.buildingLabel : WORKSPACE_COPY.live}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}

LivePill.propTypes = {
  env: PropTypes.shape({ buildStatus: PropTypes.string }),
  building: PropTypes.bool,
};
