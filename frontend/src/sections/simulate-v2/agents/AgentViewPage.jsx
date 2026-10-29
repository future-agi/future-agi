import { staleSince } from "../_mock/optimizationRuns";
import PropTypes from "prop-types";
import { useCallback, useMemo, useState } from "react";
import SandboxImport from "./connect/SandboxImport";
import { SANDBOX_IMPORT_PROVIDERS } from "../_mock/connectAgent";
import { Helmet } from "react-helmet-async";
import { useSnackbar } from "notistack";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Box,
  Button,
  Chip,
  Dialog,
  IconButton,
  Menu,
  MenuItem,
  Select,
  Stack,
  Tab,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { useConnectedAgents, useSimStore } from "../store";
import {
  raiseNudgesIn,
  dispatchNudges,
  nudgeAnnouncement,
} from "../_mock/nudgeEffects";
import { draftReleaseOf } from "./versionRelease";
import {
  agentNeedsRuntime,
  agentSourceOf,
  connectedAgentsFrom,
  pushedVersion,
} from "../_mock/agentRegistry";
import { cloneNameFor, replicationOf } from "../_mock/replication";
import {
  githubAccessLabel,
  githubAccessOf,
  postSlack,
  useIntegrations,
} from "../_mock/integrations";
import { agentFilesOf } from "../_mock/agentFiles";
import { agentToolsFor } from "../_mock/toolFit";
import { credentialsVersionRecord, currentEnvVersion } from "../_mock/versions";
import { isGitAgent, versionNameOf, tracingCommitOf } from "../_mock/codeRefs";
import CommitPicker from "./CommitPicker";
import { currentEnvSnapshot } from "../_mock/envSnapshot";
import { runResultOf } from "../_mock/runResult";
import { ObserveProjectCard } from "./AgentObserveProjects";
import { useAgentObserveProject } from "./observeProject";
import { deleteBlockOf } from "./agentDelete";
import { useDemoMode } from "./demoMode";
import ImprovementSourcesList from "../improvements/ImprovementSourcesList";
import { improvementRowsOf } from "../improvements/improvementRows";
import AgentSyncDialog from "../components/AgentSyncDialog";
import AgentDefinitionTab from "./AgentDefinitionTab";
import AgentOverviewTab, { PassRateCard } from "./AgentOverviewTab";
import AddVersionDialog from "./AddVersionDialog";
import AgentRuntimeEditor from "./AgentRuntimeEditor";
import AgentCallSettings from "./AgentCallSettings";
import AgentReplicationCard from "./AgentReplicationCard";
import { EndpointConsent } from "./connect/replicationBits";
import {
  SecretRefEditor,
  SecretRefText,
  PreflightLine,
  RefTag,
} from "../workspace/SecretRefControls";
import { defaultRefFor, preflightRecord } from "../_mock/secretRefs";
import AgentVersionsTab from "./AgentVersionsTab";
import AgentConstraintsTab from "./AgentConstraintsTab";
import BuildEnvironmentDialog from "./BuildEnvironmentDialog";
import SwitchVersionDialog from "./SwitchVersionDialog";
import MovePlatformDialog from "./MovePlatformDialog";
import AgentSetupCard from "./AgentSetupCard";
import {
  COMING_SOON_NOTE,
  agentStatusOf,
  fixDepthOf,
  isComingSoon,
  mainEnvOf,
} from "../_mock/rsiModel";
import { OUTSIDE_CHANGE_LABEL, isOutsideChange } from "../_mock/agentSync";
import { runIdentityOf } from "../_mock/runIdentity";
import { limitsOf } from "./agentLimits";
import { AgentStatusBanner, NeedsConsentNotice } from "./AgentProfileStrip";
import { LiveConsentCard, RetellPinCard } from "./AgentLiveConsent";
import {
  ArchiveDialog,
  LifecycleCard,
  PrototypeControlsCard,
  ReleaseDetectionCard,
} from "./AgentLifecycleCard";
import { ActionText, DemoLabel, InfoTip, QuietTag } from "./AgentBits";
import { clickable, focusRing } from "../components/clickable";
import { EmptyState } from "../components/primitives";
import SettingsSection from "./SettingsSection";
import ConnectionCheckSection from "./ConnectionCheckSection";
import PlatformTestCall from "./PlatformTestCall";
import AgentCompatibilityTab from "../compat/AgentCompatibilityTab";
import { checksConnection, connectionStateOf } from "../_mock/connectionCheck";
import { IdentityCard, SourceCard } from "./AgentProfileCards";
import {
  PLATFORM_KEY_SCOPE,
  identityOf,
  isHostedPlatform,
  languagesLabel,
} from "./agentProfile";
import { currentUser } from "../_mock/scenarioProvenance";
import {
  MONO,
  ORIGIN_LABEL,
  ago,
  buildPresetFor,
  fixDepthLine,
  FIX_DEPTH_OPTIONS,
  REACHED_AT_OPTIONS,
  isFinishedRun,
  parentLabel,
  passRate,
  plural,
  runsOfAgent,
  showsSetup,
} from "./agentViewUtils";

const TABS = [
  { id: "overview", label: "Overview" },
  /* What we read about the agent, then the files themselves. */
  { id: "definition", label: "Definition" },
  { id: "versions", label: "Versions" },
  /* What an improvement may change on this agent, and what it must not move. */
  { id: "constraints", label: "Constraints" },
  { id: "environments", label: "Environments" },
  /* The improvement runs that tried to make this agent better. */
  { id: "improvements", label: "Improvements" },
  { id: "connections", label: "Connections" },
  /* Can it run a given world — per capability, with the cases each gap affects. */
  { id: "compatibility", label: "Compatibility" },
];

/* Old tab ids still arrive in links — each lands on the tab that took its
   content. Production lives in Observe, linked from the Overview. */
const TAB_ALIAS = {
  files: "definition",
  used: "environments",
  usedin: "environments",
  "used-in": "environments",
  connection: "connections",
  production: "overview",
};

/**
 * One connected agent. Answers, in order: what is this agent made of, what
 * changed lately, did it get better or worse, and where is it being tested.
 */
