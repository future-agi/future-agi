import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import {
  Box,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import ModalWrapper from "src/components/ModalWrapper";
import { useAgentEnvironments } from "src/api/agents/agentDetail";
import { useConnectedAgents } from "src/api/agents/connectedAgents";
import { listScenarios } from "src/api/simulate-environments/scenarios";
import { runnableScenarioKey } from "src/api/simulate-environments/scenarioSelection";
import { useStartSkillTest } from "src/api/simulate-skills/skills";
import { UNSUPPORTED_SOURCES } from "./skillLabels";

const SCENARIO_PAGE = 100;

export default function TestDialog({ open, onClose, skill, version }) {
  const [agentId, setAgentId] = useState("");
  const [environmentId, setEnvironmentId] = useState("");
  const [picked, setPicked] = useState([]);
  const [trials, setTrials] = useState(1);
  const start = useStartSkillTest(skill?.id);
  const agents = useConnectedAgents({ page: 0, pageSize: 100, search: "" });
  const environments = useAgentEnvironments(agentId || null);
  const scenarios = useQuery({
    queryKey: ["simulate-skills", "test-scenarios", environmentId],
    queryFn: () =>
      listScenarios(environmentId, {
        page: 1,
        limit: SCENARIO_PAGE,
        group_by: "",
      }),
    enabled: Boolean(environmentId),
  });

  useEffect(() => {
    if (open) {
      setAgentId("");
      setEnvironmentId("");
      setPicked([]);
      setTrials(1);
    }
  }, [open, version]);

  const rows = useMemo(
    () =>
      (scenarios.data?.results || [])
        .map((row) => ({ key: runnableScenarioKey(row), name: row.name }))
        .filter((row) => row.key),
    [scenarios.data],
  );

  const toggle = (key) =>
    setPicked((all) =>
      all.includes(key) ? all.filter((one) => one !== key) : [...all, key],
    );

  const submit = async () => {
    try {
      await start.mutateAsync({
        version,
        agent_definition_id: agentId,
        environment_id: environmentId,
        scenario_keys: picked,
        trials,
      });
      enqueueSnackbar(
        "Test started: two runs on the same scenarios, current version and this one",
        { variant: "success" },
      );
      onClose();
    } catch (error) {
      enqueueSnackbar(error?.message || "Could not start the test", {
        variant: "error",
      });
    }
  };

  const readyEnvironments = (environments.data || []).filter(
    (env) => env.status === "ready",
  );

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      onCancelBtn={onClose}
      onSubmit={submit}
      isValid={Boolean(agentId && environmentId && picked.length)}
      isLoading={start.isPending}
      title={`Test ${skill?.name} v${version}`}
      subTitle="Runs the agent twice on the same scenarios: once with the version it uses now, once with this one."
      actionBtnTitle="Start test"
      modalWidth="640px"
    >
      <Stack spacing={1.5}>
        <TextField
          select
          size="small"
          label="Agent"
          value={agentId}
          onChange={(event) => {
            setAgentId(event.target.value);
            setEnvironmentId("");
            setPicked([]);
          }}
        >
          {(agents.data?.items || []).map((agent) => (
            <MenuItem
              key={agent.id}
              value={agent.id}
              disabled={UNSUPPORTED_SOURCES.has(agent.source)}
            >
              {UNSUPPORTED_SOURCES.has(agent.source)
                ? `${agent.name} · Unsupported, runs outside our sandbox`
                : agent.name}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Environment"
          value={environmentId}
          onChange={(event) => {
            setEnvironmentId(event.target.value);
            setPicked([]);
          }}
          disabled={!agentId}
          helperText={
            agentId && environments.isSuccess && !readyEnvironments.length
              ? "This agent has no ready environment yet"
              : " "
          }
        >
          {readyEnvironments.map((env) => (
            <MenuItem key={env.id} value={env.id}>
              {env.name}
            </MenuItem>
          ))}
        </TextField>
        {environmentId && (
          <Box>
            <Typography sx={{ typography: "m3", mb: 0.5 }}>
              Scenarios where this skill matters
            </Typography>
            {scenarios.isSuccess && !rows.length && (
              <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                This environment has no saved scenarios. Add scenarios for the
                skill first, then test it.
              </Typography>
            )}
            <Box sx={{ maxHeight: 260, overflowY: "auto" }}>
              {rows.map((row) => (
                <FormControlLabel
                  key={row.key}
                  sx={{ display: "flex", mx: 0 }}
                  control={
                    <Checkbox
                      size="small"
                      checked={picked.includes(row.key)}
                      onChange={() => toggle(row.key)}
                    />
                  }
                  label={
                    <Typography sx={{ typography: "s1" }}>
                      {row.name || row.key}
                    </Typography>
                  }
                />
              ))}
            </Box>
          </Box>
        )}
        <TextField
          size="small"
          type="number"
          label="Trials per scenario"
          value={trials}
          onChange={(event) =>
            setTrials(
              Math.max(1, Math.min(20, Number(event.target.value) || 1)),
            )
          }
          inputProps={{ min: 1, max: 20 }}
          sx={{ width: 200 }}
        />
      </Stack>
    </ModalWrapper>
  );
}

TestDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  skill: PropTypes.object,
  version: PropTypes.number,
};
