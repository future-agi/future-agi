import PropTypes from "prop-types";
import { Box, Stack, Typography, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  isVersioningDisabled,
  useEnvironmentVersions,
  useRefreshWhenChecked,
} from "src/api/simulate-environments/versions";
import { VERSIONS_COPY, WORKSPACE_COPY } from "./workspace.constants";

const Strong = ({ children }) => (
  <Box
    component="span"
    sx={{ color: "text.primary", fontWeight: "fontWeightSemiBold" }}
  >
    {children}
  </Box>
);
Strong.propTypes = { children: PropTypes.node };

const Dot = () => (
  <Typography sx={{ typography: "s3", color: "text.disabled" }}>·</Typography>
);

// What is being tested, against what. A run is a pairing — this environment
// version against that agent version — and a result nobody can attribute to a
// specific pair is not reproducible, so the pairing is stated here.
//
// Stated, not selected: two dropdowns under the header would read as filters
// that scope the page and do not. The labels are the environment's defaults;
// with versioning off there is no pairing to state, so only the scenario count
// shows, after the agent under test when one is connected. Until runs are pinned
// to versions, the agent shown is the environment's default agent version, not
// a claim about which one a run used.
export default function VersionBar({ env, envState }) {
  const subject = env?.testSubject;
  const enabled = Boolean(env?.versioning?.enabled);
  const { data, error } = useEnvironmentVersions(env?.id, {
    enabled,
  });
  // Always mounted, so a check that lands with the drawer closed still
  // refreshes what follows the default.
  useRefreshWhenChecked(env?.id, data?.agent);
  const { versionBar } = WORKSPACE_COPY;

  // Only state a pairing once the server has answered. A failed refresh keeps
  // what it last answered; before any answer only the scenario count shows.
  const showPairing = enabled && Boolean(data) && !isVersioningDisabled(error);
  const envV = data?.defaults?.environment;
  const agentV = data?.defaults?.agent;
  const suiteV = data?.defaults?.suite;
  const versioned = Boolean(envV || agentV || suiteV);
  const named = (version) => version.label || VERSIONS_COPY.unlabelled;
  const envLabel = envV ? named(envV) : versionBar.beforeVersioning;
  const agentLabel = agentV ? named(agentV) : versionBar.noAgent;
  const fromSuite = showPairing && suiteV?.scenarioCount != null;
  const scenarioCount = fromSuite
    ? suiteV.scenarioCount
    : env?.counts?.scenarios ?? envState?.scenarios?.length ?? 0;

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
      {showPairing && (
        <>
          {/* One line, agent first: the agent version under test against the
              environment and scenario versions it runs on. */}
          <Tooltip
            arrow
            title={
              versioned
                ? versionBar.pairingTooltip(
                    envV ? envLabel : null,
                    envV?.summary,
                    agentV ? agentLabel : null,
                  )
                : versionBar.noVersionsTooltip
            }
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ cursor: "default", minWidth: 0 }}
            >
              <Iconify
                icon="solar:box-linear"
                width={14}
                sx={{ color: "text.subtitle" }}
              />
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {versionBar.agentPrefix} <Strong>{agentLabel}</Strong>
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.disabled" }}>
                ×
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {versionBar.envPrefix} <Strong>{envLabel}</Strong>
              </Typography>
            </Stack>
          </Tooltip>
          {suiteV && (
            <>
              <Dot />
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {versionBar.suitePrefix} <Strong>{named(suiteV)}</Strong>
              </Typography>
            </>
          )}
          <Dot />
        </>
      )}

      {!showPairing && subject && (
        <>
          <Tooltip arrow title={versionBar.subjectTooltip}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ cursor: "default" }}
            >
              <Iconify
                icon="solar:cpu-bolt-linear"
                width={14}
                sx={{ color: "text.subtitle" }}
              />
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  fontWeight: "fontWeightSemiBold",
                }}
              >
                {versionBar.testSubject}
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.primary",
                  fontWeight: "fontWeightSemiBold",
                }}
              >
                {subject.name || versionBar.unnamedAgent}
              </Typography>
              {subject.active_version && (
                <Box
                  component="span"
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    fontWeight: "fontWeightSemiBold",
                  }}
                >
                  {subject.active_version}
                </Box>
              )}
            </Stack>
          </Tooltip>
          <Typography
            sx={{ typography: "s3", color: "text.disabled", mx: 0.5 }}
          >
            ·
          </Typography>
        </>
      )}

      <Tooltip
        arrow
        title={
          fromSuite
            ? versionBar.defaultSuiteTooltip(named(suiteV))
            : versionBar.scenariosTooltip
        }
      >
        <Typography
          sx={{ typography: "s3", color: "text.subtitle", cursor: "default" }}
        >
          {versionBar.scenariosShared(scenarioCount)}
        </Typography>
      </Tooltip>
    </Stack>
  );
}

VersionBar.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
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