export default function AgentViewPage() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  /* Opened from an environment's View agent: back names it and goes there. */
  const fromEnv =
    location.state?.from === "environment" && location.state?.envId
      ? {
          id: location.state.envId,
          name: location.state.envName || "the environment",
        }
      : null;
  const goBack = () =>
    fromEnv
      ? navigate(paths.dashboard.simulate.environmentDetail(fromEnv.id))
      : location.state?.from
        ? navigate(-1)
        : navigate(`${paths.dashboard.agents}?tab=connected`);
  const {
    agents,
    removeAgent,
    recordAgentVersion,
    runVersionIn,
    setAgentValues,
    state,
    hydrated,
  } = useConnectedAgents();
  const { dispatch: storeDispatch } = useSimStore();
  /* A deleted agent is a tombstone: off the list, but the runs and
     improvements that name it still open its history here. */
  const agent = useMemo(
    () =>
      agents.find((a) => a.id === agentId) ||
      connectedAgentsFrom(state, { withDeleted: true }).find(
        (a) => a.id === agentId && a.deleted,
      ),
    [agents, agentId, state],
  );
  const [syncOpen, setSyncOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [buildOpen, setBuildOpen] = useState(false);
  /* The channel a Build environment is for, when one is picked (Calls settings). */
  const [buildChannel, setBuildChannel] = useState(null);
  const [switchFor, setSwitchFor] = useState(null); // { envId, label }
  const [moreAnchor, setMoreAnchor] = useState(null);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const integrations = useIntegrations();
  const { prs } = integrations;
  /* Staging-only controls (Simulate a push, Revoke key…) show with ?demo=1. */
  const demo = useDemoMode();
  /* The one Observe project its traces or call logs land in. */
  const observeLink = useAgentObserveProject(agent);
  const { enqueueSnackbar } = useSnackbar();

  const rawTab = params.get("tab") || "overview";
  const tab =
    TAB_ALIAS[rawTab] ||
    (TABS.some((t) => t.id === rawTab) ? rawTab : "overview");
  /* Opens on what's in production — a draft waiting to go live is one click
     away in the picker, never what the page shows by default. */
  const homeLabel = agent ? agent.production || agent.latest : null;
  const viewing =
    agent && agent.versions.some((v) => v.label === params.get("v"))
      ? params.get("v")
      : homeLabel;
  const setQuery = (patch) => {
    const next = new URLSearchParams(params);
    Object.entries(patch).forEach(([k, v]) =>
      v == null ? next.delete(k) : next.set(k, v),
    );
    setParams(next, { replace: true, state: location.state });
  };

  /* The environment the agent was first read in — its world is where the
     agent's own tools and knowledge were read from. The earliest one, so
     the order environments happen to be listed in never changes "what the
     agent is made of". An agent connected from the Agents page has none. */
  const home = useMemo(() => {
    if (!agent) return null;
    const cands = [...agent.usedIn, ...(agent.formerlyIn || [])];
    const at = (id) =>
      Date.parse(
        state.myEnvironments.find((e) => e.id === id)?.adoptedAt || 0,
      ) || 0;
    return [...cands].sort((a, b) => at(a.envId) - at(b.envId))[0] || null;
  }, [agent, state.myEnvironments]);
  const env = home
    ? state.myEnvironments.find((e) => e.id === home.envId)
    : null;
  const ctxState = useMemo(
    () =>
      agent
        ? {
            ...(home ? state.byEnv[home.envId] : {}),
            agentVersions: agent.versions,
            activeAgentVersion: null,
          }
        : null,
    [agent, home, state.byEnv],
  );

  const filesOf = useCallback(
    (label) =>
      agentFilesOf({
        agent,
        version: agent.versions.find((v) => v.label === label),
        env,
        envState: ctxState,
      }).files,
    [agent, env, ctxState],
  );
  /* A version's tools as the world it was read in resolves them — what the
     Versions tab fingerprints. */
  const toolsOf = useCallback(
    (label) => (env ? agentToolsFor(env, ctxState, label) : null),
    [env, ctxState],
  );

  /* Every run of this agent, each marked full or partial. A partial run (a
     rule probe, a hand-picked subset) is real history but not comparable to
     a full sweep, so pass rates only ever compare full runs. */
  const allRuns = useMemo(
    () => (agent ? runsOfAgent(agent, state) : []),
    [agent, state],
  );
  /* A run still in progress (or one that never finished) isn't a result —
     it's recorded as 0 of N when it starts. */
  const inProgress = useMemo(
    /* One unfinished for over a day was timed out — shown as stopped, not running. */
    () =>
      allRuns
        .filter((r) => !isFinishedRun(r))
        .map((r) => (staleSince(r) ? { ...r, stopped: true } : r)),
    [allRuns],
  );
  const runs = useMemo(
    () =>
      allRuns.filter(isFinishedRun).map((raw) => {
        /* Scored from the run's own scenarios, as the run screen shows it. */
        const r = runResultOf(
          state.myEnvironments.find((e) => e.id === raw.envId),
          state.byEnv[raw.envId],
          raw,
        );
        /* Use what the run recorded about itself; only guess for old runs. */
        if (typeof r.partial === "boolean") return r;
        const size = state.byEnv[r.envId]?.scenarios?.length || 0;
        return {
          ...r,
          partial: r.scenarioIds?.length
            ? r.scenarioIds.length < size
            : !(r.total >= size || /all scenarios/i.test(r.label || "")),
        };
      }),
    [allRuns, state],
  );
  /* Results compare within ONE environment — a run is environment × agent
     version, so the same version scores differently in different worlds. */
  const [scopeEnvId, setScopeEnvId] = useState(null);
  const scopes = agent ? [...agent.usedIn, ...(agent.formerlyIn || [])] : [];
  /* Opens on its main environment — where its fixes run. */
  const mainScopeId = agent ? mainEnvOf(agent, state)?.envId : null;
  const scopeEnv =
    scopes.find((u) => u.envId === scopeEnvId) ||
    scopes.find((u) => u.envId === mainScopeId) ||
    scopes[0] ||
    null;
  /* …and within one version of that environment: a changed world scores the
     same agent differently, so runs on an older world don't compare. */
  const scopeEnvObj = scopeEnv
    ? state.myEnvironments.find((e) => e.id === scopeEnv.envId)
    : null;
  const scopeEnvVersion = scopeEnvObj
    ? currentEnvVersion(scopeEnvObj, state.byEnv[scopeEnv.envId])?.label
    : null;
  /* …named as its environment version (a snapshot of world, scenarios, evals). */
  const scopeEnvShown = scopeEnvObj
    ? currentEnvSnapshot(scopeEnvObj, state.byEnv[scopeEnv.envId])?.label ||
      null
    : null;
  const scopedRuns = useMemo(
    () =>
      runs.filter(
        (r) =>
          r.envId === scopeEnv?.envId &&
          !r.partial &&
          (!r.envVersion ||
            !scopeEnvVersion ||
            r.envVersion === scopeEnvVersion),
      ),
    [runs, scopeEnv, scopeEnvVersion],
  );
  /* Why runs here don't make the pass-rate chart — by design they compare
     only full runs on the current environment version; the chart says which. */
  const heldBack = (() => {
    const here = runs.filter(
      (r) => r.envId === scopeEnv?.envId && !scopedRuns.includes(r),
    );
    const older = here.some(
      (r) =>
        r.envVersion && scopeEnvVersion && r.envVersion !== scopeEnvVersion,
    );
    const subset = here.some((r) => r.partial);
    return older && subset
      ? "older-or-subset"
      : older
        ? "older"
        : subset
          ? "subset"
          : null;
  })();
  const statsOf = useCallback(
    /* One label, or several read as one behaviour (v5 and its credentials-only v5.1). */
    (label) => {
      const labels = [].concat(label);
      const mine = scopedRuns.filter((r) => labels.includes(r.agentVersion));
      if (!mine.length) return null;
      /* Each run's pass rate, weighted by how many scenarios it measured. */
      const measured = mine.reduce(
        (n, r) => n + (r.measured ?? r.total ?? 0),
        0,
      );
      const points = mine.reduce(
        (n, r) => n + (passRate(r) || 0) * (r.measured ?? r.total ?? 0),
        0,
      );
      return {
        runs: mine.length,
        rate: measured ? Math.round(points / measured) : 0,
      };
    },
    [scopedRuns],
  );

  /* Only counts — the Improvements tab builds its own list. */
  const improvementStats = useMemo(() => {
    const mine = agent
      ? improvementRowsOf({ state, agents, prs }).filter(
          (r) => r.agentKey === agent.key,
        )
      : [];
    return {
      total: mine.length,
      ready: mine.filter((r) => r.status?.key === "ready").length,
      running: mine.filter((r) => r.status?.key === "running").length,
    };
  }, [agent, agents, state, prs]);
  const improvementCount = improvementStats.total;

  if (!agent) {
    /* Still loading the store — blank rather than flash "not found". Then
       the same empty state an unknown environment shows. */
    if (!hydrated) {
      return (
        <Box
          sx={{
            p: 2,
            height: "100%",
            minHeight: 420,
            display: "grid",
            placeItems: "center",
          }}
        />
      );
    }
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Agent not found"
          body="It may have been deleted, or the link is out of date."
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() =>
                navigate(`${paths.dashboard.agents}?tab=connected`)
              }
            >
              Back to agents
            </Button>
          }
        />
      </Box>
    );
  }

  const source = agentSourceOf(agent);
  /* How the platform copies it — said in the header, set on Connections. */
  const replication = replicationOf(agent, env);
  /* The labelled clone each environment made, as its build recorded it. */
  const clones = agent.usedIn.map((u) => {
    const e = state.myEnvironments.find((x) => x.id === u.envId);
    return {
      envId: u.envId,
      envName: u.envName,
      cloneName:
        e?.replication?.cloneName || cloneNameFor(e || { name: u.envName }),
    };
  });
  const saveValues = (patch, msg) => {
    setAgentValues(agent.key, patch);
    if (msg) enqueueSnackbar(msg, { variant: "success" });
  };
  /* Credentials are versioned with the agent: a replaced key is a minor
     version on top of production (v5 → v5.1). It behaves as v5, so no
     environment gets a card or a rebuild — those following production just
     run with the new key, and every run says which version (so which key)
     it used. */
  const replaceKey = (values) => {
    const base = agent.production;
    const v = credentialsVersionRecord(agent.versions, base, {
      what: "API key reference replaced",
    });
    setAgentValues(agent.key, {
      ...values,
      /* An admin-chosen production moves to the key it now runs with. */
      ...(agent.values?.activeVersion?.label === base && {
        activeVersion: { ...agent.values.activeVersion, label: v.label },
      }),
    });
    recordAgentVersion(agent.key, v);
    enqueueSnackbar(
      `API key replaced — saved as ${v.label}, credentials only. It behaves as ${v.sameAs}, so its environments have nothing to check.`,
      { variant: "success" },
    );
  };
  /* Connected, disconnected (a revoked key, a removed app, a deleted agent)
     or archived — said in the header and on Connections. */
  const status = agentStatusOf(agent);
  /* Finish setting up is on the Overview (see AgentSetupCard). */
  const settingUp = showsSetup(agent);
  /* Deleted reads like archived everywhere it stops work — with nothing to restore. */
  const deleted = status.status === "deleted";
  const archived = status.status === "archived" || deleted;
  const identity = identityOf(agent);
  /* The environment Fix runs in by default. */
  const mainEnv = mainEnvOf(agent, state);
  /* What archiving removes on the platform — the test copies we made. */
  const madeCopies = ["provider_clone", "platform_branch"].includes(
    replication.mode,
  )
    ? clones.map((c) => c.cloneName)
    : replication.mode === "platform_draft"
      ? [
          `Draft versions created for ${agent.usedIn.length === 1 ? agent.usedIn[0].envName : `${agent.usedIn.length} environments`}`,
        ]
      : [];
  const toggleArchive = () => {
    setArchiveOpen(false);
    if (archived) {
      saveValues(
        { archived: null },
        `${agent.name} restored — runs and improvements can start again.`,
      );
    } else {
      saveValues(
        {
          archived: { at: new Date().toISOString(), removedCopies: madeCopies },
        },
        `${agent.name} archived${madeCopies.length ? ` — ${madeCopies.length === 1 ? "its test copy was" : `${madeCopies.length} test copies were`} removed` : ""}. Versions, environments, runs and proofs stay.`,
      );
    }
  };
  const { files, definition } = agentFilesOf({
    agent,
    version: agent.versions.find((v) => v.label === viewing),
    env,
    envState: ctxState,
  });
  /* The version this one was made from — the Definition file view marks
     what changed since it. */
  const prevLabel = parentLabel(agent.versions, viewing);
  const prevFiles = prevLabel ? filesOf(prevLabel) : null;
  /* Add version changes the latest, whichever version is being viewed. */
  const latestDefinition =
    viewing === agent.latest
      ? definition
      : agentFilesOf({
          agent,
          version: agent.versions[agent.versions.length - 1],
          env,
          envState: ctxState,
        }).definition;

  const baseTools = env
    ? agentToolsFor(env, ctxState, agent.latest)
    : agentFilesOf({
        agent,
        version: agent.versions[agent.versions.length - 1],
        env,
        envState: ctxState,
      }).definition.tools.items;
  const announce = (v, how) =>
    postSlack({
      event: "version",
      title: `${agent.name} · ${v.label} saved from ${how}`,
      lines: [
        v.commit
          ? `${v.commit.sha} ${v.commit.message} — ${v.commit.author}`
          : v.note,
        agent.usedIn.length
          ? `${agent.usedIn.length === 1 ? "Its environment tests" : `Its ${agent.usedIn.length} environments test`} it on the next run if following production; pinned ones stay put.`
          : "No environment tests it right now.",
      ],
      actions: [
        "Compare with the previous version",
        ...(agent.usedIn.length ? ["Review environments"] : []),
      ],
      links: {
        "Compare with the previous version": `${paths.dashboard.agents}/connected/${agent.id}?tab=versions`,
        "Review environments": `${paths.dashboard.agents}/connected/${agent.id}?tab=environments`,
      },
    });
  /* Staging stand-in (?demo=1) for the release branch moving. */
  const simulatePush = () => {
    const v = pushedVersion({
      agent,
      versions: agent.versions,
      env,
      baseTools,
    });
    recordAgentVersion(agent.key, v);
    /* A new agent version only — anything it no longer fits is raised as a
       nudge in each environment using it, never applied (PRD v7 §4.10). */
    const envs = agent.usedIn
      .map((u) => state.myEnvironments.find((x) => x.id === u.envId))
      .filter(Boolean);
    const raised = raiseNudgesIn(state, envs, v);
    dispatchNudges(storeDispatch, raised);
    announce(v, "a push");
    setQuery({ v: null });
    const said = nudgeAnnouncement(raised);
    enqueueSnackbar(
      `Push ${v.commit.sha} saved as agent ${v.label}. Environments and scenarios stay as they are${said.count ? ` · ${said.text}` : ""}.`,
      { variant: "success" },
    );
  };

  /* From the Calls settings, for one channel ("voice", "chat") — the
     environment is built for that channel; elsewhere the agent's own. */
  const buildEnvironment = (ch) => {
    setBuildChannel(typeof ch === "string" ? ch : null);
    setBuildOpen(true);
  };
  const startBuild = (choice) =>
    navigate(paths.dashboard.simulate.environmentBuild, {
      state: {
        presetSource: buildPresetFor(agent, {
          ...choice,
          channel: buildChannel,
        }),
      },
    });
  /* Which version an environment should test — the one in production
     unless another was picked from Versions. */
  const switchTo = (envId, label = agent.production) =>
    setSwitchFor({ envId, label });
  const switchEnv = switchFor
    ? state.myEnvironments.find((e) => e.id === switchFor.envId)
    : null;
  /* Deleted only once it has no environments left (RSI PRD, Lifecycle). */
  const deleteBlocked = deleteBlockOf(agent);
  const disconnect = () => {
    setDisconnectOpen(false);
    if (deleteBlocked) return;
    navigate(`${paths.dashboard.agents}?tab=connected`);
    removeAgent(agent.key);
    enqueueSnackbar(
      `${agent.name} deleted. Its versions, runs and proofs are kept.`,
      { variant: "success" },
    );
  };
  /* Must-pass scenarios — each one, with the environment it lives in. */
  const mustPassItems = agent.usedIn.flatMap((u) =>
    (state.byEnv[u.envId]?.scenarios || [])
      .filter((x) => x.critical)
      .map((x) => ({
        id: x.id,
        name: x.title || x.name || "Scenario",
        persona:
          typeof x.persona === "string" ? x.persona : x.persona?.name || null,
        envId: u.envId,
        envName: u.envName,
      })),
  );
  const mustPass = {
    total: mustPassItems.length,
    envs: agent.usedIn.length,
    items: mustPassItems,
  };
  return (
    <Box sx={{ p: 2, height: "100%", overflow: "auto" }}>
      <Helmet>
        <title>{`${agent.name} · Agents | Future AGI`}</title>
      </Helmet>

      {/* ── header ── */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1.5 }}>
        {fromEnv ? (
          <Button
            size="small"
            onClick={goBack}
            startIcon={
              <Iconify icon="solar:alt-arrow-left-linear" width={16} />
            }
            sx={{ color: "text.secondary", flexShrink: 0, maxWidth: 220 }}
          >
            <Box
              component="span"
              sx={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {fromEnv.name}
            </Box>
          </Button>
        ) : (
          <IconButton size="small" onClick={goBack} aria-label="Back">
            <Iconify icon="solar:alt-arrow-left-linear" width={20} />
          </IconButton>
        )}
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography noWrap sx={{ typography: "m3", fontWeight: 600 }}>
              {agent.name}
            </Typography>
            {/* Disconnected is said once, by the status pill beside it. */}
            <Pill>
              {status.status === "disconnected"
                ? source.label.replace(/ · disconnected$/, "")
                : source.label}
            </Pill>
            <Tooltip
              arrow
              title={`${replication.how} ${replication.proofLabel} proof — ${replication.proofNote}`}
            >
              <Box
                {...clickable(() => setQuery({ tab: "connections" }))}
                sx={{ cursor: "pointer", ...focusRing }}
              >
                <Pill>{`${replication.label} · ${replication.proofLabel}`}</Pill>
              </Box>
            </Tooltip>
            {isComingSoon(agent) && (
              <Tooltip arrow title={COMING_SOON_NOTE}>
                <Box component="span">
                  <QuietTag>Coming soon</QuietTag>
                </Box>
              </Tooltip>
            )}
            {/* Draft until its connection check passes; Needs attention when
                something it relied on lapsed after a good test. */}
            {checksConnection(agent) &&
              (agent.values?.setupStartedAt || agent.values?.connectionTest) &&
              (() => {
                const c = connectionStateOf(agent);
                return (
                  <Tooltip arrow title={c.line}>
                    <Box
                      component="span"
                      sx={{ cursor: "pointer" }}
                      onClick={() => setQuery({ tab: "connections" })}
                    >
                      <QuietTag
                        tone={c.state === "attention" ? "error" : undefined}
                      >
                        {c.label}
                      </QuietTag>
                    </Box>
                  </Tooltip>
                );
              })()}
            {agent.sample && (
              <Tooltip
                arrow
                title="Comes with a prebuilt world — the agent it was designed and proven with. Connect your own agent to that environment to test yours there."
              >
                <Box component="span">
                  <QuietTag>Sample</QuietTag>
                </Box>
              </Tooltip>
            )}
            {status.status !== "connected" && (
              <Tooltip arrow title={status.reason || ""}>
                <Box
                  {...clickable(() => setQuery({ tab: "connections" }))}
                  sx={{ cursor: "pointer", ...focusRing }}
                >
                  <Pill
                    dot={
                      status.status === "disconnected"
                        ? "status.danger.solid"
                        : undefined
                    }
                  >
                    {status.label}
                  </Pill>
                </Box>
              </Tooltip>
            )}
          </Stack>
          {/* One line: where it lives, when it was last read, where it's
              tested, who owns it. Languages and the description sit in the
              tooltip — both are set on Connections. */}
          <Tooltip
            arrow
            placement="bottom-start"
            title={[
              identity.description,
              languagesLabel(identity.languages),
              "Owner, languages and description — edit on Connections",
            ]
              .filter(Boolean)
              .join(" · ")}
          >
            <Typography
              noWrap
              sx={{
                typography: "s3",
                color: "text.subtitle",
                mt: 0.25,
                width: "fit-content",
                maxWidth: "100%",
              }}
            >
              {source.idMissing ? (
                <Box
                  component="span"
                  {...clickable(() => setQuery({ tab: "connections" }))}
                  sx={{
                    color: "status.danger.fg",
                    cursor: "pointer",
                    "&:hover": { textDecoration: "underline" },
                    ...focusRing,
                  }}
                >
                  Assistant ID missing
                </Box>
              ) : (
                <Box component="span" sx={{ fontFamily: MONO }}>
                  {/* A pasted prompt has nowhere to point — show what it runs at. */}
                  {(source.kind === "prompt"
                    ? source.runtime.where
                    : source.where) || "—"}
                </Box>
              )}
              {replication.runLocation === "hosted" ? " · hosted sandbox" : ""}
              {` · ${agent.lastSyncedAt ? `Synced ${ago(agent.lastSyncedAt)}` : `Connected ${ago(agent.connectedAt)}`} · `}
              <Box
                component="span"
                {...clickable(() => setQuery({ tab: "environments" }))}
                sx={{
                  cursor: "pointer",
                  "&:hover": {
                    color: "text.primary",
                    textDecoration: "underline",
                  },
                  ...focusRing,
                }}
              >
                {agent.usedIn.length
                  ? `${agent.usedIn.length} environment${agent.usedIn.length === 1 ? "" : "s"}`
                  : agent.formerlyIn?.length || agent.deletedIn?.length
                    ? "No environment now"
                    : "No environment yet"}
              </Box>
              {" · "}
              <Box
                component="span"
                {...clickable(() => setQuery({ tab: "connections" }))}
                sx={{
                  cursor: "pointer",
                  "&:hover": {
                    color: "text.primary",
                    textDecoration: "underline",
                  },
                  ...focusRing,
                }}
              >
                {`Owner ${identity.owner}`}
              </Box>
            </Typography>
          </Tooltip>
        </Box>
        {/* A code agent is picked the way Git names it — branch, then commit;
            any other agent by its version number. */}
        {isGitAgent(agent) ? (
          <CommitPicker
            agent={agent}
            value={viewing}
            production={agent.production}
            onChange={(l) => setQuery({ v: l === homeLabel ? null : l })}
          />
        ) : (
          <Select
            size="small"
            value={viewing}
            onChange={(e) =>
              setQuery({
                v: e.target.value === agent.latest ? null : e.target.value,
              })
            }
            /* Only changes what this page shows — environments pick their own. */
            /* The same words as every version picker: draft / in production. */
            renderValue={(l) =>
              l === agent.production
                ? `${l} · in production`
                : l === agent.latest && agent.latestIsDraft
                  ? `${l} · draft`
                  : `Viewing ${l}`
            }
            sx={{
              height: 32,
              typography: "s2",
              fontWeight: 600,
              minWidth: 120,
            }}
          >
            {[...agent.versions].reverse().map((v) => (
              <MenuItem key={v.label} value={v.label} sx={{ typography: "s2" }}>
                <Box
                  component="span"
                  sx={{ fontFamily: MONO, fontWeight: 600, mr: 1 }}
                >
                  {v.label}
                </Box>
                <Box component="span" sx={{ color: "text.subtitle" }}>
                  {[
                    v.pendingDraft
                      ? "draft"
                      : v.label === agent.production
                        ? "in production"
                        : null,
                    ago(v.createdAt || v.connectedAt),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </Box>
              </MenuItem>
            ))}
          </Select>
        )}
        {/* Re-read the source when we can; an upload takes its new folder or
            .zip; otherwise record the change by hand. */}
        <Tooltip
          arrow
          title={
            source.kind === "upload"
              ? "Upload the new folder or .zip — we read it, show what changed since the last upload and save it as the next version"
              : status.status !== "connected" && source.syncable
                ? `${status.label} — ${status.reason}`
                : source.syncable
                  ? `Sync from ${source.label} and save any changes as a new version`
                  : source.idMissing
                    ? `This agent lost its ${source.label} assistant ID. Add it back and Sync works again.`
                    : source.appLost
                      ? "The GitHub app no longer has access to this repository — record a change by hand, or reinstall it."
                      : `We can't sync ${source.label} agents — record what changed as a new version.`
          }
        >
          <span>
            <Button
              variant="outlined"
              size="small"
              disabled={
                deleted || (source.syncable && status.status !== "connected")
              }
              onClick={() =>
                source.syncable || source.kind === "upload"
                  ? setSyncOpen(true)
                  : source.idMissing
                    ? setQuery({ tab: "connections", edit: "id" })
                    : setAddOpen(true)
              }
              startIcon={
                <Iconify
                  icon={
                    source.kind === "upload"
                      ? "solar:cloud-upload-linear"
                      : source.syncable
                        ? "solar:refresh-linear"
                        : source.idMissing
                          ? "solar:link-round-linear"
                          : "solar:add-circle-linear"
                  }
                  width={16}
                />
              }
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              {source.kind === "upload"
                ? "Upload new version"
                : source.syncable
                  ? "Sync"
                  : source.idMissing
                    ? "Reconnect"
                    : "Add version"}
            </Button>
          </span>
        </Tooltip>
        {/* Build environment lives in Next steps and on the Environments tab —
            one of two ways forward, not a header action. */}
        {!deleted && (
          <IconButton
            size="small"
            aria-label="More actions"
            onClick={(e) => setMoreAnchor(e.currentTarget)}
            sx={{ color: "text.subtitle" }}
          >
            <Iconify icon="solar:menu-dots-bold" width={20} />
          </IconButton>
        )}
        <Menu
          anchorEl={moreAnchor}
          open={!!moreAnchor}
          onClose={() => setMoreAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { minWidth: 200, mt: 0.5 } } }}
        >
          <MenuItem
            onClick={() => {
              setMoreAnchor(null);
              setArchiveOpen(true);
            }}
            sx={{ gap: 1, py: 1 }}
          >
            <Iconify
              icon={archived ? "solar:restart-linear" : "solar:archive-linear"}
              width={16}
              sx={{ flexShrink: 0, color: "text.secondary" }}
            />
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {archived ? "Restore" : "Archive"}
            </Typography>
          </MenuItem>
          {/* Deleted only once it has no environments left — said, not hidden. */}
          <Tooltip arrow placement="left" title={deleteBlocked || ""}>
            <span>
              <MenuItem
                disabled={!!deleteBlocked}
                onClick={() => {
                  setMoreAnchor(null);
                  setDisconnectOpen(true);
                }}
                // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
                sx={{ gap: 1, py: 1, color: "status.danger.fg" }}
              >
                <Iconify
                  icon="solar:trash-bin-minimalistic-linear"
                  width={16}
                  sx={{ flexShrink: 0 }}
                />
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, color: "inherit" }}
                >
                  Delete
                </Typography>
              </MenuItem>
            </span>
          </Tooltip>
        </Menu>
      </Stack>

      <Box sx={{ borderBottom: "1px solid", borderColor: "divider", mb: 2 }}>
        <CustomTabs value={tab} onChange={(_, v) => setQuery({ tab: v })}>
          {TABS.map((t) => (
            <Tab
              key={t.id}
              value={t.id}
              disableRipple
              label={
                t.id === "versions"
                  ? `${isGitAgent(agent) ? "Commits" : "Versions"} ${agent.versions.length + (isGitAgent(agent) && tracingCommitOf(agent) ? 1 : 0)}`
                  : t.id === "environments"
                    ? `Environments ${agent.usedIn.length}`
                    : t.id === "improvements" && improvementCount
                      ? `Improvements ${improvementCount}`
                      : t.label
              }
            />
          ))}
        </CustomTabs>
      </Box>

      {tab !== "connections" && (
        <AgentStatusBanner
          agent={agent}
          onReconnect={() => setQuery({ tab: "connections" })}
          onRestore={() => setArchiveOpen(true)}
          onReconnected={() =>
            enqueueSnackbar(`${agent.name} is connected again.`, {
              variant: "success",
            })
          }
        />
      )}
      {/* What needs doing, as rows of one card — not a stack of banners. */}
      {/* Connected in one step: what's left, in one place — it carries consent
          and where it runs, so their own notices step aside while it shows. */}
      {tab === "overview" && !archived && (
        <AgentSetupCard
          agent={agent}
          env={env}
          onTab={(t) => setQuery({ tab: t })}
          onBuild={buildEnvironment}
          onSaveValues={saveValues}
        />
      )}
      {tab === "overview" && !settingUp && (
        <Box sx={TODO_CARD_SX}>
          {!archived && (
            <NeedsConsentNotice
              agent={agent}
              env={env}
              onOpen={() => setQuery({ tab: "connections" })}
            />
          )}
        </Box>
      )}
      {/* A framework agent with no address for serve() yet — can't be simulated yet. */}
      {tab === "overview" && !settingUp && agentNeedsRuntime(agent) && (
        <AgentRuntimeEditor agent={agent} />
      )}
      {/* An endpoint connected before the acknowledgment existed: blocked
          until someone says the real agent may be driven (AC-1.12). */}
      {tab === "overview" && replication.needsConsent && (
        <Box sx={{ mb: 2 }}>
          <EndpointConsent
            value={replication.consent}
            showWebhook={false}
            lead="Runs of this agent are blocked until this is acknowledged"
            onChange={(next) =>
              next &&
              saveValues(
                { endpointConsent: next },
                "Acknowledged — simulations can drive this agent. Add a secondary webhook on Connections to see its tool calls.",
              )
            }
          />
        </Box>
      )}
      {tab === "overview" && (
        <AgentOverviewTab
          /* Nothing set up yet — Next steps says it all; empty numbers would only repeat it. */
          fresh={
            settingUp &&
            !agent.usedIn.length &&
            !runs.length &&
            !inProgress.length &&
            !observeLink.project &&
            !improvementStats?.total
          }
          improvements={improvementStats}
          production={{
            project: observeLink.project,
            pending: !!observeLink.pending,
            onOpenProject: observeLink.openInObserve,
            /* Error Feed, filtered to this agent's project. */
            onViewIssues: observeLink.viewIssues,
            onLink: () => setQuery({ tab: "connections" }),
          }}
          agent={agent}
          source={source}
          files={files}
          runs={runs}
          inProgress={inProgress}
          scopedRuns={scopedRuns}
          statsOf={statsOf}
          scopeEnv={scopeEnv}
          scopeEnvVersion={scopeEnvShown}
          scopes={scopes}
          onScopeEnv={setScopeEnvId}
          onSwitch={(envId) => switchTo(envId)}
          onOpenRun={(r) =>
            navigate(paths.dashboard.simulate.simulationRun(r.envId, r.id))
          }
          onTab={(t) => setQuery({ tab: t })}
        />
      )}
      {tab === "definition" && (
        <AgentDefinitionTab
          agent={agent}
          definition={definition}
          source={source}
          files={files}
          prevFiles={prevFiles}
          label={viewing}
          prevLabel={prevLabel}
          latest={agent.latest}
          focusPath={params.get("file")}
          diffOn={params.get("diff") === "1"}
          observeProject={observeLink.project}
          observePending={!!observeLink.pending}
          onOpenEnv={(envId) =>
            navigate(paths.dashboard.simulate.environmentDetail(envId))
          }
          onTab={(t) => setQuery({ tab: t })}
          protectedMembers={limitsOf(state, agent.key).protectedMembers || []}
          onVersion={(v) => setQuery({ v })}
        />
      )}
      {/* Pass rate by version, above the versions it scores. */}
      {tab === "versions" && (
        <Box sx={{ mb: 2, height: 300 }}>
          <PassRateCard
            agent={agent}
            statsOf={statsOf}
            scopeEnv={scopeEnv}
            scopeEnvVersion={scopeEnvShown}
            scopes={scopes}
            onScopeEnv={setScopeEnvId}
            heldBack={heldBack}
          />
        </Box>
      )}
      {tab === "versions" && (
        <AgentVersionsTab
          agent={agent}
          source={source}
          filesOf={filesOf}
          toolsOf={toolsOf}
          statsOf={statsOf}
          scopeName={scopeEnv?.envName}
          onView={(l) =>
            setQuery({ v: l === homeLabel ? null : l, tab: "definition" })
          }
          onRunIn={switchTo}
          onMakeActive={(label) => {
            saveValues(
              {
                activeVersion: {
                  label,
                  at: new Date().toISOString(),
                  by: currentUser().name,
                },
              },
              `${label} is active — released. Environments following production test it on their next run; pinned ones stay put.`,
            );
            /* An improvement's draft made active is that improvement's release:
               recorded where it was verified, so the issue moves on to
               Watching and the other environments get its card. */
            const home = draftReleaseOf(agent, state, label);
            if (home)
              storeDispatch({
                type: "patchEnvState",
                envId: home.envId,
                patch: home.patch,
              });
          }}
        />
      )}
      {tab === "constraints" && (
        <AgentConstraintsTab
          agent={agent}
          onBuildEnv={archived ? null : buildEnvironment}
          files={filesOf(agent.latest)}
          model={definition?.model?.visible ? definition.model.name : null}
          speech={definition?.voice?.visible ? definition.voice : null}
          mustPass={mustPass}
        />
      )}
      {tab === "environments" && (
        <EnvironmentsTab
          agent={agent}
          focusEnvId={params.get("env")}
          mainEnvId={mainEnv?.envId}
          mainPicked={!!mainEnv?.picked}
          onMakeMain={(envId, envName) =>
            saveValues(
              { mainEnvId: envId },
              `${envName} is now the main environment — improvements run there, including fixes for Error Feed issues.`,
            )
          }
          onBuild={buildEnvironment}
          runs={runs}
          onRun={(envId) => switchTo(envId)}
          onOpen={(envId) =>
            navigate(paths.dashboard.simulate.environmentDetail(envId))
          }
        />
      )}
      {/* Archived: past improvements stay listed; a new one can't start. */}
      {tab === "improvements" && archived && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
          <Iconify
            icon="solar:archive-linear"
            width={14}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {deleted
              ? "Improvements are off — this agent is deleted. Its past improvements stay listed."
              : "Improvements are off — this agent is archived. Restore it from the menu at the top to start one."}
          </Typography>
        </Stack>
      )}
      {tab === "improvements" && <ImprovementSourcesList agent={agent} />}
      {tab === "compatibility" && (
        <AgentCompatibilityTab
          agent={agent}
          state={state}
          envId={params.get("env")}
          mainEnvId={mainEnv?.envId}
          onPickEnv={(id) => setQuery({ env: id })}
          onRepair={(row) =>
            setQuery({
              tab: row?.repair === "agent" ? "definition" : "connections",
              env: null,
            })
          }
        />
      )}
      {tab === "connections" && (
        <ConnectionTab
          agent={agent}
          source={source}
          definition={definition}
          onSync={() => setSyncOpen(true)}
          onSimulatePush={demo ? simulatePush : null}
          demo={demo}
          onOpenVersions={() => setQuery({ tab: "versions" })}
          env={env}
          clones={clones}
          onSaveValues={saveValues}
          onReplaceKey={replaceKey}
          onArchive={() => setArchiveOpen(true)}
          onDelete={() => setDisconnectOpen(true)}
          state={state}
          onBuildEnv={archived ? null : buildEnvironment}
          editId={params.get("edit") === "id"}
          onSetId={(id) => {
            setAgentValues(agent.key, { agentId: id });
            setQuery({ edit: null });
            enqueueSnackbar(`Reconnected — Sync reads ${source.label} again.`, {
              variant: "success",
            });
          }}
        />
      )}

      <BuildEnvironmentDialog
        open={buildOpen}
        onClose={() => setBuildOpen(false)}
        agent={agent}
        defaultVersion={agent.production || agent.latest}
        channel={buildChannel}
        onBuild={startBuild}
      />
      <SwitchVersionDialog
        open={!!switchFor}
        onClose={() => setSwitchFor(null)}
        env={switchEnv}
        envState={switchFor ? state.byEnv[switchFor.envId] : null}
        label={switchFor?.label}
        onConfirm={() => {
          runVersionIn(switchFor.envId, switchFor.label);
          enqueueSnackbar(
            switchFor.label === agent.production
              ? `${switchEnv?.name}'s default is production — its next run tests ${switchFor.label}. Recorded in its history.`
              : `${switchEnv?.name}'s default is ${switchFor.label} — its next run tests it. Recorded in its history.`,
            { variant: "success" },
          );
          setSwitchFor(null);
        }}
      />

      <AddVersionDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        agent={agent}
        definition={latestDefinition}
        baseTools={baseTools}
        onSave={(v) => {
          recordAgentVersion(agent.key, v);
          setAddOpen(false);
          setQuery({ v: null });
          enqueueSnackbar(
            `Saved as ${v.label}. Environments following production test it on their next run; pinned ones stay put.`,
            { variant: "success" },
          );
        }}
      />
      <AgentSyncDialog
        open={syncOpen}
        onClose={() => setSyncOpen(false)}
        agent={agent}
        versions={agent.versions}
        env={env}
        baseTools={baseTools}
        usedInCount={agent.usedIn.length}
        onOpenEnvironments={() => setQuery({ tab: "environments", v: null })}
        onSave={(v) => {
          recordAgentVersion(agent.key, v);
          announce(v, v.origin === "upload" ? "an upload" : "a sync");
          setQuery({ v: null });
        }}
      />
      <ArchiveDialog
        open={archiveOpen}
        agent={agent}
        copies={madeCopies}
        onClose={() => setArchiveOpen(false)}
        onConfirm={toggleArchive}
      />
      <DisconnectDialog
        open={disconnectOpen}
        agent={agent}
        onClose={() => setDisconnectOpen(false)}
        onConfirm={disconnect}
      />
    </Box>
  );
}

