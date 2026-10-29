import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  InputBase,
  MenuItem,
  Select,
  Stack,
  Switch,
  Typography,
  Tooltip,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { agentDefinitionOf } from "../_mock/agentRegistry";
import { agentVersions, currentAgentVersion } from "../_mock/versions";
import { currentEnvSnapshot } from "../_mock/envSnapshot";
import { DEFAULT_LIMITS, limitRows } from "../_mock/optimizationRuns";
import { useAgentImprovementFlag } from "../_mock/featureFlags";
import BuildEnvironmentDialog from "../agents/BuildEnvironmentDialog";
import { buildPresetFor } from "../agents/agentViewUtils";
import {
  FIX_DEFAULTS,
  budgetsWithin,
  envMustPassOf,
  stopEarlyNote,
} from "../_mock/improvementStart";
import { limitsOf } from "../agents/agentLimits";
import { projectLinkOf } from "../_mock/projectLinks";
import {
  targetableMembers,
  membersOf,
  isProtectedMember,
  textSearchDefault,
} from "../_mock/improvementPolicy";
import ConnectAgentDialog from "./ConnectAgentDialog";
import { codeDiagnosisOf } from "./codeDiagnosis";
import { clickable, focusRing } from "../components/clickable";
import {
  FIX_IN_FLIGHT,
  FIX_SCENARIO_CAP,
  envsForAgent,
  fixEnvFor,
  fixEstimate,
  fixScenariosOf,
  fixPreview,
  issueFixNav,
  issueProjectLinkOf,
  issueScenarioDrafts,
  productionVersionOf,
  useIssueFix,
} from "./issueFix";
import { creditsForUsd, creditsLabel } from "../_mock/metering";
import useCreditBalance from "../usage/useCreditBalance";
import CostPartsTable from "../improvements/CostPartsTable";
import ImprovementConstraints, {
  ConstraintPicker,
} from "../improvements/ImprovementConstraints";
import { withEdits } from "../improvements/constraintEdits";
import { RATE_PLAN } from "../_mock/rates";
import { useSimStore, useConnectedAgents } from "../store";
import { LiveConsentCard } from "../agents/AgentLiveConsent";

/* The time budgets a fix can take — never more than the agent's own. */
const MINUTES = [15, 30, 45, 60, 90, 120];
const minutesWithin = (ceiling) => {
  const list = MINUTES.filter((m) => m <= ceiling);
  return list.length ? list : [ceiling];
};

const label = {
  typography: "s3",
  fontWeight: 600,
  textTransform: "none",
  letterSpacing: 0.6,
  mb: 0.75,
};

/**
 * Fix — the page that starts an improvement run from an Error Feed issue
 * (RSI PRD, Flow 3). It used to be a side sheet; the plan has enough in it —
 * scenarios to edit, where it's tested, how it searches, the constraints and
 * the budget — to deserve the page (team feedback, Oct 2026).
 *
 * Laid out like the improvement page it leads to: the plan reads top to
 * bottom in the main column, in the order it happens (what we're fixing →
 * the scenarios → where they're tested → how it searches → what it may
 * change), and a summary stays beside it — the agent, the environment, the
 * budget and its estimate — with Start always in view.
 *
 * Everything arrives filled in, and the page holds itself to what the Fix
 * button checks, so every way in (the button, Prove it before it ships, Fix
 * again on a reopened issue) follows the same rules: a fix already in flight
 * opens that fix, an issue no agent is linked to connects one, and Start
 * waits for the agent's gate (not archived, disconnected or Coming soon,
 * its code read confirmed), live-test consent, a built environment and at
 * least one scenario picked. An issue the feed hasn't confirmed is noted,
 * not held: the fix reproduces it first.
 */
/* Preparing a fix plan takes a moment: the issue's traces are read, the
   agent and its production version found, where to test it settled, the
   scenarios written from the traces (RSI PRD: "the issue's conversations,
   turned into scenarios with an inferred persona and a sub-goal for the
   failing step") and the agent's limits applied. The fix itself — the
   candidate changes — is drafted after Start, in the search. Done once per
   issue in a session: reopening Fix shows the plan at once. */
const PREPARED = new Set();
const PREP_STEPS = [
  { id: "issue", label: "Reading the issue", ms: 700 },
  {
    id: "agent",
    label: "Finding the agent and its version in production",
    ms: 600,
  },
  { id: "env", label: "Finding the agent's environment", ms: 600 },
  {
    id: "diagnosis",
    label: "Diagnosing it in the agent's prompt and tools",
    ms: 900,
  },
  { id: "scenarios", label: "Writing scenarios from the traces", ms: 1100 },
  { id: "limits", label: "Applying the agent's limits", ms: 500 },
];

