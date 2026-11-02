import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  isVersioningDisabled,
  useEnvironmentVersions,
  useRefreshWhenChecked,
} from "src/api/simulate-environments/versions";
import { VERSIONS_COPY, WORKSPACE_COPY } from "./workspace.constants";

// What is being tested, against what: Vel's "Agent v2 × environment v7 · N
// scenarios", with History. While versioning is on, the labels are the
// environment's default agent, environment and scenario versions, and History
// opens the Versions tab. Stated, not selected: a run picks its own versions
// when it starts. With versioning off there is no pairing to state, so only
// the agent under test and the scenario count show.
export default function VersionBar({ env, envState, onOpenVersions }) {
  const subject = env?.testSubject;
  const enabled = Boolean(env?.versioning?.enabled);
  const { data, error } = useEnvironmentVersions(env?.id, { enabled });
  // Always mounted, so a check that lands with the drawer closed still
  // refreshes what follows the default.
  useRefreshWhenChecked(env?.id, data?.agent);
  const { versionBar } = WORKSPACE_COPY;

  // Only state a pairing once the server has answered. A failed refresh keeps
  // what it last answered; before any answer only the agent and count show.
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

  const strong = (t) => (
    <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
      {t}
    </Box>
  );
  const sep = (sign) => (
    <Box
      component="span"
      sx={{ typography: "s3", color: "text.disabled", mx: 0.5 }}
    >
      {sign}
    </Box>
  );

  let tooltip;
  if (showPairing) {
    tooltip = versioned
      ? versionBar.pairingTooltip(
          envV ? envLabel : null,
          envV?.summary,
          agentV ? agentLabel : null,
        )
      : versionBar.noVersionsTooltip;
  } else {
    tooltip = (
      <>
        {subject && (
          <Box
            component="span"
            sx={{ display: "block", fontWeight: 600, mb: 0.5 }}
          >
            {`${subject.name || versionBar.unnamedAgent} ${subject.active_version || ""}, the test subject`}
          </Box>
        )}
        {versionBar.subjectTooltip}
      </>
    );
  }

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
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ minWidth: 0 }}
      >
        <Iconify
          icon="solar:box-linear"
          width={14}
          sx={{ color: "text.subtitle", mr: 0.25, flexShrink: 0 }}
        />
        <CustomTooltip show arrow size="small" title={tooltip}>
          <Typography
            noWrap
            sx={{
              typography: "s2",
              color: "text.secondary",
              cursor: "default",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {showPairing && (
              <>
                {`${versionBar.defaultAgent} agent `}
                {strong(agentLabel)}
                {sep("×")}
                {"environment "}
                {strong(envLabel)}
                {suiteV && (
                  <>
                    {sep("·")}
                    {`${versionBar.suitePrefix} `}
                    {strong(named(suiteV))}
                  </>
                )}
              </>
            )}
            {!showPairing &&
              (subject ? (
                <>
                  {"Agent "}
                  {strong(
                    subject.active_version || subject.name || "connected",
                  )}
                </>
              ) : (
                "No agent yet"
              ))}
          </Typography>
        </CustomTooltip>
        <CustomTooltip
          show
          arrow
          size="small"
          title={
            fromSuite
              ? versionBar.defaultSuiteTooltip(named(suiteV))
              : versionBar.scenariosTooltip
          }
        >
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.subtitle",
              cursor: "default",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {` · ${scenarioCount} scenario${scenarioCount === 1 ? "" : "s"}`}
          </Typography>
        </CustomTooltip>
      </Stack>
      <Box flex={1} />
      {showPairing && onOpenVersions && (
        <Button
          size="small"
          variant="outlined"
          onClick={onOpenVersions}
          startIcon={<Iconify icon="solar:history-linear" width={16} />}
          sx={{
            minWidth: 0,
            flexShrink: 0,
            color: "text.primary",
            borderColor: "divider",
          }}
        >
          History
        </Button>
      )}
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
  // Opens the Versions tab; History shows only with it, while versioning is on.
  onOpenVersions: PropTypes.func,
};