/* ── environments ─────────────────────────────────────────────────────────── */

function EnvironmentsTab({
  agent,
  runs,
  onBuild,
  onRun,
  onOpen,
  mainEnvId,
  mainPicked,
  onMakeMain,
  focusEnvId = null,
}) {
  const { state: simState } = useSimStore();
  /* "agent vN × environment vM" — what the last run there held fixed, as every run reads. */
  /* Every version reads vN; a code agent's carries its commit — "v5 · main @ 3f9a2c1". */
  const nm = (l) => versionNameOf(agent, l).main;
  const pairOfLast = (last, envId) => {
    const env = simState.myEnvironments.find((e) => e.id === envId);
    const envPart = runIdentityOf(
      last,
      simState.byEnv?.[envId],
      env,
    ).label.split(" × ")[1];
    return `agent ${nm(last.agentVersion)}${envPart ? ` × ${envPart}` : ""}`;
  };
  /* How it did there — the newest finished run in each environment. */
  const lastIn = (envId) => runs.find((r) => r.envId === envId);
  /* Environments that share a name are told apart by when they were built. */
  const nameCount = agent.usedIn.reduce(
    (m, u) => m.set(u.envName, (m.get(u.envName) || 0) + 1),
    new Map(),
  );
  const builtAt = (envId) =>
    simState.myEnvironments.find((e) => e.id === envId)?.adoptedAt || null;
  /* A clock time, not "1h ago" — copies built minutes apart must still differ. */
  const builtWhen = (iso, withSeconds = false) => {
    const d = new Date(iso);
    const time = d.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      ...(withSeconds && { second: "2-digit" }),
    });
    return d.toDateString() === new Date().toDateString()
      ? time
      : `${d.toLocaleDateString([], { day: "numeric", month: "short" })}, ${time}`;
  };
  /* Two built in the same minute get their seconds too. */
  const minuteLabels = agent.usedIn.map((u) =>
    builtAt(u.envId) ? builtWhen(builtAt(u.envId)) : null,
  );
  const builtLabel = (envId) => {
    const at = builtAt(envId);
    const label = builtWhen(at);
    return minuteLabels.filter((l) => l === label).length > 1
      ? builtWhen(at, true)
      : label;
  };
  /* Deleted environments took their runs with them — say so, quietly. */
  const deleted = agent.deletedIn?.length ? (
    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
      {`Was tested in ${agent.deletedIn.map((u) => u.envName).join(", ")} (deleted)`}
    </Typography>
  ) : null;
  if (!agent.usedIn.length) {
    return (
      <Box
        sx={{
          p: 4,
          textAlign: "center",
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: "8px",
        }}
      >
        <Typography sx={{ typography: "s1", fontWeight: 600 }}>
          {deleted
            ? "Not used in an environment"
            : "Not used in any environment yet"}
        </Typography>
        {deleted && <Box sx={{ mt: 0.25 }}>{deleted}</Box>}
        <Typography
          sx={{ typography: "s2", color: "text.subtitle", mt: 0.5, mb: 2 }}
        >
          An environment gives this agent a world to act in — tools, seeded
          data, rules and scenarios to run.
        </Typography>
        <Button
          variant="contained"
          color="primary"
          size="small"
          onClick={onBuild}
        >
          Build environment
        </Button>
      </Box>
    );
  }
  return (
    <Stack spacing={2}>
      {agent.usedIn.length > 0 && (
        <Card
          title="Environments testing this agent"
          action={
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ minWidth: 0 }}
            >
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
              >
                {agent.usedIn.length > 1
                  ? `Fixes run in the main one${mainPicked ? "" : " — the first built"}`
                  : "Each environment tests what's in production unless its default says otherwise"}
              </Typography>
              {/* Another world for the same agent — a second channel, a new
                  domain, a harder caller mix. */}
              {onBuild && (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => onBuild()}
                  startIcon={<Iconify icon="solar:add-linear" width={14} />}
                  sx={{
                    flexShrink: 0,
                    color: "text.primary",
                    borderColor: "divider",
                  }}
                >
                  Build environment
                </Button>
              )}
            </Stack>
          }
        >
          <Row
            header
            cells={["Environment", "Testing", "Last result", "Status", ""]}
          />
          {agent.usedIn.map((u) => {
            const last = lastIn(u.envId);
            const rate = last ? passRate(last) : null;
            return (
              <Row
                key={u.envId}
                highlight={u.envId === focusEnvId}
                cells={[
                  <Stack
                    key="n"
                    direction="row"
                    alignItems="center"
                    spacing={0.75}
                    sx={{ minWidth: 0 }}
                  >
                    <Box
                      component="span"
                      sx={{
                        fontWeight: 600,
                        minWidth: 0,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {u.envName}
                    </Box>
                    {nameCount.get(u.envName) > 1 && builtAt(u.envId) && (
                      <Box
                        component="span"
                        sx={{
                          color: "text.subtitle",
                          flexShrink: 0,
                          whiteSpace: "nowrap",
                        }}
                      >
                        {`built ${builtLabel(u.envId)}`}
                      </Box>
                    )}
                    {u.envId === mainEnvId && (
                      <Tooltip
                        arrow
                        title="Every fix from the Error Feed runs here. Other improvements start here unless you pick another environment."
                      >
                        <Box component="span">
                          <QuietTag>Main</QuietTag>
                        </Box>
                      </Tooltip>
                    )}
                  </Stack>,
                  <Box key="r" component="span" sx={{ fontFamily: MONO }}>
                    {nm(u.running)}
                  </Box>,
                  last ? (
                    <Box key="l" component="span">
                      <Box
                        component="span"
                        sx={{
                          fontWeight: 600,
                          color:
                            rate >= 80
                              ? "status.success.fg"
                              : rate >= 50
                                ? "text.primary"
                                : "status.danger.fg",
                        }}
                      >{`${rate}%`}</Box>
                      <Box
                        component="span"
                        sx={{ color: "text.subtitle" }}
                      >{` · ${pairOfLast(last, u.envId)} · ${ago(last.finishedAt)}`}</Box>
                    </Box>
                  ) : (
                    <Box
                      key="l"
                      component="span"
                      sx={{ color: "text.subtitle" }}
                    >
                      No runs yet
                    </Box>
                  ),
                  /* Each environment's default version: production unless
                     someone set another — then which, and production when
                     that's moved on. */
                  u.pinned ? (
                    <Box
                      key="s"
                      component="span"
                      sx={{
                        color: u.behind ? "text.primary" : "text.secondary",
                        fontWeight: u.behind ? 600 : 400,
                      }}
                    >
                      {u.behind
                        ? `Default ${nm(u.running)} · ${nm(agent.production)} in production`
                        : `Default ${nm(u.running)}`}
                    </Box>
                  ) : (
                    <Box
                      key="s"
                      component="span"
                      sx={{ color: "text.secondary" }}
                    >
                      Default: production
                    </Box>
                  ),
                  <Stack
                    key="a"
                    direction="row"
                    spacing={1}
                    justifyContent="flex-end"
                  >
                    {u.envId !== mainEnvId && onMakeMain && (
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => onMakeMain(u.envId, u.envName)}
                        sx={{ color: "text.primary", borderColor: "divider" }}
                      >
                        Make main
                      </Button>
                    )}
                    {u.behind && (
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => onRun(u.envId)}
                        sx={{ color: "text.primary", borderColor: "divider" }}
                      >
                        Default to production
                      </Button>
                    )}
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => onOpen(u.envId)}
                      sx={{ color: "text.primary", borderColor: "divider" }}
                    >
                      Open
                    </Button>
                  </Stack>,
                ]}
              />
            );
          })}
        </Card>
      )}
      {deleted}
    </Stack>
  );
}

