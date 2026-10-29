import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, Link, MenuItem, Select, Stack, Typography } from "@mui/material";
import { creditsLabel } from "../_mock/metering";
import { ceilingLabel } from "../_mock/optimizationRuns";
import { useAgentLimits } from "../agents/agentLimits";
import { useSimStore } from "../store";
import { agentFilesOf } from "../_mock/agentFiles";
import { agentDefinitionOf } from "../_mock/agentRegistry";
import AgentConstraintsTab from "../agents/AgentConstraintsTab";
import {
  BUDGET_STEPS,
  COST_STEPS,
  LATENCY_STEPS,
  MINUTE_STEPS,
  agentPatchFor,
  agentValueOf,
  minutesLabel,
  stepsWith,
} from "./constraintEdits";

/*
  An improvement's spend (budget, time cap), picked where it's set up —
  never a page away. The agent's run budget and time cap are ceilings: a
  value under one is just this improvement's choice; a value past one
  applies to this improvement only, and asks once, in place, whether the
  agent's own limit should move too.
*/

const selectSx = {
  height: 28,
  minWidth: 150,
  typography: "s2",
  "& .MuiSelect-select": { typography: "s2", py: 0.5 },
};

const LABEL = {
  dollars: (v) => creditsLabel(v),
  minutes: (v) => minutesLabel(v),
  costCeiling: (v) => ceilingLabel(v),
  latencyCeiling: (v) => ceilingLabel(v),
};

/* "Past the agent's limit — this improvement only · Save to the agent". */
function PastLimitLine({ agentName, field, value, limit, onSave }) {
  const [saved, setSaved] = useState(null);
  if (saved === value)
    return (
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
        {`Saved — ${LABEL[field](value)} is now ${agentName}'s limit.`}
      </Typography>
    );
  if (limit == null || value <= limit) return null;
  return (
    <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}>
      {`Past ${agentName}'s limit of ${LABEL[field](limit)} — this improvement only. `}
      <Link
        component="button"
        type="button"
        underline="hover"
        onClick={() => {
          onSave();
          setSaved(value);
        }}
        sx={{ typography: "s3", fontWeight: 600, verticalAlign: "baseline" }}
      >
        Update the agent&apos;s constraint too
      </Link>
    </Typography>
  );
}
PastLimitLine.propTypes = {
  agentName: PropTypes.string,
  field: PropTypes.string,
  value: PropTypes.number,
  limit: PropTypes.number,
  onSave: PropTypes.func,
};

/** One picker for an editable constraint, with the past-the-limit line under it. */
export function ConstraintPicker({
  agent,
  limits,
  field,
  value,
  onChange,
  steps,
  fullWidth = false,
  sx,
}) {
  const { set } = useAgentLimits(agent?.key);
  const limit = agentValueOf(limits, field);
  /* Explicit steps are the whole list; otherwise the usual steps, keeping
     the agent's limit and the current value selectable. */
  const all = steps
    ? stepsWith(steps, value)
    : stepsWith(
        {
          dollars: BUDGET_STEPS,
          minutes: MINUTE_STEPS,
          costCeiling: COST_STEPS,
          latencyCeiling: LATENCY_STEPS,
        }[field],
        value,
        limit,
      );
  return (
    <Box sx={{ minWidth: 0, ...(fullWidth && { width: 1 }) }}>
      <Select
        size="small"
        fullWidth={fullWidth}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        renderValue={(v) => LABEL[field](v)}
        sx={{ ...selectSx, ...sx }}
      >
        {all.map((n) => (
          <MenuItem key={n} value={n} sx={{ typography: "s2", gap: 2 }}>
            <Box component="span" sx={{ flex: 1 }}>
              {LABEL[field](n)}
            </Box>
            {limit != null && n > limit && (
              <Box
                component="span"
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                Past the agent&apos;s limit
              </Box>
            )}
            {n === limit && (
              <Box
                component="span"
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                Agent&apos;s limit
              </Box>
            )}
          </MenuItem>
        ))}
      </Select>
      {agent && (
        <PastLimitLine
          agentName={agent.name || "the agent"}
          field={field}
          value={value}
          limit={limit}
          onSave={() => set(agentPatchFor(limits, field, value))}
        />
      )}
    </Box>
  );
}
ConstraintPicker.propTypes = {
  agent: PropTypes.object,
  limits: PropTypes.object.isRequired,
  field: PropTypes.oneOf([
    "dollars",
    "minutes",
    "costCeiling",
    "latencyCeiling",
  ]).isRequired,
  value: PropTypes.number,
  onChange: PropTypes.func.isRequired,
  steps: PropTypes.array,
  fullWidth: PropTypes.bool,
  sx: PropTypes.object,
};

