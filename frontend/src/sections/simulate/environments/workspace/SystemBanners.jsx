import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../buildEnvironment/buildTones";
import { WORKSPACE_COPY } from "./workspace.constants";
import { environmentVersions } from "src/api/simulate-environments/_fixtures/versions";

// Two banners that change how everything below reads:
//   building   — the derivation is still in flight. Panels show loading; this
//                is the top-level signal that "still building" is why.
//   off-latest — the user pinned an older env version to work from. The amber
//                pin in the header is the control, this is the reminder so an
//                hour of edits does not silently run against the wrong world.
export default function SystemBanners({ env, envState, patch }) {
  const versions = environmentVersions(env, envState);
  const newest = versions[0];
  const active = versions.find((v) => v.current) || newest;
  const offLatest = active && newest && active.label !== newest.label;

  const buildStatus = env.buildStatus;
  const buildProgress = env.buildProgress;
  const building = buildStatus === "building";

  if (!building && !offLatest) return null;

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

      {offLatest && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            px: 3,
            py: 1.25,
            bgcolor: (t) => alpha(BUILD_TONES.amber, t.palette.mode === "dark" ? 0.12 : 0.07),
            borderBottom: "1px solid",
            borderColor: () => alpha(BUILD_TONES.amber, 0.3),
          }}
        >
          <Iconify
            icon="solar:danger-triangle-bold"
            width={14}
            sx={{ color: BUILD_TONES.amber, flexShrink: 0 }}
          />
          <Typography
            sx={{
              typography: "s2",
              fontWeight: "fontWeightBold",
              color: BUILD_TONES.amber,
              flexShrink: 0,
            }}
          >
            {WORKSPACE_COPY.offLatest.title(active.label)}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
            {WORKSPACE_COPY.offLatest.body(active.label, newest.label)}
          </Typography>
          <Button
            size="small"
            onClick={() => patch({ activeEnvVersion: newest.label })}
            sx={{ typography: "s2", fontWeight: "fontWeightBold", color: BUILD_TONES.amber, flexShrink: 0 }}
          >
            {WORKSPACE_COPY.offLatest.action(newest.label)}
          </Button>
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
  envState: PropTypes.shape({
    envVersions: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string })),
    activeEnvVersion: PropTypes.string,
    scenarios: PropTypes.arrayOf(PropTypes.any),
  }).isRequired,
  patch: PropTypes.func.isRequired,
};
