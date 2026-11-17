import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import NotInReleaseButton from "src/components/NotInRelease/NotInReleaseButton";
import { paths } from "src/routes/paths";
import SectionCard from "../../components/SectionCard";
import { ENTRY_TAB } from "../../environmentOptions";
import { WORKSPACE_COPY } from "../workspace.constants";
import { ENV_SHAPE, ENV_STATE_SHAPE, CHECKLIST_COPY } from "./overview.constants";

// What is left before this environment can run its first simulation. Shown only
// for envs that have not been seeded yet (no agent, no derived world) — a
// scratch env arrives with everything derived, so every step would read as
// pre-complete and the checklist would mislead.
// An agent is set when an environment is built, so the agent step opens the
// Build tab, which makes a new environment from the agent; this one stays as
// it is. Run gives the same reason as the header's Run while a run can't
// start, and starts a run the same way when one can.
export default function NextStepsChecklist({
  env,
  envState,
  onGo,
  scenarioCount: serverScenarioCount,
  onStartRun,
  canRun = false,
  runBlockedReason,
}) {
  const navigate = useNavigate();
  const scenarioCount = serverScenarioCount ?? (envState?.scenarios?.length || 0);
  const evalCount = envState?.evals?.length || 0;
  const runCount = envState?.runs?.length || 0;

  const steps = [
    {
      id: "created",
      title: CHECKLIST_COPY.created.title,
      body: CHECKLIST_COPY.created.body(env.name),
      done: true,
    },
    {
      id: "agent",
      title: CHECKLIST_COPY.agent.title,
      body: CHECKLIST_COPY.agent.body,
      done: false,
      cta: CHECKLIST_COPY.agent.cta,
      onClick: () =>
        navigate(`${paths.dashboard.simulate.environments.root}?tab=${ENTRY_TAB.BUILD}`),
      icon: "solar:link-circle-linear",
    },
    {
      id: "scenarios",
      title: CHECKLIST_COPY.scenarios.title,
      body: CHECKLIST_COPY.scenarios.body,
      done: scenarioCount > 0,
      cta: CHECKLIST_COPY.scenarios.cta(scenarioCount),
      onClick: () => onGo?.("scenarios"),
      icon: "solar:list-check-linear",
    },
    {
      id: "evals",
      title: CHECKLIST_COPY.evals.title,
      body: CHECKLIST_COPY.evals.body,
      done: evalCount > 0,
      cta: CHECKLIST_COPY.evals.cta(evalCount),
      onClick: () => onGo?.("evals"),
      icon: "solar:target-linear",
    },
    {
      id: "run",
      title: CHECKLIST_COPY.run.title,
      body: CHECKLIST_COPY.run.body,
      done: runCount > 0,
      cta: CHECKLIST_COPY.run.cta,
      onClick: () => onStartRun?.(undefined, 1),
      disabled: !canRun || !onStartRun,
      reason: runBlockedReason || WORKSPACE_COPY.runBlocked.generic,
      icon: "solar:play-circle-linear",
    },
  ];

  const doneCount = steps.filter((s) => s.done).length;
  const currentIdx = steps.findIndex((s) => !s.done);

  return (
    <SectionCard
      dense
      title={CHECKLIST_COPY.title}
      subtitle={CHECKLIST_COPY.subtitle(doneCount, steps.length)}
      sx={{ mb: 3 }}
    >
      <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
        {steps.map((s, i) => {
          const isCurrent = i === currentIdx;
          return (
            <Stack
              key={s.id}
              direction="row" alignItems="center" spacing={2}
              sx={{
                px: 1.5, py: 1.5,
                bgcolor: isCurrent ? "action.hover" : "transparent",
              }}
            >
              <Box sx={{
                width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
                display: "grid", placeItems: "center",
                bgcolor: s.done ? "status.success.bg" : "action.selected",
                color: s.done ? "status.success.fg" : "text.subtitle",
              }}>
                <Iconify icon={s.done ? "solar:check-circle-bold" : (s.icon || "solar:circle-linear")} width={14} />
              </Box>
              <Box flex={1} minWidth={0}>
                <Typography sx={{
                  typography: "s1", fontWeight: "fontWeightMedium",
                  color: s.done ? "text.subtitle" : "text.primary",
                  textDecoration: s.done ? "line-through" : "none",
                }}>
                  {s.title}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {s.body}
                </Typography>
              </Box>
              {!s.done && s.cta && s.disabled && (
                <NotInReleaseButton
                  reason={s.reason}
                  variant={isCurrent ? "contained" : "outlined"}
                  color={isCurrent ? "primary" : "inherit"}
                  size="small"
                  sx={{ flexShrink: 0 }}
                >
                  {s.cta}
                </NotInReleaseButton>
              )}
              {!s.done && s.cta && !s.disabled && (
                <Box component="span" sx={{ display: "inline-flex", flexShrink: 0 }}>
                  <Button
                    variant={isCurrent ? "contained" : "outlined"}
                    color={isCurrent ? "primary" : "inherit"}
                    size="small"
                    onClick={s.onClick}
                    sx={{ flexShrink: 0 }}
                  >
                    {s.cta}
                  </Button>
                </Box>
              )}
            </Stack>
          );
        })}
      </Stack>
    </SectionCard>
  );
}
NextStepsChecklist.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: ENV_STATE_SHAPE,
  onGo: PropTypes.func,
  scenarioCount: PropTypes.number,
  // The workspace's own Run: the same start, gate and blocked reason as the
  // header's Run button.
  onStartRun: PropTypes.func,
  canRun: PropTypes.bool,
  runBlockedReason: PropTypes.string,
};
