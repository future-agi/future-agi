import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import SectionCard from "../../components/SectionCard";
import { ENV_SHAPE, ENV_STATE_SHAPE, CHECKLIST_COPY } from "./overview.constants";

// What is left before this environment can run its first simulation. Shown only
// for envs that have not been seeded yet (no agent, no derived world) — a
// scratch env arrives with everything derived, so every step would read as
// pre-complete and the checklist would mislead.
export default function NextStepsChecklist({ env, envState, onGo }) {
  const scenarioCount = envState?.scenarios?.length || 0;
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
      onClick: () => onGo?.("agent"),
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
      onClick: () => {},
      disabled: true,
      icon: "solar:play-circle-linear",
    },
  ];

  const doneCount = steps.filter((s) => s.done).length;
  const currentIdx = steps.findIndex((s) => !s.done);

  return (
    <SectionCard
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
                px: 2.5, py: 1.75,
                bgcolor: (t) => (isCurrent
                  ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.02)
                  : "transparent"),
              }}
            >
              <Box sx={{
                width: 26, height: 26, borderRadius: "50%", flexShrink: 0,
                display: "grid", placeItems: "center",
                bgcolor: (t) => (s.done
                  ? alpha(BUILD_TONES.green, t.palette.mode === "dark" ? 0.18 : 0.12)
                  : alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05)),
                color: s.done ? BUILD_TONES.green : "text.subtitle",
              }}>
                <Iconify icon={s.done ? "solar:check-circle-bold" : (s.icon || "solar:circle-linear")} width={14} />
              </Box>
              <Box flex={1} minWidth={0}>
                <Typography sx={{
                  typography: "s2", fontWeight: "fontWeightBold",
                  color: s.done ? "text.subtitle" : "text.primary",
                  textDecoration: s.done ? "line-through" : "none",
                }}>
                  {s.title}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {s.body}
                </Typography>
              </Box>
              {!s.done && s.cta && (
                <Button
                  variant={isCurrent ? "contained" : "outlined"}
                  color="primary"
                  size="small"
                  disabled={s.disabled}
                  onClick={s.onClick}
                  sx={{
                    typography: "s2", fontWeight: "fontWeightBold", flexShrink: 0,
                    ...(isCurrent ? {} : {
                      color: "text.primary", borderColor: "divider",
                      "&:hover": { borderColor: "text.disabled" },
                    }),
                  }}
                >
                  {s.cta}
                </Button>
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
};
