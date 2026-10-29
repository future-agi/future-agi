import PropTypes from "prop-types";
import { useMemo } from "react";
import { MenuItem, Select, Stack, Typography } from "@mui/material";
import { EmptyState } from "../components/primitives";
import { envOf } from "../_mock/runGroup";
import { compatibilityOf } from "../_mock/compatibility";
import { familyOf } from "../_mock/testedVersions";
import { sourceKindOf } from "../_mock/agentRegistry";
import { SectionCard } from "../components/primitives";
import CompatibilityReport from "./CompatibilityReport";
import TestedVersionsMatrix from "./TestedVersionsMatrix";
import SandboxCertification from "./SandboxCertification";

/**
 * Agent → Compatibility (roadmap release.md): the report for this agent on
 * one of the environments testing it — picked here, the main one first.
 * Repair and Test again go to the Connections tab, where the check runs.
 */
export default function AgentCompatibilityTab({
  agent,
  state,
  envId,
  mainEnvId,
  onPickEnv,
  onRepair,
}) {
  const envs = useMemo(
    () =>
      (agent?.usedIn || [])
        .map((u) => envOf(state, u.envId))
        .filter(Boolean)
        .sort((a, b) => (b.id === mainEnvId) - (a.id === mainEnvId)),
    [agent, state, mainEnvId],
  );
  const env = envs.find((e) => e.id === envId) || envs[0] || null;
  const envState = env ? state?.byEnv?.[env.id] : null;
  const report = useMemo(
    () =>
      env && envState
        ? compatibilityOf({
            env,
            envState,
            /* The agent as connected now — its latest check — on this world. */
            agent: {
              ...(envState.agent || {}),
              ...agent,
              values: {
                ...(envState.agent?.values || {}),
                ...(agent?.values || {}),
              },
            },
          })
        : null,
    [env, envState, agent],
  );
  if (!env || !report)
    return (
      <EmptyState
        icon="solar:shield-check-linear"
        title="No environment to check against"
        body="Compatibility is per world — build or add this agent to an environment, and its report appears here."
      />
    );
  const titles = Object.fromEntries(
    (envState.scenarios || []).map((s) => [s.id, s.title || s.name]),
  );
  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Can it run
        </Typography>
        <Select
          size="small"
          value={env.id}
          onChange={(e) => onPickEnv(e.target.value)}
          inputProps={{ "aria-label": "Environment" }}
          sx={{
            typography: "s2",
            minWidth: 240,
            "& .MuiSelect-select": { py: 0.5 },
          }}
        >
          {envs.map((e) => (
            <MenuItem key={e.id} value={e.id} sx={{ typography: "s2" }}>
              {`${e.name}${e.id === mainEnvId ? " · main" : ""}`}
            </MenuItem>
          ))}
        </Select>
      </Stack>
      <CompatibilityReport
        report={report}
        agent={agent}
        env={env}
        titles={titles}
        onRepair={onRepair}
        onRetest={onRepair}
      />
      <SectionCard
        title="Tested versions"
        subtitle={
          familyOf(agent, sourceKindOf(agent))
            ? "Exact versions per agent family, each linked to the diagnostic behind it. This agent's family is open — its own report above is what counts for this world."
            : "Exact versions per agent family, each linked to the diagnostic behind it. This agent's family isn't listed — it may connect, but nothing about it is certified."
        }
        bare
      >
        <TestedVersionsMatrix
          highlight={familyOf(agent, sourceKindOf(agent))?.family || null}
        />
      </SectionCard>
      <SectionCard
        title="Sandbox"
        subtitle="How the test sandbox holds up — concurrency, cleanup, isolation — and whether our benchmark runs match the published ones."
        bare
      >
        <SandboxCertification />
      </SectionCard>
    </Stack>
  );
}
AgentCompatibilityTab.propTypes = {
  agent: PropTypes.object,
  state: PropTypes.object,
  envId: PropTypes.string,
  mainEnvId: PropTypes.string,
  onPickEnv: PropTypes.func.isRequired,
  onRepair: PropTypes.func.isRequired,
};