export default function FixPage({ onClose, issue, rootCause = null }) {
  /* A page is open while it's shown — the sheet's effects key off this. */
  const open = true;
  const navigate = useNavigate();
  const improving = useAgentImprovementFlag();
  const { state, agent, agents, project, gate, start, evidence, status } =
    useIssueFix(issue);
  /* A fix runs in one environment (RSI PRD: "Targets come from one
     environment; the others add their must-pass sets"): the one picked for
     this fix (`?env=`, from the Fix popup), else the best fit for the
     issue's channel — the agent's main one when it fits. Picking never
     changes the agent's main environment. */
  const [params] = useSearchParams();
  const envs = useMemo(() => envsForAgent(agent, state), [agent, state]);
  const main =
    envs.find((e) => e.id === params.get("env")) ||
    fixEnvFor(envs, issue) ||
    null;
  const others = envs.filter((e) => e.id !== main?.id);
  const [alsoIn, setAlsoIn] = useState([]);
  /* Search across the lineage: changes earlier versions made, tried again. */
  const [acrossLineage, setAcrossLineage] = useState(false);
  /* The same must-pass rule the search gates on: passes every repeat today. */
  const mustPassIn = (e) => envMustPassOf(e, state.byEnv[e.id]).gated.length;
  const inFlight = !!agent && !!status && FIX_IN_FLIGHT.has(status.key);
  const [connecting, setConnecting] = useState(false);
  const mainState = main ? state.byEnv[main.id] : null;
  const [target, setTarget] = useState("");
  const [textSearch, setTextSearch] = useState(null);
  const [buildOpen, setBuildOpen] = useState(false);
  /* Live-test consent is asked here, where it's needed — not a dead end on
     the button. Nothing runs (the baseline, the fix) until it's given, and
     once given it stays on screen as "Given just now". */
  const { setAgentValues } = useConnectedAgents();
  const consentNeeded = gate?.key === "consent";
  /* "Check what we found" doesn't hold a fix back — the Fix button's tooltip says it. */
  const hardGate = gate && !consentNeeded && gate.key !== "found" ? gate : null;
  const [askedConsent, setAskedConsent] = useState(false);
  useEffect(() => {
    if (consentNeeded) setAskedConsent(true);
  }, [consentNeeded]);

  /* ── nowhere to start from yet ──
     A fix runs in the agent's main environment, and its first step measures
     today there (RSI PRD: "Measure today … sets the baseline") — so an
     environment is all it needs; nobody runs it by hand first. With none,
     Fix hands over to the normal environment flow — the same build dialog
     and builder the agent page uses — and the environment remembers this
     issue (returnToIssue) to bring the person back here once it's built.
     One way to build an environment, never a second one inside Fix. */
  const { commit } = useSimStore();
  const needsEnv = !!agent && !inFlight && !main;
  /* Still building: the fix starts once it's done. */
  const building = !!agent && !inFlight && main?.buildStatus === "building";
  /* Everything the fix needs is in place — otherwise the sheet still shows
     the whole plan, with the missing piece in Environment and its button. */
  const ready = !!main && !building;
  /* The plan's scenarios, being written: index of the step in progress. */
  const prepSteps = PREP_STEPS;
  const [prep, setPrep] = useState(() =>
    PREPARED.has(issue?.cluster_id) ? prepSteps.length : 0,
  );
  const prepared = prep >= prepSteps.length;
  useEffect(() => {
    if (!open || !issue?.cluster_id) return undefined;
    if (PREPARED.has(issue.cluster_id)) {
      setPrep(prepSteps.length);
      return undefined;
    }
    setPrep(0);
    const timers = [];
    let at = 0;
    prepSteps.forEach((step, i) => {
      at += step.ms;
      timers.push(
        setTimeout(() => {
          if (i === prepSteps.length - 1) PREPARED.add(issue.cluster_id);
          setPrep(i + 1);
        }, at),
      );
    });
    return () => timers.forEach(clearTimeout);
  }, [open, issue?.cluster_id]); // eslint-disable-line react-hooks/exhaustive-deps
  const openBuilding = () => {
    /* Watched from its page; the banner there brings the person back. */
    if (main.returnToIssue !== issue?.cluster_id) {
      commit({
        type: "patchEnvironment",
        envId: main.id,
        patch: {
          returnToIssue: issue?.cluster_id || null,
          returnFrom: "opened",
          returnSince: new Date().toISOString(),
        },
      });
    }
    navigate(paths.dashboard.simulate.environmentDetail(main.id));
  };

  /* Built from the issue's traces — shown even before there's an environment. */
  const drafts = useMemo(
    () => (issue ? issueScenarioDrafts(issue, main, mainState) : []),
    [issue, main, mainState],
  );
  const [picked, setPicked] = useState({});
  /* Search the issue's scenarios once there are many to scan. */
  const [scenarioQuery, setScenarioQuery] = useState("");
  const [titles, setTitles] = useState({});
  const [budget, setBudget] = useState(null);
  const [minutes, setMinutes] = useState(null);
  /* This fix's own constraints, on top of the agent's. */
  const [edits, setEdits] = useState({});
  /* The issue's project — the agent below is the one it was set up with. */
  const projectName = projectLinkOf(project, state, agents).name;

  useEffect(() => {
    if (!open) return;
    /* The most common situations, up to the cap, are in by default. */
    setPicked(
      Object.fromEntries(fixScenariosOf(drafts).used.map((d) => [d.id, true])),
    );
    setScenarioQuery("");
    setTitles({});
    setBudget(null);
    setMinutes(null);
    setEdits({});
    setAlsoIn([]);
    setAcrossLineage(false);
  }, [open, drafts, main]);
  useEffect(() => {
    if (!open) return;
    setTarget("");
    setTextSearch(null);
    setAskedConsent(consentNeeded);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  /* A voice agent searches in text by default; finalists run as real calls. */
  const inText =
    main?.surface === "voice" &&
    (textSearch ?? textSearchDefault(main?.surface));

  const chosen = drafts
    .filter((d) => picked[d.id])
    .map((d) => ({ ...d, title: titles[d.id]?.trim() || d.title }));
  /* The search this fix will run, worked out now — so the range is real. */
  /* The agent's constraints decide what the search may try and what it may spend. */
  const agentLimits = useMemo(
    () => limitsOf(state, agent?.key),
    [state, agent],
  );
  /* Up to the agent's run budget and time cap by default; more is this fix's own. */
  const budgets = budgetsWithin(agentLimits.budget.dollars);
  const spend =
    budget ??
    (budgets.includes(FIX_DEFAULTS.dollars)
      ? FIX_DEFAULTS.dollars
      : budgets[budgets.length - 1]);
  const minuteChoices = minutesWithin(
    agentLimits.budget?.minutes || DEFAULT_LIMITS.budget.minutes,
  );
  const spendMinutes = minutes ?? minuteChoices[minuteChoices.length - 1];
  /* What the fix is held to: the agent's constraints with this fix's own edits. */
  const limits = useMemo(
    () =>
      withEdits(agentLimits, {
        ...edits,
        ...(spend > agentLimits.budget.dollars && { dollars: spend }),
        ...(spendMinutes > agentLimits.budget.minutes && {
          minutes: spendMinutes,
        }),
      }),
    [agentLimits, edits, spend, spendMinutes],
  );
  /* Planned with the other environments ticked, so the estimate prices them the way the run will. */
  const preview = useMemo(
    () =>
      fixPreview(main, mainState, issue, drafts, limits, {
        textSearch: inText,
        state,
        regressionEnvIds: main ? [main.id, ...alsoIn] : [],
      }),
    [main, mainState, issue, drafts, limits, inText, state, alsoIn],
  );
  /* Members: one can be targeted; protected ones are never changed. */
  const members = membersOf(agent);
  const targetable = targetableMembers(agent, limits);
  /* Each extra environment runs its must-pass set for the finalists. */
  const regressionCount = others
    .filter((e) => alsoIn.includes(e.id))
    .reduce((n, e) => n + mustPassIn(e), 0);
  const estimate = fixEstimate({
    preview,
    scenarioCount: chosen.length,
    regressionCount,
    surface: main?.surface,
    inEnvCount: chosen.filter((d) =>
      (mainState?.scenarios || []).some((x) => x.id === d.id),
    ).length,
  });
  const overBudget = estimate.likely > spend;
  /* The same monthly balance runs and builds draw on: a fix whose ceiling is
     more than what's left is held, never started on credit it can't use. */
  const creditBalance = useCreditBalance();
  const overBalance = creditsForUsd(spend) > creditBalance.left;
  const overTime = estimate.minutes > spendMinutes;

  /* What's in production: the latest release, else the version the issue's
     traces carry — or, honestly, just the current version. */
  const production = productionVersionOf(agent, state, issue, mainState);
  /* Earlier versions that changed something — what a lineage search offers again. */
  const lineageVersions = useMemo(() => {
    const list = agentVersions(mainState);
    const at = list.findIndex((v) => v.label === production.label);
    return (at > 0 ? list.slice(0, at) : []).filter(
      (v) => (v.changes || []).length,
    );
  }, [mainState, production.label]);
  const def = useMemo(
    () =>
      agent && main
        ? agentDefinitionOf({ agent, env: main, envState: mainState })
        : null,
    [agent, main, mainState],
  );
  const model = def?.model?.visible ? def.model.name : null;
  /* The diagnosis the search starts from — drafted, then whatever the person edits. */
  const drafted = useMemo(
    () =>
      codeDiagnosisOf({
        issue,
        env: main,
        promptText: def?.instructions?.visible ? def.instructions.text : null,
        rootCause,
      }),
    [issue, main, def, rootCause],
  );
  const [diagEdits, setDiagEdits] = useState({});
  const edited = Object.keys(diagEdits).some(
    (k) => diagEdits[k] !== drafted?.[k],
  );
  const diagnosis = drafted ? { ...drafted, ...diagEdits, edited } : null;
  const lead = diagnosis ? 1 : 0;
  const constraints = limitRows(limits, model);

  const go = () => {
    const at = start({
      env: main,
      drafts: chosen,
      regressionEnvIds: [main.id, ...alsoIn],
      budget: spend,
      minutes: spendMinutes,
      limits,
      lineage: acrossLineage ? lineageVersions : undefined,
      textSearch: inText,
      targetMember: target || undefined,
      rootCause,
      diagnosis: diagnosis
        ? {
            where: diagnosis.where,
            cause: diagnosis.cause.trim(),
            change: diagnosis.change.trim(),
            mechanism: diagnosis.mechanism,
            edited,
            symptom: diagnosis.symptom,
          }
        : null,
    });
    /* Lands on the fix's first run, playing live; its banner opens the fix. */
    if (at?.liveRunId)
      navigate(paths.dashboard.simulate.simulationRun(at.envId, at.liveRunId));
    else navigate(...issueFixNav(at, issue?.cluster_id));
  };

  const meta = [
    issue?.cluster_id,
    issue?.suggested_fix?.failureType,
    issue?.trace_count && `${issue.trace_count} traces`,
    issue?.severity &&
      `${issue.severity.charAt(0).toUpperCase()}${issue.severity.slice(1)}`,
  ]
    .filter(Boolean)
    .join(" · ");
  /* "agent v2 × environment v7" — what a regression environment would run:
     the version in production, the same one the Agent row names and the
     fix improves. */
  const pairOf = (e) => {
    const es = state.byEnv[e.id];
    const envLabel = currentEnvSnapshot(e, es)?.label;
    return `agent ${production.label || currentAgentVersion(es)?.label || "—"} × environment ${envLabel || "—"}`;
  };

  const gateNote = hardGate && (
    <Stack
      direction="row"
      spacing={1.25}
      sx={{
        px: 1.5,
        py: 1.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Iconify
        icon="solar:lock-keyhole-linear"
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0, mt: "1px" }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {hardGate.title}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {hardGate.body}
        </Typography>
      </Box>
      {(hardGate.action === "connections" ||
        hardGate.action === "overview") && (
        <Button
          size="small"
          variant="outlined"
          onClick={() =>
            navigate(
              `${paths.dashboard.agents}/connected/${agent.id}?tab=${hardGate.action}`,
            )
          }
          sx={{
            color: "text.primary",
            borderColor: "divider",
            flexShrink: 0,
            alignSelf: "center",
          }}
        >
          {hardGate.action === "overview"
            ? "Check what we found"
            : "Open Connections"}
        </Button>
      )}
    </Stack>
  );
  const consentBlock = agent && (consentNeeded || askedConsent) && (
    <Box>
      <Typography sx={label}>Consent for live tests</Typography>
      <Box
        sx={{
          px: 1.5,
          py: 1.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
        }}
      >
        <LiveConsentCard
          agent={agent}
          env={main}
          onSave={(values) => setAgentValues(agent.key, values)}
          bare
        />
      </Box>
    </Box>
  );

  if (!improving) return null;

  /* What the summary and the button say, by where things stand. */
  const startNote = needsEnv
    ? "Build its environment first — then start the fix from here."
    : building
      ? "Its environment is still building — start the fix when it's done."
      : overBalance
        ? `Up to ${creditsLabel(spend)}, but ${Math.max(0, creditBalance.left).toLocaleString()} credits are left this month — lower the budget or ask an admin.`
        : "A winner comes back as a new version. You decide whether to make it the default.";
  const startButton = needsEnv ? (
    <Button
      fullWidth
      variant="contained"
      color="primary"
      disabled={!!hardGate}
      onClick={() => setBuildOpen(true)}
    >
      Build environment
    </Button>
  ) : building ? (
    <Button
      fullWidth
      variant="contained"
      color="primary"
      disabled={!!hardGate}
      onClick={openBuilding}
    >
      Open environment
    </Button>
  ) : (
    <Button
      fullWidth
      variant="contained"
      color="primary"
      disabled={!chosen.length || !!hardGate || consentNeeded || overBalance}
      onClick={go}
      startIcon={<Iconify icon="solar:magic-stick-3-linear" width={16} />}
    >
      Start fix
    </Button>
  );
  const alsoNames = others
    .filter((e) => alsoIn.includes(e.id))
    .map((e) => e.name);

  return (
    <Box
      sx={{
        flex: 1,
        height: "100%",
        overflow: "auto",
        bgcolor: "background.paper",
      }}
    >
      {/* Full width, the improvement page's gutters — no centred column. */}
      <Box sx={{ px: { xs: 2, md: 2.5 }, pt: 2, pb: 6 }}>
        {/* Two lines, like the issue page: back to the issue · what's being
            fixed, then the issue's ID, kind, size and severity. */}
        <Box sx={{ mb: 2.5, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" gap={1} minWidth={0}>
            <Tooltip title="Back to the issue" arrow>
              <IconButton
                size="small"
                aria-label="Back to the issue"
                onClick={onClose}
                sx={{ color: "text.secondary", ml: -0.5 }}
              >
                <Iconify icon="solar:arrow-left-linear" width={16} />
              </IconButton>
            </Tooltip>
            <Typography
              noWrap
              sx={{
                fontSize: 16,
                lineHeight: "24px",
                fontWeight: 600,
                minWidth: 0,
              }}
            >
              {`Fix · ${issue?.error?.name || "this issue"}`}
            </Typography>
          </Stack>
          <Typography
            noWrap
            sx={{ typography: "s2", color: "text.tertiary", pl: 4 }}
          >
            {meta}
          </Typography>
        </Box>

        {/* ── no agent records this issue's project ── */}
        {!agent && (
          <Notice
            icon="solar:link-broken-linear"
            title={`No agent is linked to ${projectName || "this issue's project"}`}
            body="Fix proves a change on the agent that produced these traces. Connect it once — this project comes with it, and Fix works for every issue from it."
            action={
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() => setConnecting(true)}
              >
                Connect agent to fix
              </Button>
            }
            onCancel={onClose}
          >
            <ConnectAgentDialog
              open={connecting}
              onClose={() => setConnecting(false)}
              issue={issue}
              project={issueProjectLinkOf(issue, state, agents).project}
            />
          </Notice>
        )}

        {/* ── a fix is already in flight: open it, never start a second ── */}
        {inFlight && (
          <Notice
            icon="solar:magic-stick-3-linear"
            title={`This issue already has a fix · ${status.label}`}
            body="An issue has one fix at a time. If it doesn't fix the issue, or the issue comes back after release, Fix again starts a new one."
            action={
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() =>
                  navigate(...issueFixNav(status, issue?.cluster_id))
                }
              >
                View fix
              </Button>
            }
            onCancel={onClose}
          />
        )}

        {/* ── preparing the plan ── */}
        {agent && !inFlight && !prepared && (
          <Box sx={{ maxWidth: 640 }}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              Preparing the fix plan
            </Typography>
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}
            >
              What the fix needs — usually a few seconds. The changes are
              drafted after you start.
            </Typography>
            <Box
              sx={{
                mt: 2,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
              }}
            >
              {prepSteps.map((step, i) => {
                const detail = {
                  issue: `${issue?.trace_count || 0} traces · ${issue?.users_affected || 0} users`,
                  agent: `${agent.name} · ${production.label || "production"}`,
                  env: main
                    ? `${main.name} · ${ready ? "already built — the fix measures it on today's version first" : "still building"}`
                    : "None yet — you'll build one before the fix starts",
                  diagnosis: drafted
                    ? `${drafted.where.map((w) => `${w.kind} · ${w.ref}`).join(", ")}`
                    : "No prompt or tools to read — the search starts from the issue",
                  scenarios: `${drafts.length} situations · callers inferred · failing step marked`,
                  limits: [
                    constraints.find((c) => c.id === "model")?.value,
                    constraints.find((c) => c.id === "cost")?.value &&
                      `cost ${constraints.find((c) => c.id === "cost").value.toLowerCase()}`,
                    `up to ${creditsLabel(spend)}`,
                  ]
                    .filter(Boolean)
                    .join(" · "),
                }[step.id];
                return (
                  <Stack
                    key={step.id}
                    direction="row"
                    alignItems="flex-start"
                    spacing={1.25}
                    sx={{
                      px: 2,
                      py: 1.25,
                      borderTop: i ? "1px solid" : "none",
                      borderColor: "divider",
                    }}
                  >
                    <Box
                      sx={{
                        width: 16,
                        display: "grid",
                        placeItems: "center",
                        flexShrink: 0,
                        mt: "2px",
                      }}
                    >
                      {i < prep ? (
                        <Iconify
                          icon="solar:check-circle-linear"
                          width={16}
                          sx={{ color: "text.secondary" }}
                        />
                      ) : i === prep ? (
                        <CircularProgress
                          size={12}
                          thickness={5}
                          sx={{ color: "text.disabled" }}
                        />
                      ) : (
                        <Box
                          sx={{
                            width: 10,
                            height: 10,
                            borderRadius: "50%",
                            border: "1px solid",
                            borderColor: "divider",
                          }}
                        />
                      )}
                    </Box>
                    <Box flex={1} minWidth={0}>
                      <Typography
                        sx={{
                          typography: "s2",
                          color: i <= prep ? "text.primary" : "text.disabled",
                        }}
                      >
                        {step.label}
                      </Typography>
                      {i < prep && (
                        <Typography
                          sx={{ typography: "s3", color: "text.subtitle" }}
                        >
                          {detail}
                        </Typography>
                      )}
                    </Box>
                  </Stack>
                );
              })}
            </Box>
          </Box>
        )}

        {/* ── the plan, and its summary beside it ── */}
        {agent && !inFlight && prepared && (
          <>
            {/* What needs saying before anything else. */}
            {(ready && main.returnToIssue === issue?.cluster_id) ||
            evidence ||
            gateNote ||
            consentBlock ? (
              <Stack spacing={1.25} sx={{ mb: 2.5 }}>
                {ready &&
                  main.returnToIssue &&
                  main.returnToIssue === issue?.cluster_id && (
                    <Callout
                      icon="solar:check-circle-linear"
                      title={`${main.name} is built`}
                    >
                      The fix runs its scenarios on today&apos;s version first.
                      Those that pass every repeat, with any you mark must-pass,
                      must keep passing.
                    </Callout>
                  )}
                {evidence && (
                  <Callout
                    icon="solar:info-circle-linear"
                    title={evidence.title}
                  >
                    {evidence.body}
                  </Callout>
                )}
                {gateNote}
                {consentBlock}
              </Stack>
            ) : null}

            <Box
              sx={{
                display: "grid",
                gap: 3,
                alignItems: "start",
                gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 1fr) 360px" },
              }}
            >
              {/* ── main column: the plan, in the order it happens ── */}
              <Stack spacing={2.5} sx={{ minWidth: 0 }}>
                {diagnosis && (
                  <Section
                    n={1}
                    title="Diagnosis"
                    meta={
                      edited
                        ? "Edited by you — the search starts from this"
                        : "Drafted from the agent's prompt, its tools and the failing calls — edit it if it's wrong"
                    }
                    action={
                      edited ? (
                        <Button
                          size="small"
                          onClick={() => setDiagEdits({})}
                          sx={{
                            color: "text.secondary",
                            minWidth: 0,
                            flexShrink: 0,
                          }}
                        >
                          Reset
                        </Button>
                      ) : null
                    }
                  >
                    <Diagnosis
                      diagnosis={diagnosis}
                      readOnly={!ready}
                      onEdit={(k, v) => setDiagEdits((d) => ({ ...d, [k]: v }))}
                      symptomFrom={
                        rootCause?.source === "analysis"
                          ? "Root cause tab"
                          : "Error Feed"
                      }
                    />
                  </Section>
                )}

                <Section
                  n={lead + 1}
                  title="Scenarios from this issue"
                  meta={`${chosen.length} of ${drafts.length} · used by this fix only${drafts.length > FIX_SCENARIO_CAP ? ` · the ${FIX_SCENARIO_CAP} most common are in by default` : ""}`}
                >
                  {drafts.length > 20 && (
                    <Box
                      sx={{
                        px: 2,
                        py: 1,
                        borderBottom: "1px solid",
                        borderColor: "divider",
                      }}
                    >
                      <InputBase
                        value={scenarioQuery}
                        onChange={(e) => setScenarioQuery(e.target.value)}
                        placeholder={`Search ${drafts.length} scenarios`}
                        startAdornment={
                          <Iconify
                            icon="solar:magnifer-linear"
                            width={14}
                            sx={{ color: "text.tertiary", mr: 1 }}
                          />
                        }
                        sx={{ typography: "s2", width: 1 }}
                      />
                    </Box>
                  )}
                  {drafts
                    .filter(
                      (d) =>
                        !scenarioQuery.trim() ||
                        d.title
                          .toLowerCase()
                          .includes(scenarioQuery.trim().toLowerCase()),
                    )
                    .map((d, i) => (
                      <Stack
                        key={d.id}
                        direction="row"
                        alignItems="flex-start"
                        spacing={1}
                        sx={{
                          px: 2,
                          py: 1,
                          borderTop: i ? "1px solid" : "none",
                          borderColor: "divider",
                        }}
                      >
                        <Checkbox
                          size="small"
                          checked={!!picked[d.id]}
                          disabled={!ready}
                          onChange={(e) =>
                            setPicked((p) => ({
                              ...p,
                              [d.id]: e.target.checked,
                            }))
                          }
                          inputProps={{ "aria-label": `Use ${d.title}` }}
                          sx={{
                            p: 0.25,
                            mt: 0.125,
                            ...(!ready && {
                              "&.Mui-disabled": { color: "text.disabled" },
                            }),
                          }}
                        />
                        <Box flex={1} minWidth={0}>
                          {/* The title is the scenario — edit it in place. */}
                          <InputBase
                            fullWidth
                            multiline
                            value={titles[d.id] ?? d.title}
                            readOnly={!ready}
                            onChange={(e) =>
                              setTitles((t) => ({
                                ...t,
                                [d.id]: e.target.value,
                              }))
                            }
                            sx={{
                              py: 0,
                              color: picked[d.id]
                                ? "text.primary"
                                : "text.disabled",
                              "& .MuiInputBase-input": {
                                typography: "s2",
                                fontWeight: 500,
                                p: 0,
                              },
                            }}
                          />
                          <Typography
                            sx={{ typography: "s3", color: "text.subtitle" }}
                          >
                            {`From ${d.errorFeedIssue.traceIds.length} traces${d.persona?.archetype ? ` · ${d.persona.archetype}, inferred from the calls` : d.persona?.name ? ` · ${d.persona.name}` : ""}`}
                          </Typography>
                        </Box>
                      </Stack>
                    ))}
                  <Typography
                    sx={{
                      px: 2,
                      py: 1,
                      typography: "s3",
                      color: "text.subtitle",
                      borderTop: "1px solid",
                      borderColor: "divider",
                      bgcolor: "background.neutral",
                    }}
                  >
                    {[
                      drafts[0]?.subGoals?.[1] &&
                        `Each checks the step that failed: ${drafts[0].subGoals[1].replace(/^Verify the step that failed in production:\s*/i, "")}.`,
                      ready
                        ? "Failing ones become targets; some are held back to prove the fix."
                        : "You can edit them once its environment is ready.",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  </Typography>
                </Section>

                <Section
                  n={lead + 2}
                  title="Where it's tested"
                  meta="A change that breaks what passes today is rejected"
                >
                  {!main ? (
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1.25}
                      sx={{ px: 2, py: 1 }}
                    >
                      <Iconify
                        icon="solar:box-minimalistic-linear"
                        width={16}
                        sx={{ color: "text.secondary", flexShrink: 0 }}
                      />
                      <Box flex={1} minWidth={0}>
                        <Typography
                          sx={{ typography: "s2_1", fontWeight: 500 }}
                        >
                          No environment yet
                        </Typography>
                        <Typography
                          sx={{ typography: "s3", color: "text.subtitle" }}
                        >{`Built from ${agent.name} — a few minutes. Then you come back here and start.`}</Typography>
                      </Box>
                    </Stack>
                  ) : (
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1.25}
                      sx={{ px: 2, py: 1 }}
                    >
                      <Iconify
                        icon="solar:box-minimalistic-linear"
                        width={16}
                        sx={{ color: "text.secondary", flexShrink: 0 }}
                      />
                      <Box flex={1} minWidth={0}>
                        <Typography
                          noWrap
                          sx={{ typography: "s2_1", fontWeight: 500 }}
                        >
                          {main.name}
                        </Typography>
                        <Typography
                          noWrap
                          sx={{ typography: "s3", color: "text.subtitle" }}
                        >
                          {ready
                            ? `${main.id === envs[0]?.id ? "Main environment" : "Picked for this fix"} · ${pairOf(main)} · measured on today's version first`
                            : `${main.id === envs[0]?.id ? "Main environment" : "Picked for this fix"} · still building — the fix starts once it's built`}
                        </Typography>
                      </Box>
                      <Typography
                        sx={{
                          typography: "s3",
                          fontWeight: 600,
                          color: "text.subtitle",
                          flexShrink: 0,
                        }}
                      >
                        {main.id === envs[0]?.id
                          ? "Main · always checked"
                          : "Runs here · always checked"}
                      </Typography>
                    </Stack>
                  )}
                  {/* The agent's other environments: their must-pass sets can join the check. */}
                  {ready &&
                    others.map((e) => {
                      const on = alsoIn.includes(e.id);
                      const n = mustPassIn(e);
                      return (
                        <Stack
                          key={e.id}
                          direction="row"
                          alignItems="center"
                          spacing={1.25}
                          {...clickable(
                            () =>
                              setAlsoIn((ids) =>
                                on
                                  ? ids.filter((x) => x !== e.id)
                                  : [...ids, e.id],
                              ),
                            { role: "checkbox", checked: on },
                          )}
                          sx={{
                            px: 2,
                            py: 1,
                            borderTop: "1px solid",
                            borderColor: "divider",
                            cursor: "pointer",
                            "&:hover": { bgcolor: "action.hover" },
                            ...focusRing,
                          }}
                        >
                          <Checkbox
                            size="small"
                            checked={on}
                            tabIndex={-1}
                            disableRipple
                            inputProps={{ "aria-hidden": true }}
                            sx={{ p: 0, flexShrink: 0 }}
                          />
                          <Box flex={1} minWidth={0}>
                            <Typography
                              noWrap
                              sx={{ typography: "s2_1", fontWeight: 500 }}
                            >
                              {e.name}
                            </Typography>
                            <Typography
                              noWrap
                              sx={{ typography: "s3", color: "text.subtitle" }}
                            >
                              {pairOf(e)}
                            </Typography>
                          </Box>
                          <Typography
                            noWrap
                            sx={{
                              typography: "s3",
                              color: "text.subtitle",
                              flexShrink: 0,
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            {n
                              ? `Also check ${n} must-pass`
                              : "No must-pass yet"}
                          </Typography>
                        </Stack>
                      );
                    })}
                </Section>

                <Section n={lead + 3} title="How it searches">
                  {members.length > 0 && (
                    <SettingRow
                      title="Target member"
                      body={
                        target
                          ? `Changes stay inside ${target}; parts every member shares are changed only when the fix needs them, and the proposal says so.`
                          : `Every proposal names the members it changes.${targetable.length < members.length ? " Protected members are never changed." : ""}`
                      }
                    >
                      <Select
                        size="small"
                        displayEmpty
                        value={target}
                        onChange={(e) => setTarget(e.target.value)}
                        sx={{
                          minWidth: 240,
                          typography: "s2",
                          "& .MuiSelect-select": { typography: "s2", py: 0.75 },
                        }}
                      >
                        <MenuItem value="" sx={{ typography: "s2" }}>
                          Any member the evidence points to
                        </MenuItem>
                        {members.map((m) => (
                          <MenuItem
                            key={m.id}
                            value={m.name}
                            disabled={isProtectedMember(m, limits)}
                            sx={{ typography: "s2" }}
                          >
                            {isProtectedMember(m, limits)
                              ? `${m.name} · protected, never changed`
                              : m.name}
                          </MenuItem>
                        ))}
                      </Select>
                    </SettingRow>
                  )}
                  {ready && main.surface === "voice" && (
                    <SettingRow
                      title="Search in text, confirm finalists as real calls"
                      body={
                        estimate.allCalls
                          ? `Up to ${estimate.minutes} min and ${creditsLabel(estimate.likely)} as text, against ${estimate.allCalls.minutes} min and ${creditsLabel(estimate.allCalls.likely)} as real calls. Only the finalists are called.`
                          : "Every run is a real call — slower and dearer, and closest to production."
                      }
                    >
                      <Switch
                        size="small"
                        checked={inText}
                        onChange={(e) => setTextSearch(e.target.checked)}
                      />
                    </SettingRow>
                  )}
                  {lineageVersions.length > 0 && (
                    <SettingRow
                      title="Search across earlier versions"
                      body={`Also tries the changes ${lineageVersions[0].label}–${lineageVersions[lineageVersions.length - 1].label} made, on ${production.label}. Checked the same way as every other proposed change.`}
                    >
                      <Switch
                        size="small"
                        checked={acrossLineage}
                        onChange={(e) => setAcrossLineage(e.target.checked)}
                      />
                    </SettingRow>
                  )}
                  {!members.length &&
                    !(ready && main.surface === "voice") &&
                    !lineageVersions.length && (
                      <Typography
                        sx={{
                          px: 2,
                          py: 1.25,
                          typography: "s2",
                          color: "text.secondary",
                        }}
                      >
                        Searches the parts the agent allows, guided by the
                        evidence. Nothing to choose for this agent.
                      </Typography>
                    )}
                </Section>

                <Section
                  n={lead + 4}
                  title="The agent's rules"
                  meta="A change that breaks one is rejected"
                >
                  <Box sx={{ px: 2, py: 1.5 }}>
                    <ImprovementConstraints
                      agent={agent}
                      env={main}
                      envState={mainState}
                      limits={agentLimits}
                      edits={edits}
                      onChange={setEdits}
                      model={model}
                      showTime={false}
                    />
                  </Box>
                </Section>
              </Stack>

              {/* ── beside it: the summary, and Start always in view ── */}
              <Box sx={{ position: { lg: "sticky" }, top: { lg: 16 } }}>
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "8px",
                    overflow: "hidden",
                  }}
                >
                  <Typography
                    sx={{
                      px: 2,
                      py: 1.25,
                      typography: "s1",
                      fontWeight: 600,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    This fix
                  </Typography>
                  <SumRow k="Agent">
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 600 }}
                    >
                      {agent.name}
                    </Typography>
                    <Typography
                      noWrap
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {production.line}
                    </Typography>
                    <Typography
                      {...clickable(
                        () =>
                          navigate(
                            `${paths.dashboard.agents}/connected/${agent.id}`,
                          ),
                        { role: "link" },
                      )}
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        color: "text.secondary",
                        cursor: "pointer",
                        width: "fit-content",
                        "&:hover": { color: "text.primary" },
                        ...focusRing,
                      }}
                    >
                      View agent
                    </Typography>
                  </SumRow>
                  <SumRow k="Tested in">
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 600 }}
                    >
                      {main ? main.name : "Not set up yet"}
                    </Typography>
                    <Typography
                      noWrap
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {main
                        ? alsoNames.length
                          ? `+ must-pass from ${alsoNames.join(", ")}`
                          : "its must-pass set is the regression check"
                        : "built before the fix starts"}
                    </Typography>
                  </SumRow>
                  <SumRow k="Scenarios">
                    <Typography
                      sx={{ typography: "s2", fontWeight: 600 }}
                    >{`${chosen.length} from this issue`}</Typography>
                    {regressionCount > 0 && (
                      <Typography
                        sx={{ typography: "s3", color: "text.subtitle" }}
                      >{`+ ${regressionCount} must-pass from other environments`}</Typography>
                    )}
                  </SumRow>
                  <SumRow k="Budget">
                    {ready ? (
                      <>
                        {/* The agent's run budget and time cap are the defaults;
                            past either, it asks in place whether the agent's
                            own limit should move too. */}
                        <Stack spacing={1}>
                          <ConstraintPicker
                            agent={agent}
                            limits={agentLimits}
                            field="dollars"
                            value={spend}
                            onChange={setBudget}
                            fullWidth
                          />
                          <ConstraintPicker
                            agent={agent}
                            limits={agentLimits}
                            field="minutes"
                            value={spendMinutes}
                            onChange={setMinutes}
                            steps={MINUTES}
                            fullWidth
                          />
                        </Stack>
                        <Typography
                          sx={{
                            typography: "s3",
                            color: "text.secondary",
                            mt: 0.75,
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {`Up to ${estimate.runs} trials · ${creditsLabel(estimate.likely)} · ${estimate.minutes} min`}
                        </Typography>
                        <Typography
                          sx={{
                            typography: "s3",
                            color:
                              overBudget || overTime
                                ? "text.primary"
                                : "text.subtitle",
                            mt: 0.25,
                          }}
                        >
                          {overBudget || overTime
                            ? `May reach the ${overBudget ? "credit" : "time"} budget before all ${estimate.runs} trials — you'll get the best it found. ${stopEarlyNote(estimate.runs)}`
                            : `Up to ${estimate.conversations.toLocaleString()} simulated conversations. ${stopEarlyNote(estimate.runs)}`}
                        </Typography>
                        {/* What the ceiling is made of, part by part. */}
                        <Box sx={{ mt: 1.5 }}>
                          <CostPartsTable
                            parts={estimate.parts}
                            totalLabel="Up to"
                            totalUsd={estimate.likely}
                            note={`At ${RATE_PLAN.name} · ${RATE_PLAN.version} rates. Stops at your budget; what it found by then is kept, and its spend is itemized on the improvement's page.`}
                          />
                        </Box>
                      </>
                    ) : (
                      <>
                        <Typography
                          sx={{ typography: "s2", fontWeight: 600 }}
                        >{`Up to ${creditsLabel(spend)} · ${spendMinutes} min`}</Typography>
                        <Typography
                          sx={{ typography: "s3", color: "text.subtitle" }}
                        >
                          From the agent — you set it when you start.
                        </Typography>
                      </>
                    )}
                  </SumRow>
                  <Box
                    sx={{
                      p: 2,
                      borderTop: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    {startButton}
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.subtitle",
                        mt: 1,
                        textAlign: "center",
                      }}
                    >
                      {startNote}
                    </Typography>
                    <Button
                      fullWidth
                      size="small"
                      onClick={onClose}
                      sx={{ mt: 0.5, color: "text.secondary" }}
                    >
                      Back to the issue
                    </Button>
                  </Box>
                </Box>
              </Box>
            </Box>

            {needsEnv && (
              <BuildEnvironmentDialog
                open={buildOpen}
                onClose={() => setBuildOpen(false)}
                agent={agent}
                defaultVersion={agent.production || agent.latest}
                onBuild={(choice) => {
                  setBuildOpen(false);
                  navigate(paths.dashboard.simulate.environmentBuild, {
                    state: {
                      presetSource: buildPresetFor(agent, choice),
                      fromIssue: issue?.cluster_id || null,
                    },
                  });
                }}
              />
            )}
          </>
        )}
      </Box>
    </Box>
  );
}

