import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Box, Button, Stack, Typography } from "@mui/material";
import { paths } from "src/routes/paths";
import { useSimStore, useConnectedAgents } from "../store";
import { mainEnvOf, agentStatusOf } from "../_mock/agentLifecycle";
import {
  agentDefinitionOf,
  agentIdOf,
  agentSourceOf,
  needsFoundCheck,
} from "../_mock/agentRegistry";
import { foundValuesOf, isCodeAgent } from "../agents/foundFacts";
import { TextLink } from "../agents/connect/connectBits";
import { connectedAgentPath } from "./improvementNav";
import {
  baseRunOf,
  buildImprovement,
  todaySpend,
} from "../_mock/improvementStart";
import { productionAgentVersion } from "../_mock/versions";
import { isComingSoon } from "../_mock/rsiModel";
import ImprovementSetup from "../run/fixmyagent/ImprovementSetup";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import { buildPresetFor } from "../agents/agentViewUtils";
import SideDrawer from "../components/SideDrawer";
import AgentPicker from "./AgentPicker";
import { LogoTile } from "../environments/LandingBits";
import { creditsLabel } from "../_mock/metering";
import {
  FIX_DEFAULTS,
  MAX_CANDIDATES,
  hoursLabel,
} from "../_mock/improvementStart";
import { agentPickerChoice, agentPickerHelper } from "./agentPickerChoices";
import { improvementPath } from "./improvementNav";
import { runningTwinOf } from "../_mock/optimizationRuns";
import { useSnackbar } from "notistack";

