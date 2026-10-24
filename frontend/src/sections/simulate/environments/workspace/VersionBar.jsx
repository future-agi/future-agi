import PropTypes from "prop-types";
import { Box, Stack, Typography, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { WORKSPACE_COPY } from "./workspace.constants";

// What is being tested. A run pairs this environment with one agent version,
// and a result nobody can attribute to a specific agent version is not
// reproducible, so the agent under test is stated here.
//
// Stated, not selected: a dropdown under the header would read as a filter
// that scopes the page and does not. The environment has no version of its
// own in the API, so only the agent's is shown.
export default function VersionBar({ env, envState }) {
  const subject = env?.testSubject;
  const scenarioCount = env?.counts?.scenarios ?? envState?.scenarios?.length ?? 0;
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
      {subject && (
        <>
          <Tooltip arrow title={versionBar.subjectTooltip}>
            <Stack direction="row" alignItems="center" spacing={0.75} sx={{ cursor: "default" }}>
              <Iconify icon="solar:cpu-bolt-linear" width={14} sx={{ color: "text.subtitle" }} />
              <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: "fontWeightSemiBold" }}>
                {versionBar.testSubject}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.primary", fontWeight: "fontWeightBold" }}>
                {subject.name || versionBar.unnamedAgent}
              </Typography>
              {subject.active_version && (
                <Box
                  component="span"
                  sx={{ typography: "s2", color: "text.secondary", fontWeight: "fontWeightBold" }}
                >
                  {subject.active_version}
                </Box>
              )}
            </Stack>
          </Tooltip>

          <Typography sx={{ typography: "s3", color: "text.disabled", mx: 0.5 }}>·</Typography>
        </>
      )}

      <Tooltip arrow title={versionBar.scenariosTooltip}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", cursor: "default" }}>
          {versionBar.scenariosShared(scenarioCount)}
        </Typography>
      </Tooltip>
    </Stack>
  );
}

VersionBar.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    testSubject: PropTypes.shape({
      name: PropTypes.string,
      active_version: PropTypes.string,
    }),
    counts: PropTypes.shape({ scenarios: PropTypes.number }),
  }),
  envState: PropTypes.shape({
    scenarios: PropTypes.arrayOf(PropTypes.any),
  }),
};
