import PropTypes from "prop-types";
import { Box, Stack, Typography, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { WORKSPACE_COPY } from "./workspace.constants";
import {
  currentEnvVersion,
  currentAgentVersion,
} from "src/api/simulate-environments/_fixtures/versions";

// What is being tested, against what. A run is a pairing — this environment
// version against that agent version — and a result nobody can attribute to a
// specific pair is not reproducible, so the pairing is stated here.
//
// Stated, not selected: two dropdowns under the header would read as filters
// that scope the page and do not. The choice belongs at the moment before a
// run, so this is a read-only strip; the version-creation control is deferred.
export default function VersionBar({ env, envState }) {
  const envV = currentEnvVersion(env, envState);
  const agentV = currentAgentVersion(envState);
  const { versionBar } = WORKSPACE_COPY;

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 2,
        py: 0.875,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        flexWrap: "wrap",
        rowGap: 1,
      }}
    >
      {/* The environment is the primary identity; the agent version is the
          current test subject running against it. The env is stated first and
          bold, the agent prefixed "Test subject" and rendered lighter. */}
      <Tooltip arrow title={versionBar.pairingTooltip(envV.label, envV.note, agentV.label)}>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ cursor: "default" }}>
          <Iconify icon="solar:box-linear" width={14} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {versionBar.envPrefix}{" "}
            <Box component="span" sx={{ color: "text.primary", fontWeight: "fontWeightBold" }}>
              {envV.label}
            </Box>
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.disabled", mx: 0.25 }}>·</Typography>
          <Iconify icon="solar:cpu-bolt-linear" width={14} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: "fontWeightSemiBold" }}>
            {versionBar.testSubject}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {versionBar.agentPrefix}{" "}
            <Box component="span" sx={{ color: "text.primary", fontWeight: "fontWeightBold" }}>
              {agentV.label}
            </Box>
          </Typography>
        </Stack>
      </Tooltip>

      <Typography sx={{ typography: "s3", color: "text.disabled", mx: 0.5 }}>·</Typography>

      <Tooltip arrow title={versionBar.scenariosTooltip}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", cursor: "default" }}>
          {versionBar.scenariosShared(envV.scenarios)}
        </Typography>
      </Tooltip>
    </Stack>
  );
}

VersionBar.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string }),
  envState: PropTypes.shape({
    envVersions: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string })),
    agentVersions: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string })),
    activeEnvVersion: PropTypes.string,
    activeAgentVersion: PropTypes.string,
    scenarios: PropTypes.arrayOf(PropTypes.any),
  }),
};