FixPage.propTypes = {
  /* Back to the issue. */
  onClose: PropTypes.func.isRequired,
  issue: PropTypes.object,
  rootCause: PropTypes.object,
};

/* A step of the plan — numbered, titled, its rows inside one frame. */
/*
  The diagnosis, as a spec sheet: where in the agent it goes wrong, why,
  and what to change — the last two edited in place, like the scenario
  titles below. The feed's read is kept as the symptom it is.
*/
function Diagnosis({ diagnosis, readOnly, onEdit, symptomFrom }) {
  const field = (k) => (
    <InputBase
      fullWidth
      multiline
      value={diagnosis[k]}
      readOnly={readOnly}
      onChange={(e) => onEdit(k, e.target.value)}
      inputProps={{
        "aria-label": k === "cause" ? "Why it fails" : "What to change",
      }}
      sx={{
        py: 0.25,
        px: 0.75,
        mx: -0.75,
        borderRadius: "4px",
        border: "1px solid transparent",
        "&:hover": { borderColor: readOnly ? "transparent" : "divider" },
        "&.Mui-focused": { borderColor: "primary.main" },
        "& .MuiInputBase-input": { typography: "s2", p: 0 },
      }}
    />
  );
  const rows = [
    [
      "Where",
      <Stack key="where" spacing={0.75}>
        {diagnosis.where.map((w) => (
          <Box key={`${w.kind}:${w.ref}`} sx={{ minWidth: 0 }}>
            <Typography sx={{ typography: "s2" }}>
              <Box
                component="span"
                sx={{ color: "text.secondary" }}
              >{`${w.kind} · `}</Box>
              <Box
                component="span"
                sx={{
                  fontWeight: 600,
                  fontFamily:
                    w.kind === "System prompt"
                      ? "inherit"
                      : "ui-monospace, Menlo, monospace",
                  fontSize: w.kind === "System prompt" ? undefined : 13,
                }}
              >
                {w.ref}
              </Box>
            </Typography>
            {w.quote && (
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  mt: 0.25,
                }}
              >{`“${w.quote}”`}</Typography>
            )}
          </Box>
        ))}
      </Stack>,
    ],
    diagnosis.suggested && [
      "Suggested fix",
      <Typography
        key="suggested"
        sx={{ typography: "s2", color: "text.primary" }}
      >
        {diagnosis.suggested}
        <Box component="span" sx={{ color: "text.subtitle" }}>
          {" · from the Error Feed"}
        </Box>
      </Typography>,
    ],
    ["Why it fails", field("cause")],
    ["What to change", field("change")],
    diagnosis.wrongIf && [
      "Wrong if",
      <Typography
        key="wrongif"
        sx={{ typography: "s2", color: "text.secondary" }}
      >
        {diagnosis.wrongIf}
        <Box component="span" sx={{ color: "text.subtitle" }}>
          {" · what would prove this fix wrong, from the Error Feed"}
        </Box>
      </Typography>,
    ],
    diagnosis.symptom && [
      "Symptom",
      <Typography
        key="symptom"
        sx={{ typography: "s2", color: "text.secondary" }}
      >
        {diagnosis.symptom}
        <Box
          component="span"
          sx={{ color: "text.subtitle" }}
        >{` · what ${symptomFrom} saw — the search doesn't start from it`}</Box>
      </Typography>,
    ],
  ].filter(Boolean);
  return (
    <Box>
      {rows.map(([k, v], i) => (
        <Box
          key={k}
          sx={{
            display: "grid",
            gridTemplateColumns: "120px minmax(0, 1fr)",
            columnGap: 2,
            px: 2,
            py: 1.125,
            borderTop: i ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s2",
              color: "text.subtitle",
              pt: k === "Why it fails" || k === "What to change" ? 0.25 : 0,
            }}
          >
            {k}
          </Typography>
          <Box sx={{ minWidth: 0 }}>{v}</Box>
        </Box>
      ))}
    </Box>
  );
}
Diagnosis.propTypes = {
  diagnosis: PropTypes.object.isRequired,
  readOnly: PropTypes.bool,
  onEdit: PropTypes.func.isRequired,
  symptomFrom: PropTypes.string,
};