EnvironmentsTab.propTypes = {
  /* The environment the page was opened from — its row is picked out. */
  focusEnvId: PropTypes.string,
  mainEnvId: PropTypes.string,
  mainPicked: PropTypes.bool,
  onMakeMain: PropTypes.func,
  agent: PropTypes.object,
  runs: PropTypes.array,
  onBuild: PropTypes.func,
  onRun: PropTypes.func,
  onOpen: PropTypes.func,
};

/* ── connection ───────────────────────────────────────────────────────────── */

/* The overview's to-do card: each notice inside it loses its own frame and
   becomes a row. Hidden when nothing needs doing. */
const TODO_CARD_SX = {
  mb: 2,
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  bgcolor: "background.paper",
  overflow: "hidden",
  "&:empty": { display: "none" },
  "& > *": {
    m: "0 !important",
    border: "none !important",
    borderRadius: "0 !important",
    bgcolor: "transparent !important",
  },
  "& > * + *": {
    borderTop: (t) => `1px solid ${t.palette.divider} !important`,
  },
};

function ConnectionTab({
  agent,
  source,
  definition,
  onSync,
  onSimulatePush,
  demo,
  onOpenVersions,
  editId,
  onSetId,
  onSaveValues,
  onReplaceKey,
  env,
  clones,
  onArchive,
  onDelete,
  state,
  onBuildEnv,
}) {
  const [editingRuntime, setEditingRuntime] = useState(false);
  const replication = replicationOf(agent, env);
  /* The repository a GitHub installation would cover — the agent's own record first. */
  const repoForAccess =
    agent?.definition?.repoUrl || agent?.source?.repoUrl || definition?.repoUrl;
  const [idDraft, setIdDraft] = useState("");
  /* Keys get rotated; a stale one quietly breaks Sync, so it can be
     replaced here without reconnecting. */
  const [editingKey, setEditingKey] = useState(false);
  /* Moved to another platform, or rebuilt under a new ID — still this agent. */
  const [moving, setMoving] = useState(false);
  const isPlatform =
    source.kind === "platform" && agent.values?.provider !== "other";
  /* Voice agents get their call settings as controls, not read-only rows. */
  const voice =
    ["voice_platform", "voice_sip"].includes(agent.typeId) ||
    !!agent.values?.contact;
  /* What a re-read can pick up from this source — an MCP server publishes
     instructions and tools, not the model; only a platform holds a voice. */
  const syncReads = (
    source.kind === "mcp"
      ? ["instructions", "tools"]
      : source.kind === "platform" && voice
        ? ["instructions", "model", "voice", "tools"]
        : ["instructions", "model", "tools"]
  ).join(", ");
  /* Only where an endpoint is how we reach it — a platform or a phone line
     is reached its own way, so editing an endpoint there would do nothing. */
  /* Code we build runs in our hosted sandbox — nothing of theirs to reach. */
  const builtHere =
    replication.runLocation === "hosted" && !source.runtime.where;
  const editableRuntime =
    !builtHere &&
    source.kind !== "platform" &&
    ["endpoint", "none"].includes(source.runtime.kind);
  const syncs = agent.versions
    .filter((v) => ["sync", "push", "pr", "upload"].includes(v.origin))
    .reverse();
  /* An upload has nothing to re-read — a new version is a new folder or .zip. */
  const uploaded = source.kind === "upload";
  const rows = [
    {
      label: "Reached at",
      value: source.runtime.where
        ? `${source.runtime.label} · ${source.runtime.where}${source.runtime.by ? ` · ${source.runtime.by}` : ""}`
        : builtHere
          ? "Hosted sandbox · built fresh from the code for every run"
          : source.idMissing
            ? `${source.label} API — needs the assistant ID below`
            : "Not set — simulations need it",
      edit: editableRuntime,
      options: REACHED_AT_OPTIONS,
    },
    ...(source.idMissing
      ? [{ label: "Assistant ID", value: "Missing", idRow: true }]
      : []),
    /* Read at install; write only once someone opens the first draft PR. */
    ...(githubAccessOf(repoForAccess)
      ? [
          {
            label: "GitHub access",
            value: githubAccessLabel(githubAccessOf(repoForAccess)),
          },
        ]
      : []),
    /* How deep an improvement can go for how it came in — plainly, for an
       agent we can only call. */
    {
      label: "How far a fix can go",
      value: fixDepthLine(agent),
      hint: `${fixDepthOf(agent).note} ${FIX_DEPTH_OPTIONS}`,
    },
    ...definition.connection
      .filter((r) => !/^(endpoint|agent id|provider)$/i.test(r.label))
      .filter(
        (r) =>
          !(
            voice &&
            /^(simulated calls|agent speaks first|call direction)$/i.test(
              r.label,
            )
          ),
      )
      .map((r) => (/^api key$/i.test(r.label) ? { ...r, keyRow: true } : r)),
    ...(isPlatform && !agent.values?.hasApiKey && !agent.values?.apiKeyRef
      ? [{ label: "API key", value: "Not stored", keyRow: true, missing: true }]
      : []),
    {
      label: "Connected",
      value: agent.connectedAt
        ? `${new Date(agent.connectedAt).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })} · ${ago(agent.connectedAt)}`
        : "—",
      /* Where it first came in — said on hover, not as its own row. */
      hint: `First seen ${agent.origin === "agents" ? "on the Agents page" : agent.origin === "github" ? "in Settings → GitHub" : `in ${agent.firstEnvName || "an environment"}`} · ${ORIGIN_LABEL(agent.versions[0], source).toLowerCase()} as v1.`,
    },
    /* The last sync, beside the connection it reads — not only under Source and sync. */
    {
      label: uploaded ? "Last upload" : "Last synced",
      value: syncs.length
        ? `${ago(syncs[0].createdAt || syncs[0].connectedAt)} · saved as ${syncs[0].label}`
        : uploaded
          ? `Only the first — ${source.where}`
          : source.syncable
            ? "Never synced"
            : "Can't be synced — use Add version when it changes",
      ...(source.syncable &&
        onSync && { action: { label: "Sync now", onClick: onSync } }),
      ...(uploaded &&
        onSync && { action: { label: "Upload new version", onClick: onSync } }),
    },
  ];
  const lastReadAt = agent.lastSyncedAt || syncs[0]?.createdAt || null;
  const syncWhat =
    source.kind === "repo"
      ? "the repository"
      : source.kind === "framework"
        ? `the agent through its ${source.label} adapter`
        : source.label;
  return (
    <Stack minWidth={0}>
      {checksConnection(agent) && (
        <ConnectionCheckSection agent={agent} onSaveValues={onSaveValues} />
      )}

      <SettingsSection
        title="How we reach it"
        sub={
          <>
            Where it lives, and its key
            <InfoTip text="Replacing the key saves a credentials-only version (v5 → v5.1); nothing is re-checked." />
          </>
        }
      >
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            overflow: "hidden",
          }}
        >
          {rows.map((r) => (
            <Stack
              key={r.label}
              direction="row"
              alignItems={
                r.keyRow && (editingKey || agent.values?.apiKeyCheck)
                  ? "flex-start"
                  : "center"
              }
              spacing={2}
              sx={{
                px: 2,
                py: 1,
                borderTop: "1px solid",
                borderColor: "divider",
                "&:first-of-type": { borderTop: "none" },
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.subtitle",
                  width: 150,
                  flexShrink: 0,
                  ...(r.keyRow && editingKey && { pt: 0.75 }),
                }}
              >
                {r.label}
                {r.keyRow && isHostedPlatform(agent) && (
                  <InfoTip text={PLATFORM_KEY_SCOPE} />
                )}
              </Typography>
              {r.idRow ? (
                /* A platform agent that lost its assistant ID — add it back and Sync works again. */
                <Stack
                  direction="row"
                  spacing={1}
                  sx={{ flex: 1, minWidth: 0 }}
                >
                  <TextField
                    size="small"
                    fullWidth
                    autoFocus={editId}
                    value={idDraft}
                    onChange={(e) => setIdDraft(e.target.value)}
                    placeholder={`${source.label} assistant ID`}
                    InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
                  />
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={!idDraft.trim()}
                    onClick={() => onSetId(idDraft.trim())}
                    sx={{ flexShrink: 0 }}
                  >
                    Reconnect
                  </Button>
                </Stack>
              ) : r.keyRow ? (
                /* By reference (AC-6.12): the path is shown, never the key.
                   Replacing it re-runs the access check and saves a
                   credentials-only version (v5 → v5.1). */
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  {editingKey ? (
                    <SecretRefEditor
                      placeholder={
                        agent.values?.apiKeyRef ||
                        defaultRefFor(
                          `${agent.values?.provider || "agent"}_api_key`,
                        )
                      }
                      saveLabel={r.missing ? "Save reference" : "Replace"}
                      onSave={(ref) => {
                        const values = {
                          hasApiKey: true,
                          apiKeyRef: ref,
                          apiKeyMigrated: false,
                          apiKeyCheck: preflightRecord(
                            ref,
                            agent.values?.provider,
                          ),
                          /* A new key reconnects an agent whose key was revoked. */
                          ...(agent.values?.disconnected?.cause === "key" && {
                            disconnected: null,
                          }),
                        };
                        /* The first key is setup; replacing one is a credentials version. */
                        if (r.missing || !onReplaceKey)
                          onSaveValues(
                            values,
                            "API key reference saved — checking access.",
                          );
                        else onReplaceKey(values);
                        setEditingKey(false);
                      }}
                      onCancel={() => setEditingKey(false)}
                    />
                  ) : (
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1}
                      sx={{ minWidth: 0 }}
                    >
                      <SecretRefText
                        value={agent.values?.apiKeyRef}
                        empty={
                          r.missing
                            ? "Not set"
                            : "Held by the connection · no reference recorded"
                        }
                        sx={{ flex: 1 }}
                      />
                      {agent.values?.apiKeyMigrated && (
                        <RefTag>moved from a pasted key</RefTag>
                      )}
                      {/* The key the platform refused — replacing it reconnects. */}
                      {agent.values?.disconnected?.cause === "key" && (
                        <QuietTag tone="error">
                          Refused — replace to reconnect
                        </QuietTag>
                      )}
                      <ActionText onClick={() => setEditingKey(true)}>
                        {r.missing ? "Add" : "Replace"}
                      </ActionText>
                    </Stack>
                  )}
                  {!editingKey && agent.values?.apiKeyCheck && (
                    <PreflightLine
                      check={agent.values.apiKeyCheck}
                      sx={{ mt: 0.5 }}
                    />
                  )}
                </Box>
              ) : r.edit && editingRuntime ? (
                <AgentRuntimeEditor
                  agent={agent}
                  current={source.runtime.where || ""}
                  variant="inline"
                  onDone={() => setEditingRuntime(false)}
                />
              ) : r.hint ? (
                <Typography
                  sx={{
                    typography: "s2",
                    flex: 1,
                    minWidth: 0,
                    fontFamily: r.mono ? MONO : undefined,
                  }}
                >
                  {r.value}
                  <InfoTip text={r.hint} />
                </Typography>
              ) : (
                <>
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontFamily: r.mono ? MONO : undefined,
                      minWidth: 0,
                      flex: 1,
                      color:
                        r.edit && !source.runtime.where
                          ? "status.danger.fg"
                          : "text.primary",
                    }}
                  >
                    {r.value}
                    {r.options && <InfoTip text={r.options} />}
                  </Typography>
                  {r.action && (
                    <ActionText onClick={r.action.onClick}>
                      {r.action.label}
                    </ActionText>
                  )}
                  {r.edit && (
                    <ActionText onClick={() => setEditingRuntime(true)}>
                      {source.runtime.where ? "Edit" : "Add"}
                    </ActionText>
                  )}
                </>
              )}
            </Stack>
          ))}
          {isPlatform && !source.idMissing && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{
                px: 2,
                py: 1,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.subtitle",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {`Moved off ${source.label}, or rebuilt under a new ID? It stays this agent.`}
              </Typography>
              <ActionText onClick={() => setMoving(true)}>Move…</ActionText>
            </Stack>
          )}
        </Box>
        <MovePlatformDialog
          open={moving}
          onClose={() => setMoving(false)}
          agent={agent}
          onSave={(patch, msg) => {
            onSaveValues(patch, msg);
            setMoving(false);
          }}
        />
      </SettingsSection>

      {/* A platform agent: one real test call, before a run. */}
      {source.kind === "platform" && (
        <SettingsSection
          title="Test call"
          sub="One real call through the platform — what it lets us measure, before a run."
        >
          <PlatformTestCall agent={agent} onSaveValues={onSaveValues} />
        </SettingsSection>
      )}

      <SettingsSection
        title="Live testing"
        sub="What we create and call in your account."
      >
        {/* Live agents: consent before the first run (an endpoint has its own, under How we test it). */}
        {replication.mode !== "wrapped_endpoint" && (
          <LiveConsentCard agent={agent} env={env} onSave={onSaveValues} />
        )}
        <RetellPinCard agent={agent} onSave={onSaveValues} />
        <AgentReplicationCard
          agent={agent}
          env={env}
          clones={clones}
          onSaveValues={onSaveValues}
        />
        {/* Vapi, Retell: test a sealed copy instead of the live assistant — offered here, never at connect. */}
        {SANDBOX_IMPORT_PROVIDERS.includes(agent.values?.provider) &&
          !agent.values?.importedCopy && (
            <SealedCopyOffer
              providerName={source.label}
              onSave={onSaveValues}
            />
          )}
      </SettingsSection>

      <SettingsSection
        title="Calls"
        sub="How simulations reach it, per channel."
      >
        <AgentCallSettings
          bare
          agent={agent}
          state={state}
          callOver={source.kind !== "prompt"}
          onSave={onSaveValues}
          onBuildEnv={onBuildEnv ? (ch) => onBuildEnv(ch) : null}
        />
      </SettingsSection>

      {/* The one Observe project its traces or call logs land in. */}
      <SettingsSection
        title="Observe project"
        sub={
          <>
            Where its traces or call logs feed the Error Feed.
            <InfoTip text="Error Feed issues from this project are this agent's — Fix, Copy for coding agent and an environment's From Error Feed all read it." />
          </>
        }
      >
        <ObserveProjectCard agent={agent} />
      </SettingsSection>

      <SettingsSection
        title="Source and sync"
        sub="What a version pins, and how it syncs."
      >
        <SourceCard agent={agent} source={source} onSave={onSaveValues} />
        <Card
          title={
            <>
              {uploaded ? "Upload" : "Sync"}
              {source.syncable && (
                <InfoTip
                  text={`Starting an improvement syncs it too. A change found there is marked "${OUTSIDE_CHANGE_LABEL}" and the improvement starts from it.`}
                />
              )}
            </>
          }
          action={
            source.syncable ? (
              <ActionText onClick={onSync}>Sync now</ActionText>
            ) : uploaded && onSync ? (
              <ActionText onClick={onSync}>Upload new version</ActionText>
            ) : null
          }
        >
          {/* Staging only (?demo=1): the release branch moving, on demand. */}
          {demo && onSimulatePush && source.viaApp && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 2,
                py: 1,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}
              >
                <DemoLabel />A push to the release branch
              </Typography>
              <Button
                size="small"
                variant="outlined"
                onClick={onSimulatePush}
                startIcon={
                  <Iconify icon="solar:upload-minimalistic-linear" width={14} />
                }
                sx={{ color: "text.primary", borderColor: "divider" }}
              >
                Simulate a push
              </Button>
            </Stack>
          )}
          <Typography
            sx={{ px: 2, py: 1.25, typography: "s3", color: "text.subtitle" }}
          >
            {source.syncable
              ? `Syncs from ${syncWhat} — a change is saved as a new version`
              : uploaded
                ? "Read once from the upload — upload a new folder or .zip to save the next version"
                : source.idMissing
                  ? "Needs the assistant ID — add it under How we reach it"
                  : source.appLost
                    ? "The GitHub app lost access to the repository"
                    : "Can't be synced — use Add version when it changes"}
            <InfoTip
              text={
                source.syncable
                  ? `What it picks up: ${syncReads}. Environments following production test it on their next run; pinned ones stay put.`
                  : source.appLost
                    ? "Reinstall it from Settings → Integrations → GitHub — or use Add version at the top of this page."
                    : source.kind === "repo" && !source.idMissing
                      ? "No repository is recorded for this agent, so there's nothing to sync."
                      : null
              }
            />
          </Typography>
          {/* The last re-read, in one line — every version it saved is on Versions. */}
          {syncs.length > 0 && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ px: 2, pb: 1.5 }}
            >
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {`${uploaded ? "Last upload" : "Last synced"} ${ago(syncs[0].createdAt || syncs[0].connectedAt)} · saved as `}
                <Box
                  component="span"
                  sx={{ fontFamily: MONO, color: "text.primary" }}
                >
                  {syncs[0].label}
                </Box>
                {isOutsideChange(syncs[0]) ? ` · ${OUTSIDE_CHANGE_LABEL}` : ""}
              </Typography>
              {onOpenVersions && (
                <ActionText onClick={onOpenVersions}>See versions</ActionText>
              )}
            </Stack>
          )}
          {source.syncable && !syncs.length && (
            <Typography
              sx={{ px: 2, pb: 1.5, typography: "s3", color: "text.subtitle" }}
            >
              Never synced.
            </Typography>
          )}
          {/* When re-reads happen — not a setting. */}
          {source.syncable && (
            <KvLine label="Re-read">
              {reReadLine(source)}
              {lastReadAt ? ` · last ${ago(lastReadAt)}` : ""}
            </KvLine>
          )}
        </Card>
        <ReleaseDetectionCard agent={agent} onSaveValues={onSaveValues} />
      </SettingsSection>

      <SettingsSection
        title="Identity"
        sub="Shown in the header and on Connected agents."
      >
        <IdentityCard bare agent={agent} onSave={onSaveValues} />
      </SettingsSection>

      <SettingsSection title="Archive or delete">
        <LifecycleCard
          agent={agent}
          onSaveValues={onSaveValues}
          onArchive={onArchive}
          onDelete={onDelete}
        />
      </SettingsSection>

      {/* Staging only (?demo=1): states the product reaches on its own. */}
      {demo && (
        <SettingsSection
          title={
            <>
              <DemoLabel />
              Staging controls
            </>
          }
          sub="States the product reaches on its own."
        >
          <PrototypeControlsCard agent={agent} onSaveValues={onSaveValues} />
        </SettingsSection>
      )}
    </Stack>
  );
}
ConnectionTab.propTypes = {
  state: PropTypes.object,
  onBuildEnv: PropTypes.func,
  onArchive: PropTypes.func,
  onDelete: PropTypes.func,
  editId: PropTypes.bool,
  onSetId: PropTypes.func,
  agent: PropTypes.object,
  source: PropTypes.object,
  definition: PropTypes.object,
  onSync: PropTypes.func,
  onSimulatePush: PropTypes.func,
  demo: PropTypes.bool,
  onOpenVersions: PropTypes.func,
  onSaveValues: PropTypes.func,
  onReplaceKey: PropTypes.func,
  env: PropTypes.object,
  clones: PropTypes.array,
};

