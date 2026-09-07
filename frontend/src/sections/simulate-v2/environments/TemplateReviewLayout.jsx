import PropTypes from "prop-types";
import { useState, useMemo, useEffect, useRef } from "react";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { useEnvState } from "../store";
import { builderRun } from "../_mock/builder";
import AssistantConsole from "../assistant/AssistantConsole";
import DerivedPanels from "./DerivedPanels";

const STAGE_ORDER = ["understand", "build", "scenarios"];

/**
 * Post-fit-check review screen — exactly the same split-view shape
 * BuildFromAgent shows after derivation completes: a narrow
 * AssistantConsole on the left and the real DerivedPanels
 * (Overview / Agents / Contract / Scenarios / Evaluations tabs)
 * on the right.
 *
 * The env is already adopted into the store by the time this renders,
 * so DerivedPanels is looking at the real live envState (same one the
 * post-Finish workspace will show). This means everything on the right
 * — including the twin sandbox preview on the Overview tab — is
 * already live; the user is just previewing and tweaking before
 * committing/provisioning.
 */

/*
  Suggested chip prompts, keyed by the tab the user is currently
  viewing. Each list is short (3-4) so the composer stays uncluttered
  and each one is a concrete action the builder can actually take on
  that tab's data. New tabs added to DerivedPanels should add an entry
  here or they'll fall back to the Overview set.
*/
const CHIPS_BY_TAB = {
  overview: [
    "Summarise what's in this environment",
    "What's still missing before we can run?",
    "Explain the tools and rules to me",
  ],
  agent: [
    "Add a new version of the agent",
    "Roll back to the previous version",
    "What changed between versions?",
  ],
  contract: [
    "Tighten the refund rule",
    "Add a hard rule about escalations",
    "Add an adversarial actor",
    "Explain the reward function",
  ],
  scenarios: [
    "Drop payments scenarios",
    "Add a dispute case",
    "Add an edge case where the tool fails",
    "Rewrite the rushed-caller persona",
  ],
  evals: [
    "Add a grader for tool-choice correctness",
    "Tighten the hand-off quality bar",
    "Explain what each grader measures",
  ],
};
export default function TemplateReviewLayout({
  env, onFinish, onBack, isTwin, initialTurns,
}) {
  const { envState, patch } = useEnvState(env.id);
  const scenarioCount = envState?.scenarios?.length || 0;

  /*
    `initialTurns` lets the caller seed the chat with prior history —
    used by the scratch flow to carry the build transcript over
    (`READING → EXTRACTING → SEEDING → …`) so the user's original
    description and the steps the builder took stay visible on the
    review page, matching how the build-from-agent flow persists its
    derivation history. If nothing is passed, fall back to the
    template greeting.
  */
  /* If the caller preseeded turns (scratch flow carries its build
     transcript over) we take those verbatim and mark everything as
     already built. Otherwise we stream the stages fresh on mount, so
     the user watches the build unfold before landing on the review. */
  const seeded = Array.isArray(initialTurns) && initialTurns.length > 0;
  const [turns, setTurns] = useState(() => (seeded ? initialTurns : []));
  const [running, setRunning] = useState(!seeded);
  const [activeTab, setActiveTab] = useState("overview");
  const [done, setDone] = useState(() => (seeded ? STAGE_ORDER.slice() : []));
  const timers = useRef([]);

  /*
    Stream the derivation stages on mount — same shape BuildFromAgent
    uses so the review screen carries the same "we're standing this up"
    animation before it settles. Each step lands 380ms after the last;
    stages chain automatically, and once every stage is done the panel
    on the right unfreezes and Finish setup goes live.
  */
  useEffect(() => {
    if (seeded) return undefined;
    const source = { kind: "template", value: env.name, templateId: env.id };
    const play = (stageId, delay = 0) => {
      const stage = builderRun(stageId, source);
      const turnId = `t-${stageId}-${Date.now()}`;
      timers.current.push(setTimeout(() => {
        setTurns((prev) => [...prev, { id: turnId, role: "builder", title: stage.title, steps: [] }]);
      }, delay));
      stage.steps.forEach((step, i) => {
        timers.current.push(setTimeout(() => {
          setTurns((prev) => prev.map((t) => (t.id === turnId ? { ...t, steps: [...t.steps, step] } : t)));
          if (i !== stage.steps.length - 1) return;
          setDone((d) => (d.includes(stageId) ? d : [...d, stageId]));
          const nextId = STAGE_ORDER[STAGE_ORDER.indexOf(stageId) + 1];
          if (nextId) {
            play(nextId, 500);
          } else {
            setRunning(false);
          }
        }, delay + 380 * (i + 1)));
      });
    };
    /* Kick off with a short greeting so the log doesn't start empty. */
    setTurns([{
      id: "a-init",
      role: "assistant",
      steps: [{
        kind: "note",
        text: `Setting up the ${env.name} template — ${scenarioCount} scenarios, ${(env.tools || []).length} tools, ${(env.rules || []).length} rules. Building the world now.`,
      }],
    }]);
    play("understand", 600);
    return () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /*
    Suggested-message chips change with the tab the user is looking
    at. Reading the tabs' content and firing a "add X" chip against
    the wrong tab was the old failure mode — asking to "drop payment
    scenarios" while sitting on the Contract tab made the assistant
    feel context-blind. Now every tab carries its own short list of
    prompts that make sense for what's on-screen.
  */
  const chips = useMemo(() => (CHIPS_BY_TAB[activeTab] || CHIPS_BY_TAB.overview), [activeTab]);

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
    DerivedPanels reads `done` (which stages have completed) and
    `running` (whether the builder is mid-stage). The streaming effect
    above advances both as each stage settles, so tabs light up
    progressively instead of appearing pre-built.
  */
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
        {running && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{
              px: 1.25, py: 0.5, borderRadius: 1,
              border: "1px solid",
              borderColor: (t) => t.palette.mode === "dark" ? "rgba(120, 87, 252, 0.5)" : "rgba(120, 87, 252, 0.35)",
              bgcolor: (t) => t.palette.mode === "dark" ? "rgba(120, 87, 252, 0.14)" : "rgba(120, 87, 252, 0.08)",
              color: "#7857FC",
              flexShrink: 0,
            }}
          >
            <Box sx={{
              width: 7, height: 7, borderRadius: "50%",
              bgcolor: "#7857FC",
              animation: "pulse 1.4s ease-in-out infinite",
              "@keyframes pulse": { "0%,100%": { opacity: 0.4 }, "50%": { opacity: 1 } },
            }} />
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>Setup being built</Typography>
          </Stack>
        )}
        {/*
          Setup is complete by the time we hit the review layout — the
          builder streamed every stage and the world is standing. The
          only meaningful action left is running a simulation, so the
          CTA reads "Run simulation" on every flow (template, twin,
          scratch) and the "Finish setup" label is retired.
          Gate on evals: a run with no graders produces no verdict, so
          the button stays disabled until the user adds at least one
          from the Evaluations tab. While the builder is still
          streaming we also disable it so users can't fire before the
          setup finishes.
        */}
        {(() => {
          const evalsCount = envState?.evals?.length || 0;
          const hasEvals = evalsCount > 0;
          const canRun = !running && hasEvals;
          const disabledReason = running
            ? "Building the environment — one moment…"
            : !hasEvals
              ? "Add at least one evaluation on the Evaluations tab — a run with no graders can't be scored."
              : "";
          return (
            <Tooltip arrow title={disabledReason}>
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
        })()}
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

        <SectionCard sx={{
          height: "100%", minHeight: 0,
          display: "flex", flexDirection: "column",
          overflow: "hidden", px: 2.5,
        }}>
          <DerivedPanels
            env={env}
            envState={envState}
            patch={patch}
            source={source}
            done={done}
            running={running}
            onTabChange={setActiveTab}
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
  initialTurns: PropTypes.array,
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
