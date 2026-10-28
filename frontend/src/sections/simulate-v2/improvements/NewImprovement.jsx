import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Typography } from "@mui/material";
import { paths } from "src/routes/paths";
import { useSimStore, useConnectedAgents } from "../store";
import { mainEnvOf, agentStatusOf } from "../_mock/agentLifecycle";
import { baseRunOf, buildImprovement, todaySpend } from "../_mock/improvementStart";
import { productionAgentVersion } from "../_mock/versions";
import { isComingSoon } from "../_mock/rsiModel";
import ImprovementSetup from "../run/fixmyagent/ImprovementSetup";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import { buildPresetFor } from "../agents/agentViewUtils";
import SideDrawer from "../components/SideDrawer";
import AgentPicker from "./AgentPicker";
import { agentPickerChoice, agentPickerHelper } from "./agentPickerChoices";
import { improvementPath } from "./improvementNav";

/*
  New improvement (RSI PRD, Flow 4): pick the agent, then the same setup a
  run's Debug failures opens — on the agent's main environment, its other
  environments optional, aimed at a goal. Agents we can only call start
  here too: they rarely send anything to the Error Feed. An agent with no
  environment is listed too: Build environment hands over to the normal
  build, which ends on Run simulation — that first run shows where the agent
  fails, and the person improves from what it finds.
*/
export default function NewImprovement({ open, onClose }) {
  const navigate = useNavigate();
  /* commit: it may be opened outside Improvements, and navigates there. */
  const { state, commit } = useSimStore();
  const { agents } = useConnectedAgents();
  const [picked, setPicked] = useState(null);
  const [buildOpen, setBuildOpen] = useState(false);

  /* Agents that can be improved: real (not a template's stand-in) and a type
     improvements run on. One with no environment yet is listed too. */
  const choices = useMemo(() => agents
    .filter((a) => !a.placeholder && !isComingSoon(a) && !isComingSoon(mainEnvOf(a, state)?.env))
    .map((a) => ({ agent: a, main: mainEnvOf(a, state), status: agentStatusOf(a) })), [agents, state]);

  const close = () => { setPicked(null); onClose(); };
  const main = picked ? mainEnvOf(picked, state) : null;
  const env = main?.env || null;
  /* Picked, but nowhere to score it yet. */
  const needsEnv = !!picked && !env;

  const start = ({ name, model, writerModel, envIds, goal, budget, limits, seed, lineage, reread, textSearch, optimizerId, base, today, depth }) => {
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
      /* Measuring today is spent before the search: inside the budget and the Cost. */
      spentFirst: today ? todaySpend(today, env.surface) : 0,
      extra: { ...(name && { name }), ...(model && { model }), ...(writerModel && { writerModel }), ...(depth && { fixDepth: depth, suggestChosen: true }) },
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
  /* Why nobody is listed: no agents, or none of a type improvements run on yet. */
  const emptyText = useMemo(() => {
    const real = agents.filter((a) => !a.placeholder);
    if (!real.length) return "No agents connected yet — connect one from Agents first.";
    return "None of your agents can be improved yet — improvements don't run on their type yet.";
  }, [agents]);
  const agentField = (
    <Box>
      <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Agent</Typography>
      <AgentPicker
        choices={pickerChoices}
        emptyText={emptyText}
        value={picked?.key || null}
        onChange={(key) => setPicked(choices.find((c) => c.agent.key === key)?.agent || null)}
      />
      {pickedChoice && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
          {needsEnv
            ? `An improvement is scored in an environment. Build one from ${picked.name} — a few minutes — then run it once and improve from what it finds.`
            : agentPickerHelper(pickedChoice, main?.envName)}
        </Typography>
      )}
    </Box>
  );

  return (
    <SideDrawer open={open} onClose={close} width={{ xs: "100%", sm: 640 }}>
      <ImprovementSetup
        bare open={open} onClose={close}
        agentField={agentField}
        preset={picked && env ? { agentKey: picked.key, envId: env.id, fromGoal: true } : { ...(picked && { agentKey: picked.key }), fromGoal: true }}
        onStart={start}
        onBuildEnv={needsEnv ? () => setBuildOpen(true) : null}
      />
      {needsEnv && (
        <BuildEnvironmentDialog
          open={buildOpen}
          onClose={() => setBuildOpen(false)}
          agent={picked}
          defaultVersion={picked.production || picked.latest}
          onBuild={(choice) => {
            setBuildOpen(false);
            close();
            navigate(paths.dashboard.simulate.environmentBuild, {
              state: { presetSource: buildPresetFor(picked, choice) },
            });
          }}
        />
      )}
    </SideDrawer>
  );
}
NewImprovement.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};