function Section({ n, title, meta, action, children }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box
          sx={{
            width: 20,
            height: 20,
            borderRadius: "50%",
            border: "1px solid",
            borderColor: "divider",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {n}
          </Typography>
        </Box>
        <Typography sx={{ typography: "s1", fontWeight: 600, flexShrink: 0 }}>
          {title}
        </Typography>
        {meta && (
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.subtitle",
              flex: 1,
              minWidth: 0,
            }}
          >
            {meta}
          </Typography>
        )}
        {!meta && <Box flex={1} />}
        {action}
      </Stack>
      {children}
    </Box>
  );
}
Section.propTypes = {
  n: PropTypes.number,
  title: PropTypes.string,
  meta: PropTypes.string,
  action: PropTypes.node,
  children: PropTypes.node,
};

/* One choice in How it searches: what it is, what it does, its control. */
function SettingRow({ title, body, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{
        px: 2,
        py: 1.25,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {body}
        </Typography>
      </Box>
      <Box sx={{ flexShrink: 0 }}>{children}</Box>
    </Stack>
  );
}
SettingRow.propTypes = {
  title: PropTypes.string,
  body: PropTypes.string,
  children: PropTypes.node,
};

/* One line of the summary: a label above what it is. */
function SumRow({ k, children }) {
  return (
    <Box
      sx={{
        px: 2,
        py: 1.25,
        borderTop: "1px solid",
        borderColor: "divider",
        "&:nth-of-type(2)": { borderTop: "none" },
        minWidth: 0,
      }}
    >
      <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.25 }}>
        {k}
      </Typography>
      {children}
    </Box>
  );
}
SumRow.propTypes = { k: PropTypes.string, children: PropTypes.node };

/* Something to know before the plan — said once, at the top. */
function Callout({ icon, title, children }) {
  return (
    <Stack
      direction="row"
      spacing={1.25}
      sx={{
        px: 2,
        py: 1.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
      }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
          {children}
        </Typography>
      </Box>
    </Stack>
  );
}
Callout.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.node,
  children: PropTypes.node,
};

/* A page with one thing to do: why, and the way forward. */
function Notice({ icon, title, body, action, onCancel, children }) {
  return (
    <Box
      sx={{
        maxWidth: 640,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        p: 2.5,
      }}
    >
      <Stack direction="row" spacing={1.5}>
        <Iconify
          icon={icon}
          width={20}
          sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {title}
          </Typography>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}
          >
            {body}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
            {action}
            <Button
              size="small"
              onClick={onCancel}
              sx={{ color: "text.secondary" }}
            >
              Back to the issue
            </Button>
          </Stack>
        </Box>
      </Stack>
      {children}
    </Box>
  );
}
Notice.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string,
  body: PropTypes.string,
  action: PropTypes.node,
  onCancel: PropTypes.func,
  children: PropTypes.node,
};
