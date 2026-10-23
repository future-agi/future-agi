import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { currentAgentVersion } from "../_mock/versions";
import { latestEnvVersion, toolFit } from "../_mock/toolFit";
import { ToolList } from "./toolFitParts";
import { useRebuildForAgent } from "./useRebuildForAgent";
import { envLabelOf } from "../_mock/envVersionKinds";
import { versionCardOf } from "../_mock/versionCard";

/**
 * "Can the agent you are about to test actually be tested here?"
 *
 * Only one answer needs saying: it calls a tool this world cannot answer —
 * rebuild, or switch to the environment version that already learned it.
 * A newer agent that calls the same tools just plugs in; there is nothing to
 * rebuild, so nothing is shown.
 *
 * When the new version already raised these tools as suggestions in Needs
 * input, that is where they're answered — the environment changes only by an
 * edit or an approved suggestion, never by a second button beside it.
 */


export default function ToolFitBanner({ env, envState, patch, sx, locked = false, deferToCard = false }) {
  const rebuild = useRebuildForAgent(env, envState, patch);
  const agentV = currentAgentVersion(envState);
  if (!agentV) return null;

  const fit = toolFit(env, envState);
  const latest = latestEnvVersion(env, envState);
  const latestFits = latest && latest.label !== fit.envVersion
    && toolFit(env, envState, { envVersion: latest.label }).fits;
  if (fit.fits) return null;
  /* `deferToCard`: where the agent's change card shows (Overview), a card
     that already adds every missing tool is the one place to rebuild. */
  const card = deferToCard ? versionCardOf(envState) : null;
  const covered = new Set((card?.status === "open" ? card.capabilities : []).filter((c) => c.kind === "add").map((c) => c.capability));
  if (covered.size && fit.missing.every((t) => covered.has(t.name))) return null;

  const n = fit.missing.length;

  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      alignItems={{ sm: "center" }}
      spacing={1.5}
      sx={{
        p: 1.75, borderRadius: 1.5, border: "1px solid", borderColor: "text.disabled",
        bgcolor: "background.neutral",
        ...sx,
      }}
    >
      <Iconify
        icon="solar:plug-circle-linear"
        width={20}
        sx={{ color: "text.primary", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>
          {`Agent ${agentV.label} calls ${n === 1 ? "a tool" : `${n} tools`} environment ${envLabelOf(env, envState, fit.envVersion)} can't answer`}
        </Typography>
        <Typography component="div" sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {latestFits ? (
            <>
              <ToolList tools={fit.missing} /> {n === 1 ? "is" : "are"} new in {agentV.label}. Environment {envLabelOf(env, envState, latest.label)} already
              answers {n === 1 ? "it" : "them"} — switch to it for a clean run of {agentV.label}.
            </>
          ) : (
            <>
              <ToolList tools={fit.missing} /> {n === 1 ? "is" : "are"} new in {agentV.label}. Rebuild so the world learns{" "}
              {n === 1 ? "it" : "them"} — until then, scenarios where {agentV.label} calls {n === 1 ? "it" : "them"} here
              get no answer and come back not measured.
            </>
          )}
        </Typography>
      </Box>
      {/* A template preview's world is read-only — fork it to rebuild. */}
      {!locked && (
      <Button
        variant="contained"
        color="primary"
        size="small"
        onClick={() => (latestFits ? patch({ activeEnvVersion: latest.label }) : rebuild(agentV.label))}
        startIcon={<Iconify icon={latestFits ? "solar:transfer-horizontal-linear" : "solar:refresh-linear"} width={14} />}
        sx={{
          flexShrink: 0, typography: "s2", fontWeight: 700,
        }}
      >
        {latestFits ? `Use environment ${envLabelOf(env, envState, latest.label)}` : "Rebuild environment"}
      </Button>
      )}
    </Stack>
  );
}

ToolFitBanner.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  sx: PropTypes.object,
  locked: PropTypes.bool,
  deferToCard: PropTypes.bool,
};
