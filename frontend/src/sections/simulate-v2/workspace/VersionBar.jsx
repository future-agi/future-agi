import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { currentEnvVersion, currentAgentVersion, nextAgentVersion } from "../_mock/versions";
import NewAgentVersion from "./NewAgentVersion";

/**
 * What is being tested, against what.
 *
 * A run is a pairing — this environment version × that agent version — and a
 * result nobody can attribute to a specific pair is not reproducible. So the
 * pairing is stated here.
 *
 * Stated, not selected. Two dropdowns pinned under the header read as filters:
 * pick agent v3 and you would expect the workspace to show v3's world. They
 * did nothing, on eleven steps where nine of them do not change meaning by
 * version anyway — and a control that looks like it scopes the page and does
 * not is worse than no control. The choice belongs where a choice is actually
 * made, which is the moment before a run, so the pre-flight quotes the pair it
 * is about to use. Everything a result is attributed to, it carries itself:
 * every run row, every run header, every comparison chip names its version.
 *
 * Comparing versions is not offered here either. It was a second, weaker route
 * to a screen that already exists — runs are picked and compared on the Runs
 * step, against real recorded runs rather than a summary of two — and two
 * doors to one room means the smaller one is always the disappointing one.
 */
export default function VersionBar({ env, envState, scenarioCount = 0, onAddVersion, onRunAfterVersion, patch }) {
  const envV = currentEnvVersion(env, envState);
  const agentV = currentAgentVersion(envState);
  const [adding, setAdding] = useState(false);

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ px: 2, py: 0.875, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0, flexWrap: "wrap", rowGap: 1 }}
    >
      <Tooltip
        arrow
        title={`Environment ${envV.label} — ${envV.note}. Agent ${agentV.label} is what the next run will use.`}
      >
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ cursor: "default" }}>
          <Iconify icon="solar:box-linear" width={14} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            env <Box component="span" sx={{ color: "text.primary", fontWeight: 700 }}>{envV.label}</Box>
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.disabled" }}>×</Typography>
          <Iconify icon="solar:cpu-bolt-linear" width={14} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            agent <Box component="span" sx={{ color: "text.primary", fontWeight: 700 }}>{agentV.label}</Box>
          </Typography>
        </Stack>
      </Tooltip>

      <Typography sx={{ typography: "s3", color: "text.disabled", mx: 0.5 }}>·</Typography>

      <Tooltip arrow title="Scenarios belong to the environment, so the same set runs against any agent version">
        <Typography sx={{ typography: "s3", color: "text.subtitle", cursor: "default" }}>
          {envV.scenarios} scenarios, shared across agent versions
        </Typography>
      </Tooltip>

      <Box flex={1} />

      {/* Stated where the versions are, because that is where someone decides
          they have changed the agent. */}
      <Tooltip
        arrow
        title={envState?.autoRun
          ? "Every new agent version starts a run of this suite automatically"
          : "Turn on to run this suite automatically whenever a version is added"}
      >
        <Stack
          direction="row" alignItems="center" spacing={0.625}
          onClick={() => patch?.({ autoRun: !envState?.autoRun })}
          sx={{
            px: 0.875, py: 0.375, borderRadius: 0.75, cursor: "pointer", flexShrink: 0,
            border: "1px solid",
            borderColor: envState?.autoRun ? alpha("#16A34A", 0.4) : "divider",
            bgcolor: (t) => (envState?.autoRun ? alpha("#16A34A", t.palette.mode === "dark" ? 0.12 : 0.06) : "transparent"),
          }}
        >
          <Iconify
            icon={envState?.autoRun ? "solar:play-circle-bold" : "solar:play-circle-linear"}
            width={14}
            sx={{ color: envState?.autoRun ? "#16A34A" : "text.subtitle" }}
          />
          <Typography sx={{ typography: "s3", fontWeight: 600, color: envState?.autoRun ? "#16A34A" : "text.subtitle" }}>
            Run on new version
          </Typography>
        </Stack>
      </Tooltip>

      <Button
        size="small"
        onClick={() => setAdding(true)}
        startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
        sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", flexShrink: 0 }}
      >
        Add version
      </Button>

      <NewAgentVersion
        env={env}
        envState={envState}
        scenarioCount={scenarioCount}
        open={adding}
        onClose={() => setAdding(false)}
        onCreate={(note) => {
          onAddVersion?.(nextAgentVersion(envState, { note }));
          /* The loop only closes if something closes it. A suite that has to be
             remembered is a suite that gets run the day before a release and
             never in between. */
          if (envState?.autoRun) onRunAfterVersion?.();
        }}
      />

    </Stack>
  );
}

VersionBar.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onAddVersion: PropTypes.func,
  onRunAfterVersion: PropTypes.func,
  patch: PropTypes.func,
  scenarioCount: PropTypes.number,
};

