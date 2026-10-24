import PropTypes from "prop-types";
import { useState, useMemo, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useEnvState } from "../store";
import { runReadiness } from "../_mock/setupGaps";
import useRunFitCheck from "../run/useRunFitCheck";
import { builderRun } from "../_mock/builder";
import { scenarioPoolFor } from "../_mock/scenarioPool";
import { detectAddScenariosIntent } from "../_mock/addScenariosIntent";
import {
  stampNewBatch,
  unanswerableNote,
  rejectedNote,
} from "../_mock/addScenarios";
import { asProposal } from "../_mock/scenarioSources";
import { currentUser } from "../_mock/scenarioProvenance";
import { currentAgentVersion } from "../_mock/versions";
import { subGoalCatalogOf } from "../_mock/envModel";
import AssistantConsole from "../assistant/AssistantConsole";
import DerivedPanels from "./DerivedPanels";
import AgentReadReceipt from "./AgentReadReceipt";

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
  viewing. Only prompts this chat really answers or acts on: questions
  it answers from the environment, and "write more scenarios", which
  proposes them for review like every other source. Edits it can't make
  here aren't offered. New tabs fall back to the Overview set.
*/
const CHIPS_BY_TAB = {
  overview: [
    "Summarise what's in this environment",
    "What's still missing before we can run?",
    "Explain the tools and rules to me",
  ],
  agent: [
    "Which agent version does this test?",
    "What's still missing before we can run?",
  ],
  contract: ["Explain the tools and rules to me", "How is a run scored?"],
  scenarios: ["Write 5 more scenarios", "How is a run scored?"],
  evals: ["How is a run scored?"],
};
export default function TemplateReviewLayout({
  env,
  onFinish,
  onBack,
  isTwin,
  initialTurns,
}) {
  const navigate = useNavigate();
  const { envState, patch } = useEnvState(env.id);
  const scenarioCount = envState?.scenarios?.length || 0;
  /* Same tool-fit ask every other Run button makes. */
  const { check: checkRunFit, dialog: runFitDialog } = useRunFitCheck(
    env,
    envState,
    patch,
  );

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
  const [running, setRunning] = useState(false);
  const [activeTab, setActiveTab] = useState("overview");
  const [done, setDone] = useState(() => (seeded ? STAGE_ORDER.slice() : []));
  const timers = useRef([]);
  /*
    Show the AgentReadReceipt FIRST — the read-audit is the entry
    point, not a checkpoint mid-derivation. The receipt reads the
    template statically (no streaming needed — the template already
    declares its tools/rules/data), the user confirms, and only then
    do the derivation stages start streaming. Seeded flows carry
    prior state and skip the receipt entirely.
  */
  /* Built from a description: there's no agent to read, so the build starts
     from what the person wrote — no read receipt. */
  const fromScratch = env?.builtFrom?.kind === "scratch";
  const scratchText = env?.builtFrom?.intake?.flow || "";
  const [awaitReadAck, setAwaitReadAck] = useState(!seeded && !fromScratch);
  const [readAnswers, setReadAnswers] = useState(null);

  /*
    Stream the derivation stages on mount — same shape BuildFromAgent
    uses so the review screen carries the same "we're standing this up"
    animation before it settles. Each step lands 380ms after the last;
    stages chain automatically, and once every stage is done the panel
    on the right unfreezes and Finish setup goes live.
  */
  /*
    Kick off the streaming derivation. Not called on mount any more —
    the AgentReadReceipt gates it. When the user clicks Build the
    world, we call this to stream every stage in sequence.
  */
  const startDerivation = () => {
    /* The world's real tools travel with the source, so the narration counts
       what this environment has — not the demo agent's twelve. */
    const source = narrationSource();
    const play = (stageId, delay = 0) => {
      const stage = builderRun(stageId, source);
      const turnId = `t-${stageId}-${Date.now()}`;
      timers.current.push(
        setTimeout(() => {
          setTurns((prev) => [
            ...prev,
            { id: turnId, role: "builder", title: stage.title, steps: [] },
          ]);
        }, delay),
      );
      stage.steps.forEach((step, i) => {
        timers.current.push(
          setTimeout(
            () => {
              setTurns((prev) =>
                prev.map((t) =>
                  t.id === turnId ? { ...t, steps: [...t.steps, step] } : t,
                ),
              );
              if (i !== stage.steps.length - 1) return;
              setDone((d) => (d.includes(stageId) ? d : [...d, stageId]));
              const nextId = STAGE_ORDER[STAGE_ORDER.indexOf(stageId) + 1];
              if (nextId) play(nextId, 500);
              else setRunning(false);
            },
            delay + 380 * (i + 1),
          ),
        );
      });
    };
    setRunning(true);
    setTurns([
      ...(fromScratch && scratchText
        ? [{ id: "u-intake", role: "user", text: scratchText }]
        : []),
      {
        id: "a-init",
        role: "assistant",
        steps: [
          {
            kind: "note",
            text: fromScratch
              ? `Building ${env.name} from your description — ${scenarioCount} scenario${scenarioCount === 1 ? "" : "s"} and ${(env.rules || []).length} rule${(env.rules || []).length === 1 ? "" : "s"} to start. No agent yet: connect it when it's ready, and runs start once it is.`
              : `Building ${env.name} — ${scenarioCount} scenarios, ${(env.tools || []).length} tools, ${(env.rules || []).length} rules.`,
          },
        ],
      },
    ]);
    play("understand", 600);
  };

  /* A scratch build starts as soon as the page opens — once, even when
     React mounts the effect twice. */
  const startedRef = useRef(false);
  useEffect(() => {
    if (!fromScratch || seeded || startedRef.current) return;
    startedRef.current = true;
    startDerivation();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
    };
  }, []);

  /*
    Suggested-message chips change with the tab the user is looking
    at. Reading the tabs' content and firing a "add X" chip against
    the wrong tab was the old failure mode — asking to "drop payment
    scenarios" while sitting on the Contract tab made the assistant
    feel context-blind. Now every tab carries its own short list of
    prompts that make sense for what's on-screen.
  */
  const chips = useMemo(
    () => CHIPS_BY_TAB[activeTab] || CHIPS_BY_TAB.overview,
    [activeTab],
  );

  /* What the build narration reads: this world's own tools, the agent it
     belongs to (so the narration says sealed or live honestly), and the
     distinct sub-goals its scenarios use. */
  const narrationSource = () =>
    fromScratch
      ? {
          kind: "scratch",
          rules: (env.rules || []).length,
          scenarios: (envState?.scenarios || []).length,
          surface: env.surface,
        }
      : {
          kind: "template",
          value: env.name,
          templateId: env.id,
          tools: env.tools || [],
          connectedAgent: envState?.agent || null,
          subGoalCount: subGoalCatalogOf(env, {
            ...envState,
            scenarios: (envState?.scenarios || []).filter((r) => !r.proposed),
          }).length,
        };

  /*
    The chat never claims a change it didn't make. Asking for more scenarios
    proposes them (reviewed and added on the Scenarios tab, like every other
    source); questions are answered from the environment itself; anything
    else says plainly it can't be done here, and where it can.
  */
  const reply = (trimmed) => {
    const intent = detectAddScenariosIntent(trimmed);
    if (intent) {
      const existing = new Set((envState?.scenarios || []).map((r) => r.id));
      const fresh = scenarioPoolFor(env, envState)
        .filter((r) => !existing.has(r.id))
        .slice(0, intent.count);
      if (!fresh.length)
        return "Every scenario this world can write is already here — remove a few on the Scenarios tab first.";
      const batch = stampNewBatch(
        fresh.map((r) => ({ ...r, chatAsk: trimmed })),
        { env, envState, source: "chat", actor: currentUser() },
      );
      if (batch.rows.length)
        patch({
          scenarios: [
            ...(envState?.scenarios || []),
            ...batch.rows.map((r) => asProposal(r, "chat")),
          ],
        });
      const warn = [unanswerableNote(batch), rejectedNote(batch)]
        .filter(Boolean)
        .join(" ");
      return `${batch.rows.length} proposed — review and add them on the Scenarios tab.${fresh.length < intent.count ? ` Only ${fresh.length} new ones could be written.` : ""}${warn ? ` ${warn}` : ""}`;
    }
    return answerFromEnvironment(trimmed, env, envState);
  };

  const send = (text) => {
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    setTurns((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", text: trimmed },
    ]);
    const answer = reply(trimmed);
    setTurns((prev) => [
      ...prev,
      {
        id: `a-${Date.now()}`,
        role: "assistant",
        steps: [{ kind: "note", text: answer }],
      },
    ]);
  };

  /*
    DerivedPanels reads `done` (which stages have completed) and
    `running` (whether the builder is mid-stage). The streaming effect
    above advances both as each stage settles, so tabs light up
    progressively instead of appearing pre-built.
  */
  const source = useMemo(
    () => ({ kind: "template", templateId: env.id }),
    [env.id],
  );

  /*
    Nikhil's ask: users need to be able to refresh the environment
    (re-read the source, rebuild) — but it's not the centrepiece.
    A small ghost button in the header replays the same streaming
    derivation that ran on mount: clear turns/done, kick the
    stream. Nothing destructive to envState — the derivation
    writes over the derived fields on completion.
  */
  const refreshFromSource = () => {
    if (running) return;
    /* Cancel any pending stream timers before restarting so we don't
       stack two streams on top of each other. */
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setDone([]);
    setRunning(true);
    setTurns([
      {
        id: `a-refresh-${Date.now()}`,
        role: "assistant",
        steps: [
          {
            kind: "note",
            text: `Re-reading the ${env.name} source and rebuilding tools, rules and scenarios.`,
          },
        ],
      },
    ]);
    const play = (stageId, delay = 0) => {
      const stage = builderRun(stageId, narrationSource());
      const turnId = `t-${stageId}-${Date.now()}`;
      timers.current.push(
        setTimeout(() => {
          setTurns((prev) => [
            ...prev,
            { id: turnId, role: "builder", title: stage.title, steps: [] },
          ]);
        }, delay),
      );
      stage.steps.forEach((step, i) => {
        timers.current.push(
          setTimeout(
            () => {
              setTurns((prev) =>
                prev.map((t) =>
                  t.id === turnId ? { ...t, steps: [...t.steps, step] } : t,
                ),
              );
              if (i !== stage.steps.length - 1) return;
              setDone((d) => (d.includes(stageId) ? d : [...d, stageId]));
              const nextId = STAGE_ORDER[STAGE_ORDER.indexOf(stageId) + 1];
              if (nextId) play(nextId, 500);
              else setRunning(false);
            },
            delay + 380 * (i + 1),
          ),
        );
      });
    };
    play("understand", 400);
  };

  /*
    While the read-receipt is up, the review layout underneath is
    still mid-derivation — we render only the receipt so the user
    focuses on the audit + questions before the world builds.
    Clicking Build (or Back) exits the receipt and resumes streaming.
  */
  if (awaitReadAck) {
    return (
      <AgentReadReceipt
        agentRef={env?.id || "template"}
        reading={buildTemplateReading(env)}
        questions={buildTemplateQuestions(env)}
        onBack={onBack}
        onBuild={(answers) => {
          setReadAnswers(answers);
          setAwaitReadAck(false);
          startDerivation();
        }}
      />
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 3,
          py: 1.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Tooltip arrow title="Back to environments">
          <IconButton size="small" onClick={onBack}>
            <Iconify icon="solar:alt-arrow-left-linear" width={16} />
          </IconButton>
        </Tooltip>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 600 }}>
              {env.name}
            </Typography>
            {/*
              Env status pill sits inside the identity cluster —
              parallel to the workspace header — so the reader sees
              one grouping (name · status) that describes the same
              thing everywhere. `Building` (purple, pulsing) while
              derivation streams; `Ready` (green, solid) once the
              world has landed.
            */}
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{
                px: 1.25,
                py: 0.5,
                borderRadius: 1,
                border: "1px solid",
                borderColor: running
                  ? "status.discovery.border"
                  : "status.success.border",
                bgcolor: running ? "status.discovery.bg" : "status.success.bg",
                color: running ? "status.discovery.fg" : "status.success.fg",
                flexShrink: 0,
              }}
            >
              <Box
                sx={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  bgcolor: running
                    ? "status.discovery.solid"
                    : "status.success.solid",
                  animation: running
                    ? "pulse 1.4s ease-in-out infinite"
                    : undefined,
                  "@keyframes pulse": {
                    "0%,100%": { opacity: 0.4 },
                    "50%": { opacity: 1 },
                  },
                }}
              />
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {running ? "Building" : "Ready"}
              </Typography>
            </Stack>
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            {envState?.agent?.name
              ? `${envState.agent.name}'s environment — review it, then run it`
              : "Review the environment, then run it"}
          </Typography>
        </Box>
        {/*
          The world is standing by the time we hit the review layout, so
          the CTA reads "Run simulation" on every flow (template, twin,
          scratch). It runs only when the same readiness every Run button
          checks is met (agent, where it runs, scenarios, evaluations) —
          otherwise it opens the workspace on the tab with the gap rather
          than running. Disabled while the builder is still streaming.
        */}
        {/*
          Refresh — de-emphasized (ghost outline, subtitle-tinted) so
          it lives next to the primary CTA without competing with it.
          Nikhil's transcript: refresh exists, but it's not the
          centrepiece.
        */}
        <Tooltip
          arrow
          title="Re-read the source and rebuild tools, rules and scenarios."
        >
          <Box component="span">
            <Button
              variant="outlined"
              size="small"
              onClick={refreshFromSource}
              disabled={running}
              startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
              }}
            >
              Refresh
            </Button>
          </Box>
        </Tooltip>
        {(() => {
          const readiness = runReadiness(envState);
          /* Built from a description: no agent yet, so nothing to run — Connect agent on Overview first. */
          const noAgent = !envState?.agent;
          const reason = running
            ? "Building the environment — one moment…"
            : noAgent
              ? "Connect an agent first — Connect agent, on Overview. Runs start once one is connected."
              : readiness.ready
                ? ""
                : `${readiness.reason}. Opens the workspace there.`;
          const go = () =>
            readiness.ready
              ? checkRunFit(() => onFinish?.())
              : navigate(
                  paths.dashboard.simulate.environmentStep(
                    env.id,
                    readiness.step,
                  ),
                );
          return (
            <Tooltip arrow title={reason}>
              <Box component="span">
                <Button
                  variant="contained"
                  color="primary"
                  onClick={go}
                  disabled={running || noAgent}
                  startIcon={
                    <Iconify icon="solar:play-circle-linear" width={16} />
                  }
                >
                  Run simulation
                </Button>
              </Box>
            </Tooltip>
          );
        })()}
      </Stack>

      {/* Builder console (left) + tabbed panels (right) — one surface split
          by a single rule, the same as the workspace. */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: { xs: "1fr", lg: "minmax(340px, 400px) 1fr" },
        }}
      >
        <Box
          sx={{
            height: "100%",
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            /* Widths and colour set separately: a responsive "1px solid"
             shorthand resets the colour to the text colour. */
            borderStyle: "solid",
            borderWidth: 0,
            borderColor: "divider",
            borderRightWidth: { xs: 0, lg: 1 },
            borderBottomWidth: { xs: 1, lg: 0 },
          }}
        >
          <AssistantConsole
            turns={turns}
            running={running}
            chips={chips}
            onSend={send}
            onChip={(c) => send(c)}
            frozen={env?.buildStatus === "building"}
            frozenReason="Environment is still being built"
          />
        </Box>

        <Box
          sx={{
            height: "100%",
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            px: 2.5,
            bgcolor: "background.paper",
          }}
        >
          <DerivedPanels
            env={env}
            envState={envState}
            patch={patch}
            source={source}
            done={done}
            running={running}
            onTabChange={setActiveTab}
          />
        </Box>
      </Box>
      {runFitDialog}
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

