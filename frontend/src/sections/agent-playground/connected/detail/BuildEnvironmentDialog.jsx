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
import { toVersion, useBuildPreflight } from "src/api/agents/agentDetail";
import {
  createHarnessJob,
  harnessIdempotencyKey,
} from "src/api/harness/harness";
import { paths } from "src/routes/paths";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { MONO } from "./format";

const SCHEMA_VERSION = "futureagi.harness-job.v1";
const SCENARIO_COUNTS = [10, 25, 50, 100];

// A version the dialog offers: checked, or from before versioning.
const ready = (v) => !["pending", "failed", "discarded"].includes(v.check);

// The creation request for an environment bound to the agent: the platform
// takes the source and credential names from the version, so only the name,
// the scope and which version to build from travel.
export const buildFromAgentPayload = ({
  agent,
  version,
  name,
  scenarioCount,
}) => ({
  schema_version: SCHEMA_VERSION,
  scenario_count: scenarioCount,
  artifacts: { level: "standard" },
  source:
    version.source?.kind === "archive"
      ? {
          kind: "archive",
          archive_artifact_id: version.source.archive_artifact_id,
        }
      : {
          kind: "github",
          repository: version.source?.repository,
          ...(version.source?.ref ? { ref: version.source.ref } : {}),
          visibility: "public",
        },
  agent: { connector: "auto", config: {}, secret_refs: {} },
  metadata: {
    name,
    authoring_key: name,
    agent_definition_id: agent.id,
    agent_version_id: version.id,
    agent_policy: "follow",
  },
});

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
        .filter(ready),
    [list.data],
  );
  const [versionId, setVersionId] = useState(agent.activeVersionId || "");
  const [name, setName] = useState(`${agent.name} environment`);
  const [scenarioCount, setScenarioCount] = useState(25);
  const [building, setBuilding] = useState(false);
  const chosen = versionId || versions[0]?.id || "";
  const preflight = useBuildPreflight(agent.id, chosen, { enabled: !!chosen });
  const blocked = preflight.data ? !preflight.data.ok : false;

  const build = async () => {
    setBuilding(true);
    try {
      const dto = await createHarnessJob(
        buildFromAgentPayload({
          agent,
          version: versions.find((v) => v.id === chosen),
          name: name.trim(),
          scenarioCount,
        }),
        harnessIdempotencyKey(),
      );
      onClose();
      navigate(paths.dashboard.simulate.environments.detail(dto.job.job_id));
    } catch (e) {
      setBuilding(false);
      enqueueSnackbar(errorMessage(e) || "Couldn't start the build", {
        variant: "error",
      });
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
            {versions.map((v) => (
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
          {preflight.data && (
            <Box data-testid="build-env-preflight">
              <Stack spacing={0.75}>
                {preflight.data.checks.map((check) => (
                  <Check key={check.name} check={check} />
                ))}
              </Stack>
            </Box>
          )}
        </Stack>
      }
      action={
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={building || !chosen || !name.trim() || blocked}
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
