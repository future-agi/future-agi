import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Button, Dialog, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useSimStore, useConnectedAgents } from "../store";
import { mainEnvOf, agentStatusOf } from "../_mock/agentLifecycle";
import { baseRunOf, buildImprovement } from "../_mock/improvementStart";
import { productionAgentVersion } from "../_mock/versions";
import { isComingSoon } from "../_mock/rsiModel";
import { clickable, focusRing } from "../components/clickable";
import ImprovementSetup from "../run/fixmyagent/ImprovementSetup";
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

  if (picked && env) {
    return (
      <ImprovementSetup
        open={open}
        onClose={close}
        preset={{ agentKey: picked.key, envId: env.id, fromGoal: true }}
        onStart={start}
      />
    );
  }

  return (
    <Dialog open={open} onClose={close} maxWidth={false} PaperProps={{ sx: { width: 520, maxWidth: "calc(100% - 32px)", borderRadius: 1.5, backgroundImage: "none" } }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
        <Box flex={1}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>New improvement</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Which agent? It runs in that agent&apos;s main environment.</Typography>
        </Box>
        <IconButton size="small" onClick={close} aria-label="Close"><Iconify icon="eva:close-fill" width={17} /></IconButton>
      </Stack>
      <Stack sx={{ py: 0.75, maxHeight: "60vh", overflowY: "auto" }}>
        {choices.length === 0 && (
          <Typography sx={{ typography: "s2", color: "text.secondary", px: 2.5, py: 2 }}>
            No agent has an environment yet. Build one for an agent first — an improvement is proven there.
          </Typography>
        )}
        {choices.map(({ agent, main: m, status }) => {
          const off = status && status.status !== "connected";
          return (
            <Stack
              key={agent.key} direction="row" alignItems="center" spacing={1.5}
              {...clickable(off ? null : () => setPicked(agent), { disabled: off })}
              sx={{ ...focusRing, px: 2.5, py: 1.125, cursor: off ? "default" : "pointer", opacity: off ? 0.55 : 1, "&:hover": off ? undefined : { bgcolor: "action.hover" } }}
            >
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{agent.name}</Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                  {off ? `${status.label}${status.reason ? ` — ${status.reason}` : ""}` : `In production ${agent.production || "—"} · main environment ${m?.envName || "—"}${(agent.usedIn || []).length > 1 ? ` · ${agent.usedIn.length - 1} more` : ""}`}
                </Typography>
              </Box>
              {!off && <Iconify icon="solar:alt-arrow-right-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />}
            </Stack>
          );
        })}
      </Stack>
      <Stack direction="row" justifyContent="flex-end" sx={{ px: 2.5, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
        <Button onClick={close} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Cancel</Button>
      </Stack>
    </Dialog>
  );
}
NewImprovement.propTypes = { open: PropTypes.bool, onClose: PropTypes.func.isRequired };