/*
  Shape env into the payload the AgentReadReceipt renders. Template
  builds don't have real per-fact provenance (the template is just a
  fixture); we assign plausible origins so the receipt reads as a
  real audit — config for the first few tools, call-graph for the
  rest, policy.yaml for rules, fixture for the seed tables, prompt
  for behavior, one inferred item to demonstrate the ambiguity path.
*/
function buildTemplateReading(env) {
  if (!env) return { tools: [], rules: [], data: [], behavior: [] };
  const tools = (env.tools || []).map((t, i) => ({
    name: t.name,
    origin: i < 4 ? "config" : "callGraph",
  }));
  const rules = (env.rules || []).map((r) => ({
    name: r.length > 42 ? `${r.slice(0, 42)}…` : r,
    origin: "policy",
  }));
  const data = (env.seed?.tables || []).slice(0, 4).map((t) => ({
    name: `${t.name}.csv`,
    note: `${t.rows.toLocaleString()} rows`,
    origin: "fixture",
  }));
  const branchCount = Math.max(3, (env.tools || []).length - 1);
  const behavior = [
    {
      name: "routing prompt",
      note: `${branchCount} branches`,
      origin: "prompt",
    },
    { name: "transfer → front desk", origin: "prompt" },
    { name: "escalate on 2× refusal", origin: "inferred" },
  ];
  return { tools, rules, data, behavior };
}

