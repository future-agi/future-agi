import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSnackbar } from "notistack";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import {
  toVersion,
  useAgentVersion,
  useBuildPreflight,
} from "src/api/agents/agentDetail";
import {
  createHarnessJob,
  harnessIdempotencyKey,
} from "src/api/harness/harness";
import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { MONO } from "./format";
import { buildFromAgentPayload, readyToBuild } from "./buildPayload";

const SCENARIO_COUNTS = [10, 25, 50, 100];

function Check({ check }) {
  const ok = check.status === "ok";
  return (
    <Stack direction="row" spacing={1} alignItems="flex-start">
      <Iconify
        icon={ok ? "solar:check-circle-linear" : "solar:danger-triangle-linear"}
        width={16}
        sx={{ color: ok ? "success.main" : "warning.main", mt: 0.25 }}
      />
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {check.message}
      </Typography>
    </Stack>
  );
}

Check.propTypes = { check: PropTypes.object.isRequired };

/**
 * Build an environment from this agent: pick a checked version (production by
 * default), name it, choose how many scenarios, read the preflight, build.
 * The environment binds to the agent and follows production.
 */
export default function BuildEnvironmentDialog({ agent, open, onClose }) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const list = useAgentDefinitionVersions({ selectedAgentId: agent.id });
  const versions = useMemo(
    () =>
      (list.data?.pages ?? [])
        .flatMap((p) => p.results ?? [])
        .map(toVersion)
        .filter(readyToBuild),
    [list.data],
  );
  const [versionId, setVersionId] = useState(agent.activeVersionId || "");
  const [name, setName] = useState(`${agent.name} environment`);
  const [scenarioCount, setScenarioCount] = useState(25);
  const [building, setBuilding] = useState(false);
  const chosen = versionId || versions[0]?.id || "";
  // The chosen version may be past the first page of the list: its own read
  // stands in, so an old production version still builds.
  const detail = useAgentVersion(agent.id, chosen);
  const listed = versions.find((v) => v.id === chosen);
  const version =
    listed ||
    (detail.data && readyToBuild({ check: null })
      ? {
          id: detail.data.id,
          label: detail.data.label,
          isActive: detail.data.isActive,
          source: detail.data.source,
        }
      : null);
  const options = listed || !version ? versions : [version, ...versions];
  const preflight = useBuildPreflight(agent.id, chosen, { enabled: !!chosen });
  const blocked = !preflight.data?.ok;
  const reason = !name.trim()
    ? "Name it first"
    : !version
      ? "Pick a version to build from"
      : preflight.isLoading
        ? "Checking the version"
        : preflight.isError
          ? "Couldn't run the preflight; try again"
          : preflight.data && !preflight.data.ok
            ? "Fix what the preflight found first"
            : "";

  const build = async () => {
    setBuilding(true);
    try {
      const dto = await createHarnessJob(
        buildFromAgentPayload({
          agent,
          version,
          name: name.trim(),
          scenarioCount,
        }),
        harnessIdempotencyKey(),
      );
      onClose();
      navigate(paths.dashboard.simulate.environments.detail(dto.job.job_id));
    } catch (e) {
      setBuilding(false);
      enqueueSnackbar(errorMessage(e), { variant: "error" });
    }
  };

  return (
    <ConfirmDialog
      open={open}
      onClose={() => (building ? null : onClose())}
      closeDisabled={building}
      title="Build environment"
      content={
        <Stack spacing={1.5}>
          <Typography sx={{ typography: "s2_1", color: "text.secondary" }}>
            The environment runs the code of this agent and follows its
            production version until you pin it.
          </Typography>
          <TextField
            fullWidth
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            inputProps={{ "data-testid": "build-env-name" }}
          />
          <TextField
            select
            fullWidth
            label="Built from"
            value={chosen}
            onChange={(e) => setVersionId(e.target.value)}
            inputProps={{ "data-testid": "build-env-version" }}
          >
            {options.map((v) => (
              <MenuItem key={v.id} value={v.id} sx={{ fontFamily: MONO }}>
                {v.label}
                {v.isActive ? " · in production" : ""}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            fullWidth
            label="Scenarios"
            value={scenarioCount}
            onChange={(e) => setScenarioCount(Number(e.target.value))}
          >
            {SCENARIO_COUNTS.map((n) => (
              <MenuItem key={n} value={n}>
                {n}
              </MenuItem>
            ))}
          </TextField>
          {preflight.isLoading && (
            <Typography
              sx={{ typography: "s2", color: "text.secondary" }}
              data-testid="build-env-preflight-loading"
            >
              Checking the version…
            </Typography>
          )}
          {preflight.data && (
            <Box data-testid="build-env-preflight">
              <Stack spacing={0.75}>
                {preflight.data.checks.map((check) => (
                  <Check key={check.name} check={check} />
                ))}
              </Stack>
            </Box>
          )}
          {reason && (
            <Typography
              sx={{ typography: "s2", color: "text.secondary" }}
              data-testid="build-env-reason"
            >
              {reason}
            </Typography>
          )}
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={building || !version || !name.trim() || blocked}
          onClick={build}
        >
          Build environment
        </Button>
      }
    />
  );
}

BuildEnvironmentDialog.propTypes = {
  agent: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