/* ── disconnect ───────────────────────────────────────────────────────────── */

function DisconnectDialog({ open, agent, onClose, onConfirm }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Box sx={{ px: 3, pt: 2.5, pb: 2 }}>
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          {`Delete ${agent.name}?`}
        </Typography>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 0.75 }}
        >
          {`It leaves Agents and every picker. Its ${plural(agent.versions.length, "version")}, and the runs, improvements and proofs that name it, are kept — they still open its history.${agentStatusOf(agent).status === "archived" ? "" : " Archive it instead to keep it listed."}`}
        </Typography>
      </Box>
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          onClick={onConfirm}
          color="error"
        >
          Delete
        </Button>
      </Stack>
    </Dialog>
  );
}
DisconnectDialog.propTypes = {
  open: PropTypes.bool,
  agent: PropTypes.object.isRequired,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
};

/* ── small pieces ─────────────────────────────────────────────────────────── */

/* When Agents re-reads the agent (RSI PRD, Changes made outside Future AGI). */
function reReadLine(source) {
  const branch = source.kind === "repo" || source.viaApp;
  return `When an improvement starts, when you Sync, ${branch ? "when the release branch moves " : ""}and once a day`;
}

function KvLine({ label, children }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography
        sx={{
          typography: "s3",
          color: "text.subtitle",
          width: 80,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Typography
        sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}
      >
        {children}
      </Typography>
    </Stack>
  );
}
KvLine.propTypes = { label: PropTypes.node, children: PropTypes.node };

