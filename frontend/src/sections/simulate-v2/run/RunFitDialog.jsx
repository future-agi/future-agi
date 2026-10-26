import PropTypes from "prop-types";
import { Box, Button, Dialog, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { latestEnvVersion, toolFit } from "../_mock/toolFit";
import { ToolList } from "../workspace/toolFitParts";
import { useRebuildForAgent } from "../workspace/useRebuildForAgent";
import { nextSnapshotLabel } from "../_mock/envSnapshot";
import { envLabelOf } from "../_mock/envVersionKinds";
import { addAndSaveMcpToolsPatch, mcpRunGapOf } from "../_mock/worldTools";
import { gatewayServerForHost, gatewayServerTools } from "../_mock/gatewayMcp";
import { pendingEnvChanges, saveEnvChangesPatch } from "../_mock/versions";

/**
 * Asked before a run whose agent calls a tool the world can't answer.
 *
 * It warns rather than blocks: calls to that tool get no answer, so those
 * scenarios come back not measured — an environment gap is never scored as
 * the agent's failure — and sometimes running the rest is exactly what
 * someone wants. So there are three ways out — fix the environment and run,
 * run anyway, or cancel — and the recommended one is the loud button.
 */
export default function RunFitDialog({
  open,
  onClose,
  env,
  envState,
  patch,
  agent,
  onRun,
  locked = false,
}) {
  const rebuild = useRebuildForAgent(env, envState, patch);
  if (!open || !agent) return null;

  const fit = toolFit(env, envState, { agent });
  /* The agent's own MCP tools the version a run uses doesn't answer. */
  const gap = mcpRunGapOf(env, envState);
  const gapOnly = fit.fits && gap.missing.length > 0;
  const gapNames = gap.missing.map((t) => t.name);
  const gapServers = gap.groups.map((g) => g.server.label).join(" and ");
  const allUnsaved =
    gap.missing.length > 0 && gap.unsaved.length === gap.missing.length;
  const waiting = pendingEnvChanges(envState).length;
  const latest = latestEnvVersion(env, envState);
  const latestFits =
    latest &&
    latest.label !== fit.envVersion &&
    toolFit(env, envState, { agent, envVersion: latest.label }).fits;
  const n = fit.missing.length;
  /* Environment versions as people read them (snapshots of world, scenarios, evals). */
  const nextLabel = nextSnapshotLabel(env, envState);
  const latestShown = latest ? envLabelOf(env, envState, latest.label) : null;
  /* The version this run is on, as the page names it — never the world label. */
  const fitShown = envLabelOf(env, envState, fit.envVersion);

  const fixAndRun = () => {
    if (latestFits) patch({ activeEnvVersion: latest.label });
    else rebuild(agent);
    onClose();
    onRun();
  };

  const runAnyway = () => {
    onClose();
    onRun();
  };

  /* Add the agent's MCP tools — through the Gateway where it has the server —
     save them into the version, then run. Edits already waiting are saved with them. */
  const addGapAndRun = () => {
    let es = envState;
    if (allUnsaved) {
      es = { ...es, ...saveEnvChangesPatch(env, es) };
    } else {
      gap.groups.forEach(({ server, tools }) => {
        const gw = gatewayServerForHost(server.host);
        const viaGw = gw ? gatewayServerTools(gw.gateway.serverId) : null;
        const names = new Set(tools.map((t) => t.name));
        const src = viaGw?.ok
          ? {
              server: viaGw.server,
              picked: viaGw.tools.filter((t) => names.has(t.name)),
            }
          : { server, picked: tools };
        es = { ...es, ...addAndSaveMcpToolsPatch(env, es, src).statePatch };
      });
    }
    patch({
      toolEnv: es.toolEnv,
      envVersions: es.envVersions,
      activeEnvVersion: es.activeEnvVersion,
      unsavedEnvChanges: es.unsavedEnvChanges || [],
    });
    onClose();
    /* The run pins the version it finds when it opens — start it once the saved one is in. */
    setTimeout(onRun, 0);
  };

  if (gapOnly) {
    const n = gap.missing.length;
    return (
      <Dialog
        open={open}
        onClose={onClose}
        maxWidth={false}
        PaperProps={{
          sx: {
            width: 560,
            maxWidth: "calc(100% - 32px)",
            p: 3,
            borderRadius: 2,
            backgroundImage: "none",
          },
        }}
      >
        <Box
          sx={{
            width: 36,
            height: 36,
            borderRadius: 1,
            display: "grid",
            placeItems: "center",
            mb: 1.75,
            bgcolor: "action.hover",
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:plug-circle-linear" width={20} />
        </Box>
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          {`${envState?.agent?.name || `Agent ${agent}`} gets ${n === 1 ? "a tool" : `${n} tools`} from ${gapServers} environment ${fitShown} doesn't answer`}
        </Typography>
        <Typography
          component="div"
          sx={{ typography: "s2", color: "text.secondary", mt: 1 }}
        >
          Its MCP server lists <ToolList tools={gap.missing} />. Scenarios where
          it calls {n === 1 ? "it" : "them"} get no answer back, so they come
          back as <b>not measured</b> — the rest of the run still tests it.
        </Typography>
        <Box sx={{ mt: 2, p: 1.5, borderRadius: 1, bgcolor: "action.hover" }}>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {locked
              ? "This is a template preview, so its world can't change here — fork it to add them."
              : allUnsaved
                ? `${n === 1 ? "It's" : "They're"} added on the World tab but not saved yet. Saving makes environment ${nextLabel}${waiting > gap.unsaved.length ? ", with your other unsaved edits" : ""}.`
                : `Adding ${n === 1 ? "it" : "them"} makes environment ${nextLabel}${waiting ? `, saved with your ${waiting} unsaved edit${waiting === 1 ? "" : "s"}` : ""}. Each starts Simulated; no scenario's proof changes.`}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 2.5 }}>
          <Button
            size="small"
            onClick={onClose}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Box flex={1} />
          <Button
            size="small"
            variant="outlined"
            onClick={runAnyway}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              "&:hover": {
                borderColor: "text.disabled",
                bgcolor: "action.hover",
              },
            }}
          >
            Run on environment {fitShown} anyway
          </Button>
          {!locked && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={addGapAndRun}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
            >
              {allUnsaved
                ? "Save and run"
                : `Add ${n === 1 ? "it" : "them"} and run`}
            </Button>
          )}
        </Stack>
      </Dialog>
    );
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{
        sx: {
          width: 560,
          maxWidth: "calc(100% - 32px)",
          p: 3,
          borderRadius: 2,
          backgroundImage: "none",
        },
      }}
    >
      <Box
        sx={{
          width: 36,
          height: 36,
          borderRadius: 1,
          display: "grid",
          placeItems: "center",
          mb: 1.75,
          bgcolor: "action.hover",
          color: "text.primary",
        }}
      >
        <Iconify icon="solar:plug-circle-linear" width={20} />
      </Box>
      <Typography sx={{ typography: "m3", fontWeight: 600 }}>
        Agent {agent} calls {n === 1 ? "a tool" : `${n} tools`} environment{" "}
        {fitShown} can&apos;t answer
      </Typography>
      <Typography
        component="div"
        sx={{ typography: "s2", color: "text.secondary", mt: 1 }}
      >
        It calls <ToolList tools={fit.missing} />, which this world doesn&apos;t
        know how to answer. Scenarios where it calls {n === 1 ? "it" : "them"}{" "}
        come back <b>not measured</b>.
        {gap.missing.length > 0 &&
          ` Add ${gapNames.join(", ")} on the World tab.`}
      </Typography>

      <Box sx={{ mt: 2, p: 1.5, borderRadius: 1, bgcolor: "action.hover" }}>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {locked
            ? "This is a template preview, so its world can't be rebuilt or switched here — fork it to fix the environment."
            : latestFits
              ? `Environment ${latestShown} already answers ${n === 1 ? "this tool" : "these tools"} — switch to it for a clean run.`
              : `Rebuilding makes environment ${nextLabel}: the world learns ${n === 1 ? "the tool" : "the tools"}, and new scenarios are added to test ${n === 1 ? "it" : "them"}. Older environment versions stay as they are.`}
        </Typography>
      </Box>

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 2.5 }}>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Box flex={1} />
        <Button
          size="small"
          variant="outlined"
          onClick={runAnyway}
          sx={{
            color: "text.primary",
            borderColor: "divider",
            "&:hover": {
              borderColor: "text.disabled",
              bgcolor: "action.hover",
            },
          }}
        >
          Run on environment {fitShown} anyway
        </Button>
        {!locked && (
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={fixAndRun}
            startIcon={
              <Iconify
                icon={
                  latestFits
                    ? "solar:transfer-horizontal-linear"
                    : "solar:refresh-linear"
                }
                width={16}
              />
            }
          >
            {latestFits
              ? `Use environment ${latestShown} and run`
              : "Rebuild and run"}
          </Button>
        )}
      </Stack>
    </Dialog>
  );
}

RunFitDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func.isRequired,
  agent: PropTypes.string,
  onRun: PropTypes.func.isRequired,
  locked: PropTypes.bool,
};