/*
  New improvement (RSI PRD, Flow 4): pick the agent, then the same setup a
  run's Debug failures opens — on the agent's main environment, its other
  environments optional, aimed at a goal. Agents we can only call start
  here too: they rarely send anything to the Error Feed. An agent with no
  environment is listed too: Build environment hands over to the normal
  build, which ends on Run simulation — that first run shows where the agent
  fails, and the person improves from what it finds.
*/
export default function NewImprovement({
  open,
  onClose,
  initialAgentId = null,
}) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  /* commit: it may be opened outside Improvements, and navigates there. */
  const { state, commit } = useSimStore();
  const { agents } = useConnectedAgents();
  const [picked, setPicked] = useState(null);
  const [buildOpen, setBuildOpen] = useState(false);

  /* Agents that can be improved: real (not a template's stand-in) and a type
     improvements run on. One with no environment yet is listed too. */
  const choices = useMemo(
    () =>
      agents
        .filter(
          (a) =>
            !a.placeholder &&
            !isComingSoon(a) &&
            !isComingSoon(mainEnvOf(a, state)?.env),
        )
        .map((a) => ({
          agent: a,
          main: mainEnvOf(a, state),
          status: agentStatusOf(a),
        })),
    [agents, state],
  );

  /* Back from Connect a new agent: the agent it just connected, picked. */
  /* Agents may still be loading when it opens, so it waits for them, once. */
  const applied = useRef(null);
  useEffect(() => {
    if (!open || !initialAgentId || applied.current === initialAgentId) return;
    const a = agents.find((x) => agentIdOf(x.key) === initialAgentId);
    if (!a) return;
    applied.current = initialAgentId;
    setPicked(a);
  }, [open, initialAgentId, agents]);
  /* Agents → New agent, on one source or the full list, and back here with it picked. */
  const connectNew = (source = null) => {
    const q = new URLSearchParams({
      return: `${paths.dashboard.simulate.improvements}?new=1&agent={agent}`,
      for: "improve",
    });
    if (source) {
      q.set("src", source.kind);
      q.set("item", source.id);
      if (source.initial) q.set("init", JSON.stringify(source.initial));
      q.set("name", source.name);
      if (source.logo) q.set("logo", source.logo);
    }
    onClose();
    navigate(`${paths.dashboard.agents}/connect?${q.toString()}`);
  };

  const close = () => {
    setPicked(null);
    onClose();
  };
  const main = picked ? mainEnvOf(picked, state) : null;
  const env = main?.env || null;
  /* Picked, but nowhere to score it yet. */
  const needsEnv = !!picked && !env;

  const start = ({
    name,
    model,
    writerModel,
    envIds,
    goal,
    budget,
    limits,
    seed,
    lineage,
    reread,
    textSearch,
    optimizerId,
    base,
    today,
    depth,
    pick,
  }) => {
    const es = state.byEnv[env.id];
    /* Measuring today is a real run of the environment, recorded first. */
    if (today) commit({ type: "recordRun", envId: env.id, run: today });
    const from =
      base ||
      baseRunOf(env, es, productionAgentVersion(es)?.label) ||
      baseRunOf(env, es);
    if (!from?.tasks) return;
    /* The same improvement already running: open it — nothing new starts. */
    const twin = !today
      ? runningTwinOf(es, {
          fromRunId: from.id,
          baseAgentVersion: from.agentVersion || null,
          goalId: goal?.id,
        })
      : null;
    if (twin) {
      close();
      enqueueSnackbar(
        `${twin.name} is already running on this — opened it. Nothing new started.`,
        { variant: "info" },
      );
      navigate(improvementPath(env.id, twin.id));
      return;
    }
    const record = buildImprovement({
      env,
      es,
      tasks: from.tasks,
      seed,
      state,
      reread,
      textSearch,
      optimizerId,
      pick,
      fromRunId: from.id,
      baseAgentVersion: from.agentVersion || null,
      trigger: {
        kind: "goal",
        goal: goal
          ? {
              id: goal.id,
              label: goal.label,
              ...(goal.group && { group: goal.group }),
              ...(goal.reliability && { reliability: goal.reliability }),
            }
          : undefined,
      },
      limits,
      budget,
      regressionEnvIds: envIds,
      lineage,
      /* Measuring today is spent before the search: inside the budget and the Cost. */
      spentFirst: today ? todaySpend(today, env.surface) : 0,
      extra: {
        ...(name && { name }),
        ...(model && { model }),
        ...(writerModel && { writerModel }),
        ...(depth && { fixDepth: depth, suggestChosen: true }),
      },
    });
    commit({
      type: "patchEnvState",
      envId: env.id,
      patch: { optimizations: [...(es.optimizations || []), record] },
    });
    close();
    navigate(improvementPath(env.id, record.id));
  };

  /*
    One drawer, one form: the agent first, and everything else appears below
    it once it's picked. The picker is the dashboards' filter-attribute one —
    search, agent types and sources on the left, a row per agent — and the
    main environment is said under the field once one is picked.
  */
  const pickerChoices = useMemo(
    () =>
      choices
        .map((c) => agentPickerChoice(c.agent, c.main?.env, c.status))
        .sort(
          (a, b) =>
            Number(a.off) - Number(b.off) || a.name.localeCompare(b.name),
        ),
    [choices],
  );
  const pickedChoice = pickerChoices.find((c) => c.key === picked?.key) || null;
  /* Why nobody is listed: no agents, or none of a type improvements run on yet. */
  const emptyText = useMemo(() => {
    const real = agents.filter((a) => !a.placeholder);
    if (!real.length)
      return "No agents connected yet — connect one from Agents first.";
    return "None of your agents can be improved yet — improvements don't run on their type yet.";
  }, [agents]);
  const agentField = (
    <Box>
      {/* Picked one, then changed your mind: another agent is a pick away, a new one this link. */}
      <Stack
        direction="row"
        alignItems="baseline"
        justifyContent="space-between"
        sx={{ mb: 0.5 }}
      >
        <Typography sx={{ typography: "s3", fontWeight: 600 }}>
          Agent
        </Typography>
        {pickedChoice && (
          <TextLink
            onClick={() => connectNew()}
            sx={{
              color: "text.secondary",
              "&:hover": { color: "text.primary", textDecoration: "underline" },
            }}
          >
            Connect a new agent
          </TextLink>
        )}
      </Stack>
      <AgentPicker
        choices={pickerChoices}
        emptyText={emptyText}
        value={picked?.key || null}
        onChange={(key) =>
          setPicked(choices.find((c) => c.agent.key === key)?.agent || null)
        }
        emptyAction={{
          label: "Connect a new agent",
          onClick: () => connectNew(),
        }}
      />
      {!pickedChoice && (
        <>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
          >
            Its goals, budget and cost show here once it&apos;s picked.
          </Typography>
          <NotConnectedYet onConnect={connectNew} />
        </>
      )}
      {pickedChoice && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
          {needsEnv
            ? `${picked.name} has no environment yet — an improvement is scored in one, so it's built first.`
            : agentPickerHelper(pickedChoice, main?.envName)}
        </Typography>
      )}
      {needsEnv && (
        <AgentReadPanel
          agent={picked}
          onCheck={() => {
            close();
            navigate(`${connectedAgentPath(agentIdOf(picked.key))}?setup=1`);
          }}
        />
      )}
    </Box>
  );

  return (
    <SideDrawer open={open} onClose={close} width={{ xs: "100%", sm: 640 }}>
      <ImprovementSetup
        bare
        open={open}
        onClose={close}
        agentField={agentField}
        preset={
          picked && env
            ? { agentKey: picked.key, envId: env.id, fromGoal: true }
            : { ...(picked && { agentKey: picked.key }), fromGoal: true }
        }
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
              /* Built to improve: the build's last word and the first run's
                 page lead back to it. */
              state: {
                presetSource: buildPresetFor(picked, choice),
                toImprove: true,
              },
            });
          }}
        />
      )}
    </SideDrawer>
  );
}
/*
  Picked, with no environment yet — usually one just connected: what we
  read from it (key-value, as the agent's Overview says it) and what
  happens next, so the drawer isn't a blank wait before Build environment.
*/
function AgentReadPanel({ agent, onCheck }) {
  const source = agentSourceOf(agent);
  const code = isCodeAgent(agent);
  const found = code ? foundValuesOf(agent) : null;
  const def = code ? null : agentDefinitionOf({ agent });
  const tools = code
    ? found.tools
    : (def?.tools?.items || []).map((t) => t.name);
  const list = (xs, n = 3) =>
    `${xs.slice(0, n).join(", ")}${xs.length > n ? ` +${xs.length - n}` : ""}`;
  const rows = [
    /* Where it lives, unless that's just the entry point said again. */
    {
      k: "Source",
      v: [source.label, source.where !== found?.entry && source.where]
        .filter(Boolean)
        .join(" · "),
    },
    ...(code
      ? [
          { k: "Entry point", v: found.entry, mono: true },
          {
            k: "Prompts",
            v: found.prompts.length
              ? `${found.prompts.length} · ${list(found.prompts, 2)}`
              : "",
            mono: true,
          },
        ]
      : []),
    { k: "Tools", v: tools.length ? `${tools.length} · ${list(tools)}` : "" },
    {
      k: "Model",
      v: code ? found.model : def?.model?.visible ? def.model.name : "",
      mono: true,
    },
    ...(code ? [{ k: "How it runs", v: found.runCommand, mono: true }] : []),
  ].filter((r) => r.v);
  const steps = [
    "Build its environment from what we read — simulated tools, seeded data, rules and scenarios. A few minutes.",
    "Run it once, to see where it fails.",
    "Improve from what that run finds — up to 4 trials, proven on held-back scenarios.",
  ];
  return (
    <Stack spacing={3} sx={{ mt: 2.5 }}>
      <Box>
        <Typography
          sx={{ typography: "m3", fontWeight: 500, mb: 1 }}
        >{`What we read from ${agent.name}`}</Typography>
        <Box
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
        >
          {rows.map((r, i) => (
            <Stack
              key={r.k}
              direction="row"
              spacing={1.5}
              sx={{
                px: 1.5,
                py: 0.875,
                ...(i > 0 && {
                  borderTop: "1px solid",
                  borderColor: "divider",
                }),
              }}
            >
              <Typography
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  width: 96,
                  flexShrink: 0,
                }}
              >
                {r.k}
              </Typography>
              <Typography
                sx={{
                  typography: "s1",
                  minWidth: 0,
                  wordBreak: "break-word",
                  ...(r.mono && { fontFamily: "monospace" }),
                }}
              >
                {r.v}
              </Typography>
            </Stack>
          ))}
        </Box>
        {/* Optional: runs and fixes go ahead on what we read. */}
        {needsFoundCheck(agent) && (
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
          >
            Read automatically — optional to check or correct.{" "}
            <TextLink
              onClick={onCheck}
              sx={{
                color: "text.primary",
                textDecoration: "underline",
                textUnderlineOffset: "3px",
              }}
            >
              Check what we found
            </TextLink>
          </Typography>
        )}
      </Box>
      <Box>
        <Typography sx={{ typography: "m3", fontWeight: 500, mb: 1 }}>
          What happens next
        </Typography>
        <Stack spacing={1}>
          {steps.map((t, i) => (
            <Stack key={t} direction="row" spacing={1.25}>
              <Typography
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  width: 12,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {i + 1}
              </Typography>
              <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                {t}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>
    </Stack>
  );
}
AgentReadPanel.propTypes = {
  agent: PropTypes.object.isRequired,
  onCheck: PropTypes.func.isRequired,
};

/*
  Before an agent is picked: the other way in (an agent not connected yet,
  on the sources a fix can change first) and what an improvement does, in
  three plain steps — the empty-state pattern, no illustration.
*/
const SOURCES = [
  {
    id: "github",
    kind: "repo",
    name: "GitHub",
    logo: "github",
    initial: { host: "github" },
    note: "Changes come back as a pull request",
  },
  {
    id: "upload",
    kind: "upload",
    name: "Upload a folder",
    icon: "solar:upload-minimalistic-linear",
    note: "Changes come back as a patch to apply",
  },
  {
    id: null,
    name: "Another way",
    icon: "solar:widget-add-linear",
    note: "Vapi, Retell, an endpoint or a phone number — ends in a suggested change",
  },
];
function NotConnectedYet({ onConnect }) {
  const steps = [
    "Runs the agent's current version on its environment's scenarios, to see what fails.",
    `Tries up to ${MAX_CANDIDATES} changes on the failing ones, within ${creditsLabel(FIX_DEFAULTS.dollars)} or ${hoursLabel(FIX_DEFAULTS.minutes)} by default.`,
    "Proves the best on held-back and must-pass scenarios. A winner comes back as a new version; you decide whether to make it the default.",
  ];
  return (
    <Stack spacing={3} sx={{ mt: 3 }}>
      <Box>
        <Typography sx={{ typography: "m3", fontWeight: 500 }}>
          Or connect a new agent
        </Typography>
        <Typography sx={{ typography: "s1", color: "text.tertiary", mb: 1 }}>
          Not connected yet? Bring it in — you come back here with it picked,
          and its environment is built before the improvement starts.
        </Typography>
        <Box
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
        >
          {SOURCES.map((src, i) => (
            <Stack
              key={src.name}
              direction="row"
              alignItems="center"
              spacing={1.25}
              sx={{
                px: 1.5,
                py: 1,
                ...(i > 0 && {
                  borderTop: "1px solid",
                  borderColor: "divider",
                }),
              }}
            >
              <LogoTile logo={src.logo} icon={src.icon} size={24} />
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                  {src.name}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {src.note}
                </Typography>
              </Box>
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                onClick={() => onConnect(src.id ? src : null)}
                sx={{ borderColor: "divider", flexShrink: 0 }}
              >
                Connect
              </Button>
            </Stack>
          ))}
        </Box>
      </Box>
      <Box>
        <Typography sx={{ typography: "m3", fontWeight: 500, mb: 1 }}>
          How an improvement runs
        </Typography>
        <Stack spacing={1}>
          {steps.map((t, i) => (
            <Stack key={t} direction="row" spacing={1.25}>
              <Typography
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  width: 12,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {i + 1}
              </Typography>
              <Typography sx={{ typography: "s1", color: "text.secondary" }}>
                {t}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>
    </Stack>
  );
}
NotConnectedYet.propTypes = { onConnect: PropTypes.func.isRequired };

NewImprovement.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  /* An agent id to open with (agt_…), e.g. one just connected. */
  initialAgentId: PropTypes.string,
};