function Card({ title, action, children }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
          {title}
        </Typography>
        {action}
      </Stack>
      {children}
    </Box>
  );
}
Card.propTypes = {
  title: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
};

function Row({ cells, header, onClick, highlight = false }) {
  return (
    <Box
      {...clickable(onClick)}
      sx={{
        ...focusRing,
        display: "grid",
        gridTemplateColumns: `repeat(${cells.length}, minmax(0, 1fr))`,
        gap: 1.5,
        px: 2,
        py: header ? 0.75 : 1,
        alignItems: "center",
        borderTop: "1px solid",
        borderColor: "divider",
        /* Header reads like the product's DataTable: no fill, quiet title. */
        /* The row the page was opened for — an environment's View agent. */
        bgcolor: highlight ? "action.hover" : "transparent",
        cursor: onClick ? "pointer" : "default",
        "&:hover": onClick ? { bgcolor: "action.hover" } : {},
      }}
    >
      {cells.map((c, i) => (
        <Typography
          // eslint-disable-next-line react/no-array-index-key
          key={i}
          component="div"
          noWrap
          sx={{
            typography: "s2",
            ...(header && { fontSize: 13 }),
            fontWeight: header ? 500 : 400,
            color: header ? "text.secondary" : "text.primary",
          }}
        >
          {c}
        </Typography>
      ))}
    </Box>
  );
}
Row.propTypes = {
  cells: PropTypes.array,
  header: PropTypes.bool,
  onClick: PropTypes.func,
  highlight: PropTypes.bool,
};

