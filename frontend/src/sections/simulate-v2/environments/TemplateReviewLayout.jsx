import PropTypes from "prop-types";
import { useState, useMemo } from "react";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { useEnvState } from "../store";
import AssistantConsole from "../assistant/AssistantConsole";
import DerivedPanels from "./DerivedPanels";

/**
 * Post-fit-check review screen — exactly the same split-view shape
 * BuildFromAgent shows after derivation completes: a narrow
 * AssistantConsole on the left and the real DerivedPanels
 * (Overview / Agents / Contract / Scenarios / Personas / Actors /
 * Evaluations tabs) on the right.
 *
 * The env is already adopted into the store by the time this renders,
 * so DerivedPanels is looking at the real live envState (same one the
 * post-Finish workspace will show). This means everything on the right
 * — including the twin sandbox preview on the Overview tab — is
 * already live; the user is just previewing and tweaking before
 * committing/provisioning.
 */
export default function TemplateReviewLayout({
  env, onFinish, onBack, isTwin,
}) {
  const { envState, patch } = useEnvState(env.id);
  const scenarioCount = envState?.scenarios?.length || 0;

  const [turns, setTurns] = useState(() => [
    {
      id: "a-init",
      role: "assistant",
      steps: [
        {
          kind: "note",
          text: `Loaded the ${env.name} template. It ships ${scenarioCount} scenarios, ${(env.tools || []).length} tools and ${(env.rules || []).length} rules. Tweak anything you want here — drop scenarios, tighten a rule, add a fresh case — or commit as-is.`,
        },
      ],
    },
  ]);
  const [running, setRunning] = useState(false);

  const chips = ["Drop payments scenarios", "Tighten refund rule", "Add a dispute case", "Add adversarial callers"];

  const send = (text) => {
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    setTurns((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", text: trimmed }]);
    setRunning(true);
    setTimeout(() => {
      setRunning(false);
      setTurns((prev) => [...prev, {
        id: `a-${Date.now()}`,
        role: "assistant",
        steps: [{ kind: "note", text: mockAssistantReply(trimmed, scenarioCount) }],
      }]);
    }, 900);
  };

  /*
    Fake the derivation state — DerivedPanels gates tabs on `done`
    entries and dims itself when `running` is true. For a template
    everything is already built, so mark all stages complete and
    render at full opacity.
  */
  const done = useMemo(() => ["understand", "scenarios"], []);
  const source = useMemo(() => ({ kind: "template", templateId: env.id }), [env.id]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <Stack
        direction="row" alignItems="center" spacing={2}
        sx={{ px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Tooltip arrow title="Back to Connect step">
          <IconButton size="small" onClick={onBack}>
            <Iconify icon="solar:alt-arrow-left-linear" width={17} />
          </IconButton>
        </Tooltip>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>{env.name}</Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            Review the template — tweak on the left, preview on the right, commit when ready
          </Typography>
        </Box>
        {/*
          For clone envs the run kicks off directly from this header —
          gate it on evals being added, since a run with no graders
          produces no verdict. Tooltip explains why the button is
          disabled; clicking is a no-op until the user picks graders
          on the Evaluations tab. Template flow (isTwin=false) keeps
          its "Finish setup" behaviour unchanged.
        */}
        {isTwin ? (
          (() => {
            const evalsCount = envState?.evals?.length || 0;
            const canRun = evalsCount > 0;
            return (
              <Tooltip
                arrow
                title={canRun ? "" : "Add at least one evaluation on the Evaluations tab — a run with no graders can't be scored."}
              >
                <Box component="span">
                  <Button
                    variant="contained" color="primary"
                    onClick={canRun ? onFinish : undefined}
                    disabled={!canRun}
                    startIcon={<Iconify icon="solar:play-circle-linear" width={15} />}
                    sx={{ typography: "s2", fontWeight: 700 }}
                  >
                    Run simulation
                  </Button>
                </Box>
              </Tooltip>
            );
          })()
        ) : (
          <Button
            variant="contained" color="primary"
            onClick={onFinish}
            startIcon={<Iconify icon="solar:play-circle-linear" width={15} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Finish setup
          </Button>
        )}
      </Stack>

      <Box
        sx={{
          flex: 1, minHeight: 0, display: "grid", gap: 2, p: 2,
          gridTemplateColumns: { xs: "1fr", lg: "minmax(360px, 400px) 1fr" },
        }}
      >
        <SectionCard sx={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <AssistantConsole
            turns={turns} running={running} chips={chips}
            onSend={send} onChip={(c) => send(c)}
          />
        </SectionCard>

        <SectionCard sx={{ minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", px: 2.5 }}>
          <DerivedPanels
            env={env}
            envState={envState}
            patch={patch}
            source={source}
            done={done}
            running={false}
          />
        </SectionCard>
      </Box>
    </Box>
  );
}

TemplateReviewLayout.propTypes = {
  env: PropTypes.object.isRequired,
  onFinish: PropTypes.func,
  onBack: PropTypes.func,
  isTwin: PropTypes.bool,
};

function mockAssistantReply(userText, scenarioCount) {
  const t = userText.toLowerCase();
  if (/drop|remove|cut/.test(t) && /scenario/.test(t)) {
    const n = Math.max(1, Math.min(scenarioCount, 3 + (userText.length % 5)));
    return `Dropped ${n} scenario${n === 1 ? "" : "s"} matching that. ${scenarioCount - n} left. Preview on the right updated.`;
  }
  if (/add/.test(t) && /scenario/.test(t)) {
    return "Added one scenario — you'll see it in the list on the right. Anything else, or ready to commit?";
  }
  if (/rule/.test(t)) {
    return "Updated the rule. The grader will enforce the tighter wording on the next run.";
  }
  if (/tool/.test(t)) {
    return "Noted. Tool schema updated — fit check re-ran and still passes.";
  }
  return "Understood. Applied that to the env — preview on the right reflects the change. Ready when you are.";
}