/**
 * Advanced in an improvement's setup. Two kinds of constraint, kept apart:
 * what this improvement may spend (its time cap here, its budget beside the
 * goal) is its own; the agent's rules — what may change, what's never
 * edited, the model, cost and latency — are the agent's Constraints tab
 * itself, edited in place and saved to the agent, so every improvement for
 * it is held to the same rules. Never a one-off loosening.
 */
export default function ImprovementConstraints({
  agent,
  env,
  envState,
  limits,
  edits,
  onChange,
  model = null,
  showTime = true,
}) {
  const { state } = useSimStore();
  const files = useMemo(() => {
    if (!agent || !env) return [];
    try {
      return agentFilesOf({
        agent,
        version: (agent.versions || []).find((v) => v.label === agent.latest),
        env,
        envState,
      }).files;
    } catch (e) {
      return [];
    }
  }, [agent, env, envState]);
  const speech = useMemo(() => {
    if (!agent || !env) return null;
    const def = agentDefinitionOf({ agent, env, envState });
    return def?.voice?.visible ? def.voice : null;
  }, [agent, env, envState]);
  /* Must-pass across the agent's environments — what the tab shows. */
  const mustPass = useMemo(() => {
    const items = (agent?.usedIn || []).flatMap((u) =>
      (state.byEnv[u.envId]?.scenarios || [])
        .filter((x) => x.critical)
        .map((x) => ({
          id: x.id,
          name: x.title || x.name || "Scenario",
          persona:
            typeof x.persona === "string" ? x.persona : x.persona?.name || null,
          envId: u.envId,
          envName: u.envName,
        })),
    );
    return { total: items.length, envs: (agent?.usedIn || []).length, items };
  }, [agent, state.byEnv]);
  if (!agent) return null;
  return (
    <Stack spacing={2.5}>
      {showTime && (
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            This improvement
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 1 }}>
            What this one may spend. The budget is beside the goal.
          </Typography>
          <Stack direction="row" spacing={1.5} alignItems="flex-start">
            <Typography
              sx={{
                typography: "s2",
                color: "text.subtitle",
                width: 160,
                flexShrink: 0,
                lineHeight: "28px",
              }}
            >
              Time limit
            </Typography>
            <ConstraintPicker
              agent={agent}
              limits={limits}
              field="minutes"
              value={edits?.minutes ?? agentValueOf(limits, "minutes")}
              onChange={(v) => onChange({ ...edits, minutes: v })}
            />
          </Stack>
        </Box>
      )}
      <Box>
        {agent.deleted?.at ? (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`${agent.name || "This agent"} was deleted — its rules apply as they were.`}
          </Typography>
        ) : (
          <AgentConstraintsTab
            agent={agent}
            files={files}
            model={model}
            speech={speech}
            mustPass={mustPass}
            embedded
          />
        )}
      </Box>
    </Stack>
  );
}
ImprovementConstraints.propTypes = {
  agent: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  limits: PropTypes.object.isRequired,
  edits: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  model: PropTypes.string,
  showTime: PropTypes.bool,
};
