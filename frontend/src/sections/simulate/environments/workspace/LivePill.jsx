import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import { BUILD_STATUS } from "../helpers/harnessJobToRow";
import { STATUS_META } from "../myEnvironments.constants";
import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { WORKSPACE_COPY } from "./workspace.constants";

// The status pill beside an environment's name. Three states keyed on the env's
// build status: green "Live" once the environment is adopted and answering,
// "Building" while it is still being derived, and "Failed" once its build stage
// is terminal-failed — a stopped build must not read as live.
//
// The two non-live colours come from STATUS_META, the same source the My
// Environments status pill reads, so the pill inside the workspace matches the
// chip outside it.
const TONE = {
  [BUILD_STATUS.BUILDING]: STATUS_META.building.color,
  [BUILD_STATUS.FAILED]: STATUS_META.failed.color,
};

const LABEL = {
  [BUILD_STATUS.BUILDING]: WORKSPACE_COPY.buildingLabel,
  [BUILD_STATUS.FAILED]: WORKSPACE_COPY.failedLabel,
};

export default function LivePill({ env }) {
  const tone = TONE[env?.buildStatus] || BUILD_TONES.green;
  const label = LABEL[env?.buildStatus] || WORKSPACE_COPY.live;

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 0.75,
        height: 22,
        borderRadius: 0.75,
        color: tone,
        bgcolor: (t) => alpha(tone, t.palette.mode === "dark" ? 0.16 : 0.1),
        border: () => `1px solid ${alpha(tone, 0.24)}`,
      }}
    >
      <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: tone }} />
      <Typography sx={{ typography: "s3", fontWeight: "fontWeightSemiBold" }}>
        {label}
      </Typography>
    </Stack>
  );
}

LivePill.propTypes = {
  env: PropTypes.shape({ buildStatus: PropTypes.string }),
};
