import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { latestEnvVersion, scenariosCalling, toolFit } from "../_mock/toolFit";
import { useRebuildForAgent } from "../workspace/useRebuildForAgent";
import { nextEnvLabel } from "../_mock/envSnapshot";
import { envLabelOf } from "../_mock/envVersionKinds";
import { addAndSaveMcpToolsPatch, mcpRunGapOf } from "../_mock/worldTools";
import { reviewCountsOf } from "../_mock/editReview";
import { gatewayServerForHost, gatewayServerTools } from "../_mock/gatewayMcp";
import { pendingEnvChanges, saveEnvChangesPatch } from "../_mock/versions";
import { openReviewChanges } from "../workspace/reviewChangesBus";

/**
 * The agent calls tools this environment can't answer — one row in the run
 * plan, beside the other readiness checks (was a dialog before the plan).
 *
 * It warns, never blocks: scenarios that call those tools come back not
 * measured, and Start run still runs the rest. The one action fixes the
 * environment in place — use a newer version that already answers them,
 * add the agent's MCP tools, or rebuild — and the plan re-reads at once.
 */
export default function ToolFitRow({ env, envState, patch, agent, locked }) {
  const [open, setOpen] = useState(false);
  const rebuild = useRebuildForAgent(env, envState, patch);
  if (!env || !agent) return null;

  const fit = toolFit(env, envState, { agent });
  const gap = mcpRunGapOf(env, envState);
  if (fit.fits && !gap.missing.length) return null;

  const gapOnly = fit.fits;
  const tools = gapOnly ? gap.missing : fit.missing;
  const n = tools.length;
  const absent = fit.missing.filter((t) => !t.changed);
  const changedOnly = !gapOnly && !absent.length;
  const fitShown = envLabelOf(env, envState, fit.envVersion);
  const nextLabel = nextEnvLabel(env, envState);
  const latest = latestEnvVersion(env, envState);
  const latestFits =
    !gapOnly &&
    latest &&
    latest.label !== fit.envVersion &&
    toolFit(env, envState, { agent, envVersion: latest.label }).fits;
  const latestShown = latest ? envLabelOf(env, envState, latest.label) : null;
  const undecided = reviewCountsOf(pendingEnvChanges(envState)).undecided;
  const allUnsaved =
    gap.missing.length > 0 && gap.unsaved.length === gap.missing.length;
  const blocked = changedOnly
    ? scenariosCalling(
        env,
        envState,
        fit.changed.map((t) => t.name),
      ).length
    : 0;

  const addGap = () => {
    let es = envState;
    if (allUnsaved) es = { ...es, ...saveEnvChangesPatch(env, es) };
    else
      gap.groups.forEach(({ server, tools: list }) => {
        const gw = gatewayServerForHost(server.host);
        const viaGw = gw ? gatewayServerTools(gw.gateway.serverId) : null;
        const names = new Set(list.map((t) => t.name));
        const src = viaGw?.ok
          ? {
              server: viaGw.server,
              picked: viaGw.tools.filter((t) => names.has(t.name)),
            }
          : { server, picked: list };
        es = { ...es, ...addAndSaveMcpToolsPatch(env, es, src).statePatch };
      });
    patch({
      toolEnv: es.toolEnv,
      envVersions: es.envVersions,
      activeEnvVersion: es.activeEnvVersion,
      unsavedEnvChanges: es.unsavedEnvChanges || [],
    });
  };

  /* The one fix, by what the environment needs. */
  const action = locked
    ? null
    : undecided > 0
      ? {
          label: "Review changes first",
          icon: "solar:checklist-minimalistic-linear",
          tip: "Unsaved edits are waiting for Accept or Reject — they'd go into the next version.",
          run: openReviewChanges,
        }
      : latestFits
        ? {
            label: `Use environment ${latestShown}`,
            icon: "solar:transfer-horizontal-linear",
            tip: `Environment ${latestShown} already answers ${n === 1 ? "this tool" : "these tools"}.`,
            run: () => patch({ activeEnvVersion: latest.label }),
          }
        : gapOnly
          ? {
              label: allUnsaved
                ? "Save them"
                : `Add ${n === 1 ? "it" : `${n} tools`}`,
              icon: "solar:add-circle-linear",
              tip: `Makes environment ${nextLabel}. Each starts Simulated; no scenario's proof changes.`,
              run: addGap,
            }
          : {
              label: "Rebuild environment",
              icon: "solar:refresh-linear",
              tip: `Makes environment ${nextLabel}: the world learns ${changedOnly ? "the new arguments" : n === 1 ? "the tool" : "the tools"}; scenarios for them are proposed in Needs input.`,
              run: () => rebuild(agent),
            };

  const headline = changedOnly
    ? `${n === 1 ? "1 tool" : `${n} tools`} changed since environment ${fitShown}`
    : `${n === 1 ? "1 tool" : `${n} tools`} environment ${fitShown} can't answer`;
  const consequence = changedOnly
    ? `${blocked} scenario${blocked === 1 ? "" : "s"} held back until it's rebuilt`
    : "scenarios that call them come back not measured";

  return (
    <Box
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: "8px" }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ px: 1.5, minHeight: 40 }}
      >
        <Iconify
          icon="solar:plug-circle-linear"
          width={16}
          sx={{ color: "status.warning.fg", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
          <Box component="span" sx={{ fontWeight: 500 }}>
            {headline}
          </Box>
          <Box component="span" sx={{ color: "text.tertiary" }}>
            {` · ${consequence} · `}
          </Box>
          <Box
            component="button"
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            sx={{
              all: "unset",
              cursor: "pointer",
              color: "text.secondary",
              fontWeight: 500,
              "&:hover": { textDecoration: "underline" },
            }}
          >
            {open ? "Hide" : `Show ${n}`}
          </Box>
        </Typography>
        {action && (
          <Tooltip arrow describeChild title={action.tip}>
            <Button
              size="small"
              variant="outlined"
              onClick={action.run}
              startIcon={<Iconify icon={action.icon} width={14} />}
              sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
            >
              {action.label}
            </Button>
          </Tooltip>
        )}
      </Stack>
      {open && (
        <Stack
          direction="row"
          flexWrap="wrap"
          gap={0.75}
          sx={{
            px: 1.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {tools.map((t) => (
            <Box
              key={t.name}
              component="code"
              sx={{
                px: 0.75,
                py: 0.25,
                borderRadius: 0.5,
                bgcolor: "background.neutral",
                typography: "s3",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              }}
            >
              {t.name}
            </Box>
          ))}
          {locked && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", width: "100%" }}
            >
              A template preview can&apos;t change its world — fork it to add
              them.
            </Typography>
          )}
        </Stack>
      )}
    </Box>
  );
}

ToolFitRow.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  agent: PropTypes.string,
  locked: PropTypes.bool,
};