function Pill({ children, dot }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      color={dot ? "error" : "default"}
      label={children}
      sx={{ flexShrink: 0 }}
    />
  );
}
Pill.propTypes = { children: PropTypes.node, dot: PropTypes.string };

/* A sealed copy for a live Vapi or Retell assistant: import it with the code
   behind its tools, so they answer from the seeded world. */
function SealedCopyOffer({ providerName, onSave }) {
  const [on, setOn] = useState(false);
  const [files, setFiles] = useState([]);
  return (
    <Stack spacing={1}>
      <SandboxImport
        providerName={providerName}
        on={on}
        onToggle={(v) => {
          setOn(v);
          if (!v) setFiles([]);
        }}
        files={files}
        onFiles={setFiles}
      />
      {on && (
        <Box>
          <Button
            size="small"
            variant="contained"
            disabled={!files.length}
            onClick={() =>
              onSave(
                {
                  importedCopy: true,
                  toolCodeUploaded: true,
                  toolCodeFiles: files.map((f) => f.name),
                },
                "Testing a sealed copy — its tools answer from the seeded world.",
              )
            }
          >
            Use the sealed copy
          </Button>
        </Box>
      )}
    </Stack>
  );
}

SealedCopyOffer.propTypes = {
  providerName: PropTypes.string,
  onSave: PropTypes.func.isRequired,
};
