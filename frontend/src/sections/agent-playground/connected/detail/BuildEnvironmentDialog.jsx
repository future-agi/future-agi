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
import { MAX_SCENARIO_COUNT } from "src/pages/dashboard/harness/requestMapper";
import ParallelismField from "src/sections/simulate/environments/panels/ParallelismField";
import { clampParallelism } from "src/sections/simulate/environments/parallelism.constants";
import { MONO } from "./format";
import { buildFromAgentPayload, readyToBuild } from "./buildPayload";

const DEFAULT_SCENARIO_COUNT = 10;

const scenarioCountOf = (text) => {
  const count = Number(text);
  return Number.isInteger(count) && count >= 1 && count <= MAX_SCENARIO_COUNT
    ? count
    : null;
};

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
 * The environment binds to the agent and stays on the version it was built from.
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
  const [scenarioText, setScenarioText] = useState(
    String(DEFAULT_SCENARIO_COUNT),
  );
  const scenarioCount = scenarioCountOf(scenarioText);
  const [parallelismInput, setParallelismInput] = useState("1");
  const parallelism = clampParallelism(parallelismInput);
  const [building, setBuilding] = useState(false);
  const chosen = versionId || versions[0]?.id || "";
  // The chosen version may be past the first page of the list: its own read
  // stands in, so an old production version still builds.
  const detail = useAgentVersion(agent.id, chosen);
  const listed = versions.find((v) => v.id === chosen);
  const version =
    listed ||
    (detail.data
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
    : !scenarioCount
      ? `Scenarios must be between 1 and ${MAX_SCENARIO_COUNT}`
      : !version
        ? "Pick a version to build from"
        : preflight.isLoading
          ? "Checking the version"
          : preflight.isError
            ? errorMessage(preflight.error)
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
          parallelism,
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
            The environment runs the version you pick and stays on it until you
            change it or set it to follow production.
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
            fullWidth
            type="number"
            label="Scenarios"
            value={scenarioText}
            onChange={(e) => setScenarioText(e.target.value)}
            error={!scenarioCount}
            helperText={
              scenarioCount ? undefined : `Between 1 and ${MAX_SCENARIO_COUNT}`
            }
            inputProps={{
              min: 1,
              max: MAX_SCENARIO_COUNT,
              step: 1,
              "data-testid": "build-env-scenarios",
            }}
          />
          <ParallelismField
            value={parallelism}
            input={parallelismInput}
            onChange={setParallelismInput}
          />
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
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                sx={{ typography: "s2", color: "text.secondary" }}
                data-testid="build-env-reason"
              >
                {reason}
              </Typography>
              {preflight.isError && (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => preflight.refetch()}
                >
                  Retry
                </Button>
              )}
            </Stack>
          )}
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={
            building || !version || !name.trim() || !scenarioCount || blocked
          }
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