function buildTemplateQuestions(env) {
  if (!env) return [];
  const lastTool = (env.tools || [])[Math.max(0, (env.tools || []).length - 1)];
  const questions = [];
  if (lastTool) {
    questions.push({
      id: "tool-side-effects",
      title: `Does ${lastTool.name} change data?`,
      why: "It's called from your code but never described in the prompt. Your answer decides whether a scenario may trigger real side-effects.",
      kind: "choice",
      options: [
        { id: "read", label: "Read-only" },
        { id: "write", label: "Writes to state" },
        { id: "escalate", label: "Escalates externally" },
      ],
    });
  }
  questions.push({
    id: "policy-enforcement",
    title:
      "Are the policy values enforced in your backend, or only stated in the prompt?",
    why: "We can see the values, not where they're enforced. A rule we only infer is graded more softly than a hard one.",
    kind: "boolean",
  });
  /* Cap at 2 — three questions was starting to feel like a form. Two is
     the smallest number where the audit still names distinct ambiguities
     and the user is out the other side quickly. */
  return questions.slice(0, 2);
}

/* Answers this chat gives from the environment itself — and, for anything
   it can't change, where the change is made instead. Never a made-up edit. */
function answerFromEnvironment(text, env, envState) {
  const t = text.toLowerCase();
  const tools = env?.tools || [];
  const rules = env?.rules || [];
  const scenarios = (envState?.scenarios || []).filter(
    (r) => !r.proposed && !r.retired && !r.archived,
  );
  const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;
  const asksQuestion =
    /\?$|^(what|which|how|why|explain|summari[sz]e|show|list|tell)\b/.test(t);
  if (asksQuestion) {
    if (/missing|before we can run|ready/.test(t)) {
      const r = runReadiness(envState, env);
      return r.ready ? "Nothing — it's ready to run." : `${r.reason}.`;
    }
    if (/version/.test(t)) {
      const v = currentAgentVersion(envState)?.label;
      return envState?.agent
        ? `It tests ${envState.agent.name || "the agent"}${v ? ` ${v}` : ""}${envState.agentVersionPinned ? ", pinned" : ", following the version in production"}. Versions are changed on the agent's page.`
        : "No agent is attached to this environment.";
    }
    if (/scor|grad|pass|fail/.test(t)) {
      return "Each scenario passes when every critical sub-goal holds; supporting ones are reported. Evaluations score each conversation, and one you mark required fails a conversation that misses it.";
    }
    if (/tool|rule|contract/.test(t)) {
      return `${plural(tools.length, "tool")}: ${tools.map((x) => x.name).join(", ") || "none"}. ${plural(rules.length, "rule")}: ${rules.join(" · ") || "none"}.`;
    }
    if (/summar|what'?s in|overview/.test(t)) {
      return `${plural(scenarios.length, "scenario")}, ${plural(tools.length, "tool")}, ${plural(rules.length, "rule")} and ${plural((envState?.evals || []).length, "evaluation")}${envState?.agent?.name ? `, testing ${envState.agent.name}` : ""}.`;
    }
  }
  const where = /eval|grader|quality bar/.test(t)
    ? "the Evaluations tab"
    : /rule|tool|actor|contract/.test(t)
      ? "the Contract tab"
      : /version|roll ?back/.test(t)
        ? "the agent's page"
        : /scenario|persona|case/.test(t)
          ? "the Scenarios tab"
          : "the tabs on the right";
  return `I can't make that change from this chat. You can do it on ${where} — or ask me to write more scenarios, which I'll propose for review.`;
}
