import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Typography } from "@mui/material";
import { useSimStore, useConnectedAgents } from "../store";
import { mainEnvOf, agentStatusOf } from "../_mock/agentLifecycle";
import { baseRunOf, buildImprovement } from "../_mock/improvementStart";
import { productionAgentVersion } from "../_mock/versions";
import { isComingSoon } from "../_mock/rsiModel";
import ImprovementSetup from "../run/fixmyagent/ImprovementSetup";
import SideDrawer from "../components/SideDrawer";
import AgentPicker from "./AgentPicker";
import { agentPickerChoice, agentPickerHelper } from "./agentPickerChoices";
import { improvementPath } from "./improvementNav";

/*
  New improvement (RSI PRD, Flow 4): pick the agent, then the same setup a
  run's Debug failures opens — on the agent's main environment, its other
  environments optional, aimed at a goal. Agents we can only call start
  here too: they rarely send anything to the Error Feed.
*/
export default function NewImprovement({ open, onClose }) {
  const navigate = useNavigate();
  /* commit: it may be opened outside Improvements, and navigates there. */
  const { state, commit } = useSimStore();
  const { agents } = useConnectedAgents();
  const [picked, setPicked] = useState(null);

  /* Agents that can be improved: real (not a template's stand-in), a type
     improvements run on, and tested in an environment. */
  const choices = useMemo(() => agents
    .filter((a) => !a.placeholder && !isComingSoon(a) && !isComingSoon(mainEnvOf(a, state)?.env) && (a.usedIn || []).length > 0)
    .map((a) => ({ agent: a, main: mainEnvOf(a, state), status: agentStatusOf(a) })), [agents, state]);

  const close = () => { setPicked(null); onClose(); };
  const main = picked ? mainEnvOf(picked, state) : null;
  const env = main?.env || null;

  const start = ({ name, model, writerModel, envIds, goal, budget, limits, seed, lineage, reread, textSearch, optimizerId, base, today }) => {
    const es = state.byEnv[env.id];
    /* Measuring today is a real run of the environment, recorded first. */
    if (today) commit({ type: "recordRun", envId: env.id, run: today });
    const from = base || baseRunOf(env, es, productionAgentVersion(es)?.label) || baseRunOf(env, es);
    if (!from?.tasks) return;
    const record = buildImprovement({
      env, es, tasks: from.tasks, seed, state, reread, textSearch, optimizerId,
      fromRunId: from.id,
      baseAgentVersion: from.agentVersion || null,
      trigger: { kind: "goal", goal: goal ? { id: goal.id, label: goal.label, ...(goal.group && { group: goal.group }), ...(goal.reliability && { reliability: goal.reliability }) } : undefined },
      limits, budget, regressionEnvIds: envIds, lineage,
      extra: { ...(name && { name }), ...(model && { model }), ...(writerModel && { writerModel }) },
    });
    commit({ type: "patchEnvState", envId: env.id, patch: { optimizations: [...(es.optimizations || []), record] } });
    close();
    navigate(improvementPath(env.id, record.id));
  };

  /*
    One drawer, one form: the agent first, and everything else appears below
    it once it's picked. The picker is the dashboards' filter-attribute one —
    search, agent types and sources on the left, a row per agent — and the
    main environment is said under the field once one is picked.
  */
  const pickerChoices = useMemo(() => choices
    .map((c) => agentPickerChoice(c.agent, c.main?.env, c.status))
    .sort((a, b) => Number(a.off) - Number(b.off) || a.name.localeCompare(b.name)), [choices]);
  const pickedChoice = pickerChoices.find((c) => c.key === picked?.key) || null;
  const agentField = (
    <Box>
      <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Agent</Typography>
      <AgentPicker
        choices={pickerChoices}
        value={picked?.key || null}
        onChange={(key) => setPicked(choices.find((c) => c.agent.key === key)?.agent || null)}
      />
      {pickedChoice && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
          {agentPickerHelper(pickedChoice, main?.envName)}
        </Typography>
      )}
    </Box>
  );

  return (
    <SideDrawer open={open} onClose={close} width={{ xs: "100%", sm: 640 }}>
      <ImprovementSetup
        bare open={open} onClose={close}
        agentField={agentField}
        preset={picked && env ? { agentKey: picked.key, envId: env.id, fromGoal: true } : { fromGoal: true }}
        onStart={start}
      />
    </SideDrawer>
  );
}
NewImprovement.propTypes = { open: PropTypes.bool, onClose: PropTypes.func.isRequired };
