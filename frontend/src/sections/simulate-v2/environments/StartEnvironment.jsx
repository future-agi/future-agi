import PropTypes from "prop-types";
import CodeBlock from "src/components/code-block";
import { PYTHON_DEFINE, AGENT_PROMPT, MCP_ADD_CMD } from "./fromCode";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  ToggleButton,
  ToggleButtonGroup,
  Checkbox,
  Box,
  Stack,
  Typography,
  Button,
  TextField,
  IconButton,
  Tooltip,
  Tab,
  Tabs,
  Switch,
  MenuItem,
  Select,
} from "@mui/material";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import EnvironmentValues from "../agents/connect/EnvironmentValues";
import ConnectPreflight from "../agents/connect/ConnectPreflight";
import {
  credentialCheck,
  egressCheck,
  factCheck,
  parseEgress,
  usePreflight,
} from "../agents/connect/preflight";
import { useConnectBuild } from "../agents/connect/connectSubmit";
import { envValuesSourceOf } from "../_mock/envValues";
import { paths } from "src/routes/paths";
import { SectionCard } from "../components/primitives";
import { useSimStore, useConnectedAgents } from "../store";
import {
  agentSourceOf,
  sourceFromAgentRecord,
  agentRecordFromSource,
  PROVIDER_LABELS,
} from "../_mock/agentRegistry";
import AgentStateTags from "../agents/AgentStateTags";
import {
  ConnectModeContext,
  RuntimeContext,
  AgentNameContext,
  PrefillContext,
} from "./connectMode";
import LocalSteps from "./LocalSteps";
import ChipCard from "./ChipCard";
import EnvironmentsLanding from "./EnvironmentsLanding";
import PrebuiltWorldsView from "./PrebuiltWorldsView";
import SetupDrawer from "./SetupDrawer";
import { stampNewBatch } from "../_mock/addScenarios";
import { SOURCES } from "../_mock/scenarioProvenance";
import SideDrawer from "../components/SideDrawer";
import { worldTotal } from "./landingData";
import GithubRepoPicker from "../integrations/GithubRepoPicker";
import {
  gitHostOfUrl,
  hostRepoUrlOf,
  repoUrlOf,
  installedRepos,
} from "../_mock/integrations";
import DescribeFlowStep from "./intake/DescribeFlowStep";
import MyEnvironmentsTable from "./MyEnvironmentsTable";
import PersonaLibraryTab from "./personas/PersonaLibraryTab";
import JobsPage from "../jobs/JobsPage";

/* Jobs (package validation, training) is on hold until open product
   questions are answered — the tab and its links stay hidden. Flip to bring
   it back; the screen and its model are kept as built. */
const SHOW_JOBS = false;
import {
  PLATFORM_LOGOS,
  AGENT_TYPES,
  HOSTED_PLATFORMS_BY_TYPE,
  firstLive,
  isLivePlatform,
  COUNTRY_OPTIONS,
  COUNTRY_BY_ISO,
  PLATFORM_KEY_SCOPE,
} from "./connectOptions";
import {
  CloneScopeChoice,
  EndpointConsent,
  HintLine,
  HowWeCopyIt,
  RunLocationChoice,
} from "../agents/connect/replicationBits";
import {
  canCloneProvider,
  keyLacksCreateScope,
  webhookUrlFor,
} from "../_mock/replication";
import { isSecretRef, keyRefOf } from "../_mock/secretRefs";
import SecretRefField from "../agents/connect/SecretRefField";
import {
  CLOSED_PLATFORMS,
  CLOSED_PLATFORM_NOTE,
  PLATFORM_TESTING,
} from "../_mock/rsiModel";
import { clickable, focusRing } from "../components/clickable";
import WorldAgentPicker from "./AgentChoice";
import {
  buildableAgentsOf,
  mcpWorldSource,
  useBuildForAgent,
} from "./worldSource";
import { gatewayServerForHost, gatewayServersOf } from "../_mock/gatewayMcp";
import { mcpServerHost } from "../_mock/mcpServers";

/**
 * Variation B entry point.
 *
 * The old front door dropped users straight into a modality → source-kind
 * flow that assumed "I already have an agent I want to test." That excluded
 * people who wanted a template, a hosted platform, an MCP server, or nothing
 * at all. This screen inverts it — it asks how you want to start, then
 * reveals the setup for that path inline underneath. No route change on
 * pick, no full-page swap, no lost context.
 *
 * The 8 options came from the manager's list and cover every way an agent
 * can reach the platform (or the intentional "no agent yet" case).
 */
export default function StartEnvironment({ entry = false }) {
  const navigate = useNavigate();
  const { state } = useSimStore();
  const [choice, setChoice] = useState(null);
  /* What the landing screen already knew when a setup opened — and where
     the pick was made, so the panel opens right under it. */
  const [picked, setPicked] = useState(null);
  /* What the Build tab shows: the landing or every prebuilt world.
     Deep-linkable via ?view= (an old ?view=journey lands on the landing). */
  const [view, setViewState] = useState(() => {
    const v = new URLSearchParams(window.location.search).get("view");
    return v === "worlds" ? v : "landing";
  });
  const examplesRef = useRef(null);
  const buildScrollRef = useRef(null);
  const setView = (v) => {
    setViewState(v);
    setPicked(null);
    setChoice(null);
    const url = new URL(window.location.href);
    if (v === "landing") url.searchParams.delete("view");
    else url.searchParams.set("view", v);
    window.history.replaceState(window.history.state, "", url);
    buildScrollRef.current?.scrollTo?.({ top: 0 });
  };
  /* Entry-page tabs: "my" — the environments, or, before there are any,
     the way to build one — and "personas", the workspace persona library.
     Building is a drawer once environments exist (the header's Build
     environment); before that it *is* the My environments tab. An old
     ?tab=build link opens the drawer. Deep-linkable via ?tab=. */
  const initialParams = new URLSearchParams(window.location.search);
  const [tab, setTabState] = useState(() =>
    (SHOW_JOBS ? ["personas", "jobs"] : ["personas"]).includes(
      initialParams.get("tab"),
    )
      ? initialParams.get("tab")
      : "my",
  );
  const setTab = (t) => {
    setTabState(t);
    const url = new URL(window.location.href);
    if (t === "my") url.searchParams.delete("tab");
    else url.searchParams.set("tab", t);
    window.history.replaceState(window.history.state, "", url);
  };
  const envCount = (state?.myEnvironments || []).length;
  const hasEnvs = envCount > 0;
  const [buildOpen, setBuildOpen] = useState(
    () =>
      initialParams.get("tab") === "build" ||
      initialParams.get("view") === "worlds",
  );
  const openBuild = () => {
    if (!hasEnvs) {
      setTab("my");
      return;
    }
    setView("landing");
    setBuildOpen(true);
  };
  const closeBuild = () => {
    setBuildOpen(false);
    setPicked(null);
    setChoice(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("view");
    if (url.searchParams.get("tab") === "build") url.searchParams.delete("tab");
    window.history.replaceState(window.history.state, "", url);
  };
  const panelRef = useRef(null);
  const pickerRef = useRef(null);

  const pickFromLanding = (
    id,
    prefill = null,
    from = "directory",
    itemId = null,
    meta = null,
  ) => {
    setChoice(id);
    setPicked({ choice: id, prefill, from, itemId, meta, at: Date.now() });
    /* Landing picks open in a drawer — nothing to scroll to. */
  };
  /* Back from Agents → New agent (`?agent=<id>`): the agent was connected to
     build an environment, so the build starts straight away — the version in
     production and the default scenario count. More scenarios can be added on
     the environment; no second "pick the agent" step. */
  const { agents: knownAgents } = useConnectedAgents();
  /* Also set by the landing's own connect drawer, once its agent is saved. */
  const [pendingAgent, setPendingAgent] = useState(() =>
    new URLSearchParams(window.location.search).get("agent"),
  );
  /* How many scenarios the connect form asked for — the build generates that many. */
  const [pendingCount, setPendingCount] = useState(
    () =>
      Number(new URLSearchParams(window.location.search).get("count")) || null,
  );
  useEffect(() => {
    const id = pendingAgent;
    if (!id || !entry) return;
    const a = knownAgents.find((x) => x.id === id);
    if (!a) return;
    setPendingAgent(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("agent");
    url.searchParams.delete("count");
    window.history.replaceState(null, "", url.toString());
    const count = pendingCount;
    setPendingCount(null);
    const built = {
      ...sourceFromAgentRecord(a),
      scenarioCount: count || scenarioCountOf(DEFAULT_SCENARIOS),
      connectedAgent: a,
      agentVersion: a.production || a.latest,
    };
    navigate(paths.dashboard.simulate.environmentBuild, {
      state: { presetSource: { ...built, envName: envNameFor(built) } },
    });
  }, [pendingAgent, knownAgents, entry]); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (id) => {
    setChoice(id);
    /* Reveal → scroll the panel into a comfortable read position. Timeout
       gives React one paint to render the panel before we scroll to it. */
    setTimeout(() => {
      panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
  };

  const { agents: connectedAgents } = useConnectedAgents();
  /* Agents already connected (on the Agents page or by another environment)
     can be reused as-is — no credentials to re-enter. */
  const bringYourAgent = BRING_ORDER.map((id) =>
    OPTIONS.find((o) => o.id === id),
  )
    .filter((o) => o && (o.id !== "connected" || connectedAgents.length > 0))
    .map((o) =>
      o.id === "connected"
        ? { ...o, preview: connectedAgents.slice(0, 3).map((a) => a.name) }
        : o,
    );
  const bringYourWorld = WORLD_ORDER.map((id) =>
    OPTIONS.find((o) => o.id === id),
  ).filter(Boolean);

  /* When entry=true, the outer box becomes a full-height flex column so
     the My Environments tab's table + pagination follow the Datasets
     pattern — pagination pinned to the viewport's bottom, not floating
     under the last row. Non-entry callers keep the original block
     layout so their scratch panels flow naturally. */
  const rootSx = entry
    ? {
        height: "100%",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        overflow: "hidden",
      }
    : { p: 2 };

  /* How to start: paste, a prebuilt world, or connect the agent — or every
     prebuilt world. Shown in the empty My environments tab, else in the
     Build environment drawer. A pick opens its setup in a drawer on top. */
  const chooser = (inDrawer) => (
    <>
      {view === "worlds" && (
        <PrebuiltWorldsView onBack={() => setView("landing")} bare={inDrawer} />
      )}
      {view === "landing" && (
        <EnvironmentsLanding
          onPick={pickFromLanding}
          onAgentConnected={(id, count) => {
            setPendingCount(Number(count) || null);
            setPendingAgent(id);
          }}
          onBrowseWorlds={() => setView("worlds")}
          examplesRef={examplesRef}
          picked={picked}
        />
      )}
      <SetupDrawer
        open={!!picked}
        onClose={() => {
          setPicked(null);
          setChoice(null);
        }}
        meta={picked?.meta}
        subtitle={
          picked
            ? OPTIONS.find((o) => o.id === picked.choice)?.setupSubtitle ||
              OPTIONS.find((o) => o.id === picked.choice)?.blurb
            : null
        }
      >
        {picked && (
          <FlowPanel
            key={`${picked.choice}-${picked.at}`}
            choice={picked.choice}
            prefill={picked.prefill}
            bare
          />
        )}
      </SetupDrawer>
    </>
  );

  return (
    <Box sx={rootSx}>
      <PageHeader
        sx={{
          px: entry ? 3 : 0,
          pt: entry ? 2 : 0,
          mb: entry ? 1.5 : 2.5,
          flexShrink: 0,
        }}
        title={entry ? "Environments" : "New environment"}
        description={
          entry
            ? tab === "personas"
              ? "Reusable callers for voice and chat agents. Use writes an environment's tasks in a persona's voice."
              : tab === "jobs"
                ? "Long background work on your environments — package validation and training — with its stage, checkpoints and where its evidence came from."
                : "Build a simulated world for your agent, then run callers against it and read the failures."
            : "Every environment needs a world to simulate in. Start by telling us where the agent lives — or start blank if you don't have one yet."
        }
        back={
          !entry ? (
            <Tooltip arrow title="Back to environments">
              <IconButton
                size="small"
                onClick={() => navigate(paths.dashboard.simulate.environments)}
                sx={{ mt: 0.25 }}
              >
                <Iconify icon="solar:alt-arrow-left-linear" width={16} />
              </IconButton>
            </Tooltip>
          ) : null
        }
        actions={
          entry && (hasEnvs || tab !== "my") ? (
            /* One click to a new environment from either tab: a drawer over
               the list once environments exist; before that the way to build
               is My environments itself, so from Personas it goes there. */
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={openBuild}
              startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
            >
              Build environment
            </Button>
          ) : entry ? (
            /* No environments yet: the tab below is the builder — and the
               way to start from a description instead of an agent. */
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() =>
                pickFromLanding("scratch", null, "header", null, SCRATCH_META)
              }
              startIcon={
                <Iconify icon="solar:document-add-linear" width={14} />
              }
            >
              Build from scratch
            </Button>
          ) : (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => pick("scratch")}
              startIcon={
                <Iconify icon="solar:document-add-linear" width={14} />
              }
            >
              Start from scratch
            </Button>
          )
        }
      />

      {/* Tabs — visible only on the entry page. Sit under the header
          and above the picker content. Indicator line reads white in
          dark mode via text.primary so it matches the page chrome. */}
      {entry && (
        <Box
          sx={{
            px: 3,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab
              value="my"
              disableRipple
              label={
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  component="span"
                >
                  <span>My environments</span>
                  <Box
                    component="span"
                    sx={{
                      typography: "s2",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {envCount}
                  </Box>
                </Stack>
              }
              sx={TAB_SX}
            />
            <Tab value="personas" disableRipple label="Personas" sx={TAB_SX} />
            {SHOW_JOBS && (
              <Tab value="jobs" disableRipple label="Jobs" sx={TAB_SX} />
            )}
          </Tabs>
        </Box>
      )}

      {/* Building, before any environment exists: the My environments tab is
          the builder, in place of an empty table. */}
      {entry && tab === "my" && !hasEnvs && (
        <Box
          ref={buildScrollRef}
          sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 3, pt: 2.5 }}
        >
          {chooser(false)}
        </Box>
      )}

      {/* Once there are environments, the same builder opens over the list. */}
      {entry && (
        <SideDrawer
          open={buildOpen && hasEnvs}
          onClose={closeBuild}
          width={BUILD_DRAWER_WIDTH}
        >
          {buildOpen && hasEnvs && (
            <Box
              sx={{
                height: "100%",
                display: "flex",
                flexDirection: "column",
                minHeight: 0,
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.5}
                sx={{
                  px: 3,
                  py: 2,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  flexShrink: 0,
                }}
              >
                {/* Browsing every world: the header becomes that page, with the way back. */}
                {view === "worlds" && (
                  <IconButton
                    size="small"
                    onClick={() => setView("landing")}
                    aria-label="Back to build environment"
                  >
                    <Iconify icon="solar:alt-arrow-left-linear" width={20} />
                  </IconButton>
                )}
                <Box flex={1} minWidth={0}>
                  <Typography sx={{ typography: "m3", fontWeight: 600 }}>
                    {view === "worlds" ? (
                      <>
                        Prebuilt worlds
                        <Box
                          component="span"
                          sx={{
                            color: "text.secondary",
                            fontWeight: 500,
                            ml: 0.75,
                          }}
                        >
                          {WORLD_COUNT}
                        </Box>
                      </>
                    ) : (
                      "Build environment"
                    )}
                  </Typography>
                  <Typography
                    noWrap
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {view === "worlds"
                      ? "Tables, tools and rules, as designed. Try one now with its sample agent, or use it with one of your agents."
                      : "Every environment belongs to one agent — pick yours, or connect one, then start from its own world or one of these."}
                  </Typography>
                </Box>
                {/* No agent yet, or rather start from the world: describe it
                    and connect the agent later. */}
                {view === "landing" && (
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    onClick={() =>
                      pickFromLanding(
                        "scratch",
                        null,
                        "header",
                        null,
                        SCRATCH_META,
                      )
                    }
                    startIcon={
                      <Iconify icon="solar:document-add-linear" width={14} />
                    }
                    sx={{ flexShrink: 0 }}
                  >
                    Build from scratch
                  </Button>
                )}
                <IconButton
                  size="small"
                  onClick={closeBuild}
                  aria-label="Close"
                >
                  <Iconify
                    icon="solar:close-linear"
                    width={20}
                    sx={{ color: "text.secondary" }}
                  />
                </IconButton>
              </Stack>
              <Box
                ref={buildScrollRef}
                sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 3, pt: 2.5 }}
              >
                {chooser(true)}
              </Box>
            </Box>
          )}
        </SideDrawer>
      )}

      {!entry && (
        <Box>
          <Stack ref={pickerRef} spacing={2.5}>
            <Box
              sx={{
                display: "grid",
                gap: 1.5,
                gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
              }}
            >
              <TemplateHeroCard
                option={OPTIONS.find((o) => o.id === "templates")}
                selected={false}
                onClick={() =>
                  navigate(paths.dashboard.simulate.environmentTemplates)
                }
              />
              <ClonesHeroCard
                onClick={() =>
                  navigate(paths.dashboard.simulate.environmentNewTwin)
                }
              />
            </Box>
            <Box>
              <Stack
                direction="row"
                alignItems="baseline"
                spacing={1.5}
                sx={{ mb: 1.5 }}
              >
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    letterSpacing: 0.4,
                    textTransform: "none",
                  }}
                >
                  Or connect your own agent
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  We work with what you already have — no rewrite, no adapter.
                </Typography>
              </Stack>
              <Box
                sx={{
                  display: "grid",
                  gap: 1.25,
                  gridTemplateColumns: {
                    xs: "1fr",
                    sm: "1fr 1fr",
                    md: "repeat(3, 1fr)",
                  },
                }}
              >
                {bringYourAgent.map((opt) => (
                  <OptionCard
                    key={opt.id}
                    option={opt}
                    selected={choice === opt.id}
                    onClick={() => pick(opt.id)}
                  />
                ))}
              </Box>
            </Box>
            {/* Not agents: an MCP server is the world's tools, a local runtime is
                where the world runs. Only rendered without `entry`, which no
                page mounts today — the live paths are a world source of an
                agent's build (prebuilt world → On your machine). */}
            <Box>
              <Stack
                direction="row"
                alignItems="baseline"
                spacing={1.5}
                sx={{ mb: 1.5 }}
              >
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    letterSpacing: 0.4,
                    textTransform: "none",
                  }}
                >
                  Or bring your world
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  Tools or a runtime you already have — connect the agent
                  afterwards.
                </Typography>
              </Stack>
              <Box
                sx={{
                  display: "grid",
                  gap: 1.25,
                  gridTemplateColumns: {
                    xs: "1fr",
                    sm: "1fr 1fr",
                    md: "repeat(3, 1fr)",
                  },
                }}
              >
                {bringYourWorld.map((opt) => (
                  <OptionCard
                    key={opt.id}
                    option={opt}
                    selected={choice === opt.id}
                    onClick={() => pick(opt.id)}
                  />
                ))}
              </Box>
            </Box>
          </Stack>

          {choice && (
            <Box ref={panelRef} sx={{ mt: 2.5 }}>
              <FlowPanel choice={choice} />
            </Box>
          )}
        </Box>
      )}

      {entry && tab === "personas" && <PersonaLibraryTab />}
      {SHOW_JOBS && entry && tab === "jobs" && <JobsPage embedded />}

      {entry && tab === "my" && hasEnvs && (
        /* Datasets-page pattern — flex column that fills the remaining
           viewport height so DataTablePagination inside
           MyEnvironmentsTable pins to the bottom of the screen rather
           than floating right under the last row. */
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            px: 3,
            pb: 2,
          }}
        >
          <MyEnvironmentsTable
            withToolbar
            envs={state?.myEnvironments || []}
            onOpen={(env) =>
              navigate(paths.dashboard.simulate.environmentDetail(env.id))
            }
          />
        </Box>
      )}
    </Box>
  );
}

/* Shown beside "Prebuilt worlds" in the drawer header. */
const WORLD_COUNT = worldTotal();

/* Wide enough for the connect directory's four columns. */
const BUILD_DRAWER_WIDTH = 960;

const TAB_SX = {
  minWidth: 0,
  px: 0.75,
  /* The theme puts 40px after every tab but the last, on a selector that
     outranks a plain `mr` — match it to bring the three closer together. */
  "&:not(:last-of-type)": { mr: 2.5 },
  color: "text.secondary",
  "&.Mui-selected": { color: "text.primary" },
};
StartEnvironment.propTypes = { entry: PropTypes.bool };

/* ── hero + card ───────────────────────────────────────────────────────── */

const HERO_TEMPLATES = [
  "Customer Support Line",
  "Coding",
  "Browser",
  "Airline Rebooking",
];
const HERO_CLONES = ["Slack", "Notion", "Gmail", "Salesforce", "Linear"];

function HeroCard({
  icon,
  title,
  tag,
  description,
  chips,
  moreLabel,
  selected,
  onClick,
}) {
  return (
    <Stack
      {...clickable(onClick, { pressed: selected })}
      spacing={1.5}
      sx={{
        ...focusRing,
        position: "relative",
        p: 1.5,
        borderRadius: "8px",
        cursor: "pointer",
        border: "1px solid",
        borderColor: selected ? "text.primary" : "divider",
        bgcolor: selected ? "action.hover" : "background.paper",
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: selected ? undefined : "text.disabled",
        },
      }}
    >
      <Stack direction="row" spacing={1.5} alignItems="center">
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: "6px",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            bgcolor: "action.selected",
            color: "text.primary",
          }}
        >
          <Iconify icon={icon} width={16} />
        </Box>
        <Box minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s2_1", fontWeight: 600 }}>
              {title}
            </Typography>
            {tag && (
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  letterSpacing: 0.4,
                  textTransform: "none",
                  color: "text.subtitle",
                }}
              >
                · {tag}
              </Typography>
            )}
          </Stack>
        </Box>
      </Stack>
      <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1 }}>
        {description}
      </Typography>
      <Stack
        direction="row"
        spacing={0.5}
        useFlexGap
        flexWrap="wrap"
        alignItems="center"
        sx={{
          pt: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {chips.map((name) => (
          <Box
            key={name}
            sx={{
              px: 1,
              py: 0.5,
              borderRadius: "4px",
              border: "1px solid",
              borderColor: "divider",
              bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
              typography: "s3",
              fontWeight: 600,
            }}
          >
            {name}
          </Box>
        ))}
        {moreLabel && (
          <Box
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.subtitle",
              px: 0.5,
            }}
          >
            {moreLabel}
          </Box>
        )}
      </Stack>
      {selected && (
        <Iconify
          icon="solar:check-circle-bold"
          width={16}
          sx={{
            position: "absolute",
            top: 10,
            right: 10,
            color: "text.primary",
          }}
        />
      )}
    </Stack>
  );
}
HeroCard.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.node,
  tag: PropTypes.node,
  description: PropTypes.node,
  chips: PropTypes.arrayOf(PropTypes.string),
  moreLabel: PropTypes.node,
  selected: PropTypes.bool,
  onClick: PropTypes.func,
};

function TemplateHeroCard({ option, selected, onClick }) {
  return (
    <HeroCard
      icon={option.icon}
      title={option.title}
      tag="Fastest"
      description={
        "Prebuilt worlds with seeded state, tools, and rules. Pick one, then wire your agent — you'll be running scenarios in under a minute."
      }
      chips={HERO_TEMPLATES}
      moreLabel="+ 10 more"
      selected={selected}
      onClick={onClick}
    />
  );
}
TemplateHeroCard.propTypes = {
  option: PropTypes.object,
  selected: PropTypes.bool,
  onClick: PropTypes.func,
};

function ClonesHeroCard({ onClick }) {
  return (
    <HeroCard
      icon="solar:copy-linear"
      title="Web Environments"
      tag="Live SaaS sandboxes"
      description={
        "Your agent calls the real SDKs — Slack, Notion, Salesforce — but the calls land in a sandbox we own, seeded to your prompt and torn down between runs."
      }
      chips={HERO_CLONES}
      moreLabel="+ 8 more"
      onClick={onClick}
    />
  );
}
ClonesHeroCard.propTypes = { onClick: PropTypes.func };

export function OptionCard({ option, selected, onClick }) {
  return (
    <Stack
      {...clickable(onClick, { pressed: selected })}
      spacing={1.5}
      sx={{
        ...focusRing,
        position: "relative",
        p: 1.5,
        borderRadius: "8px",
        cursor: "pointer",
        border: "1px solid",
        borderColor: selected ? "text.primary" : "divider",
        bgcolor: selected ? "action.hover" : "background.paper",
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: selected ? undefined : "text.disabled",
        },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: "6px",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            bgcolor: "action.selected",
            color: "text.primary",
          }}
        >
          <Iconify icon={option.icon} width={16} />
        </Box>
        <Typography
          sx={{ typography: "s2_1", fontWeight: 600, lineHeight: 1.2 }}
        >
          {option.title}
        </Typography>
      </Stack>
      <Typography
        sx={{
          typography: "s3",
          color: "text.subtitle",
          lineHeight: 1.45,
          flex: 1,
        }}
      >
        {option.blurb}
      </Typography>
      {option.preview && (
        <Box
          sx={{
            pt: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
            {option.preview.map((item) => (
              <Typography
                key={item}
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.subtitle",
                  px: 0.75,
                  py: 0.25,
                  borderRadius: "4px",
                  bgcolor: "action.selected",
                }}
              >
                {item}
              </Typography>
            ))}
          </Stack>
        </Box>
      )}
      {selected && (
        <Iconify
          icon="solar:check-circle-bold"
          width={16}
          sx={{
            position: "absolute",
            top: 10,
            right: 10,
            color: "text.primary",
          }}
        />
      )}
    </Stack>
  );
}
OptionCard.propTypes = {
  option: PropTypes.object,
  selected: PropTypes.bool,
  onClick: PropTypes.func,
};

const CONNECT_AGENT_OPTIONS = [
  "hosted",
  "running",
  "phone",
  "source",
  "upload",
];
/* The environment picker's order — the same agent options, in the same order. */
const BRING_ORDER = ["connected", ...CONNECT_AGENT_OPTIONS];
/* Not agents — listed apart, and built with no agent attached. */
const WORLD_ORDER = ["mcp", "local"];

/* The second half of an agent: a repository or bundle says what the agent
   is; this says where it runs. */
function ConnectExtras({ choice }) {
  const connect = useContext(ConnectModeContext);
  if (!connect) return null;
  if (choice === "source" || choice === "upload") {
    return (
      <WhereItRuns
        choice={choice}
        runtime={connect.runtime}
        setRuntime={connect.setRuntime}
      />
    );
  }
  return null;
}
ConnectExtras.propTypes = { choice: PropTypes.string };

/* An endpoint or a hosted agent says where it runs; a linked repository lets
   us read what it is. Same field, same words, wherever it's offered. */
function LinkedRepoField({ value, onChange }) {
  return (
    <Field
      label="Linked repository (optional)"
      mono
      placeholder="https://github.com/your-org/your-agent"
      value={value}
      onChange={onChange}
      helper="Lets Sync see prompt and tool changes."
    />
  );
}
LinkedRepoField.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
};

/* How an agent read from code is copied and where it runs (Story 6): built
   from the code in our hosted sandbox, or run locally through the SDK — then
   the address serve() answers on, optional until the first run. */
function WhereItRuns({ choice, runtime, setRuntime }) {
  /* The hosted sandbox by default — running locally through the SDK is Coming soon. */
  const location = runtime.location || "hosted";
  const agent = {
    sourceKind: choice === "source" ? "repo" : "upload",
    values: { runLocation: location },
  };
  return (
    <Stack spacing={1.25}>
      <HowWeCopyIt agent={agent} />
      <Box
        sx={{
          px: 1.75,
          py: 1.5,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 1 }}>
          Where it runs
        </Typography>
        <RunLocationChoice
          agent={agent}
          value={location}
          onChange={(loc) => setRuntime((r) => ({ ...r, location: loc }))}
          dense
        />
        {location === "sdk" ? (
          <Stack spacing={1.25} sx={{ mt: 1.5 }}>
            <Field
              label="Where the SDK serves it (optional for now)"
              mono
              placeholder="https://api.yourapp.com/agent/step"
              value={runtime.endpoint}
              onChange={(v) => setRuntime((r) => ({ ...r, endpoint: v }))}
              helper={
                <HintLine hint="Local works — it dials out. Leave it empty and add it before the first run.">
                  The address serve() answers on.
                </HintLine>
              }
            />
            <SecretRefField
              label="Auth token (optional)"
              placeholder="vault://acme/sim/agent_endpoint_token"
              value={runtime.tokenRef || ""}
              onChange={(v) => setRuntime((r) => ({ ...r, tokenRef: v }))}
            />
          </Stack>
        ) : (
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 1.25 }}
          >
            {choice === "source"
              ? "Built from the branch above each run · nothing to deploy"
              : "Built from the uploaded bundle each run · nothing to deploy"}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
WhereItRuns.propTypes = {
  choice: PropTypes.string,
  runtime: PropTypes.object,
  setRuntime: PropTypes.func,
};

/* ── flow panel router ─────────────────────────────────────────────────── */

export function FlowPanel({ choice, prefill = null, bare = false }) {
  const connect = useContext(ConnectModeContext);
  const opt = OPTIONS.find((o) => o.id === choice);
  const Body = PANELS[choice] || PanelNotImplemented;
  /* Building an environment from code asks where the agent runs too. */
  const [runtime, setRuntime] = useState({ endpoint: "", tokenRef: "" });
  const runtimeCtx = useMemo(
    () =>
      !connect && (choice === "source" || choice === "upload")
        ? { choice, runtime, setRuntime }
        : null,
    [connect, choice, runtime],
  );
  /* Named the same way in both modes. A connected agent already has a name;
     an MCP server or a local runtime isn't an agent. */
  const [ownName, setOwnName] = useState("");
  const naming = useMemo(
    () =>
      connect
        ? { name: connect.name, setName: connect.setName }
        : { name: ownName, setName: setOwnName },
    [connect, ownName],
  );
  const asksName = CONNECT_AGENT_OPTIONS.includes(choice);
  return (
    <Shell
      bare={bare}
      title={opt.title}
      subtitle={opt.setupSubtitle || opt.blurb}
    >
      {asksName && (
        <Box sx={{ px: 2.5, pt: 2.5 }}>
          <Field
            label="Agent name"
            placeholder="Optional — e.g. Returns line agent"
            value={naming.name}
            onChange={naming.setName}
          />
        </Box>
      )}
      <AgentNameContext.Provider value={asksName ? naming : null}>
        <PrefillContext.Provider value={prefill}>
          {connect ? (
            <ConnectModeContext.Provider
              value={{ ...connect, extra: <ConnectExtras choice={choice} /> }}
            >
              <Body />
            </ConnectModeContext.Provider>
          ) : (
            <RuntimeContext.Provider value={runtimeCtx}>
              <Body />
            </RuntimeContext.Provider>
          )}
        </PrefillContext.Provider>
      </AgentNameContext.Provider>
    </Shell>
  );
}
/* In a drawer the panel needs no card of its own — the drawer is the frame. */
function Shell({ bare, title, subtitle, children }) {
  return bare ? (
    <Box>{children}</Box>
  ) : (
    <SectionCard title={title} subtitle={subtitle}>
      {children}
    </SectionCard>
  );
}
Shell.propTypes = {
  bare: PropTypes.bool,
  title: PropTypes.node,
  subtitle: PropTypes.node,
  children: PropTypes.node,
};
FlowPanel.propTypes = {
  choice: PropTypes.string,
  prefill: PropTypes.object,
  bare: PropTypes.bool,
};

/* ── build handler shared across panels ──────────────────────────────── */

/*
  Every "Build environment" button funnels through this hook. It hands
  the source object off to the /environments/new/build route where
  BuildFromAgent picks it up (via location.state.presetSource), mounts
  the chat + derivation view, and streams the build. The env itself is
  minted downstream by BuildFromAgent's own adopt flow — this hook
  intentionally does NOT dispatch adoptEnvironment so we don't create
  a half-built row that the chat is still filling in.
*/
function useBuildEnvironment() {
  const navigate = useNavigate();
  const connect = useContext(ConnectModeContext);
  const rt = useContext(RuntimeContext);
  const naming = useContext(AgentNameContext);
  return (source) => {
    if (connect) {
      connect.onConnect(source);
      return;
    }
    const runLocation = rt ? rt.runtime.location || "hosted" : null;
    const endpoint = runLocation === "sdk" ? rt.runtime.endpoint.trim() : "";
    /* Only a reference travels; a half-typed one is dropped, never sent. */
    const tokenRef =
      rt && isSecretRef(rt.runtime?.tokenRef) ? rt.runtime.tokenRef.trim() : "";
    const typedName = naming?.name.trim();
    const built = {
      ...source,
      ...(runLocation && { runLocation }),
      ...(endpoint && { runtime: { endpoint, ...(tokenRef && { tokenRef }) } }),
      ...(typedName && { name: typedName }),
    };
    navigate(paths.dashboard.simulate.environmentBuild, {
      state: { presetSource: { ...built, envName: envNameFor(built) } },
    });
  };
}

/* A new environment is named after the agent it tests — or, with no agent,
   after the world it was built from. Renamed in place on the build screen. */
function envNameFor(source) {
  if (source.kind === "mcp") {
    let host = source.value.split(/\s+/)[0];
    try {
      host = new URL(source.value).hostname;
    } catch {
      /* a stdio command */
    }
    return `${host} environment`;
  }
  if (source.kind === "local") return "Local environment";
  const agent =
    source.name ||
    source.agentName ||
    source.connectedAgent?.name ||
    (source.kind === "platform"
      ? source.agentMode === "prompt"
        ? "Prompt agent"
        : `${PROVIDER_LABELS[source.provider] || "Hosted"} agent`
      : source.kind === "upload"
        ? source.entry || source.files?.[0]?.name || "Uploaded agent"
        : agentRecordFromSource(source).name);
  return `${agent} environment`;
}

/* ── panels ────────────────────────────────────────────────────────────── */

function PanelConnectedAgent() {
  const build = useBuildEnvironment();
  const prefill = useContext(PrefillContext);
  /* A template's stand-in isn't anyone's agent to build for. */
  const { agents: all } = useConnectedAgents();
  const agents = all.filter((a) => !a.placeholder);
  const [scenarioCount, setScenarioCount] = useState(DEFAULT_SCENARIOS);
  const [agentId, setAgentId] = useState(() =>
    prefill?.agentId && agents.some((a) => a.id === prefill.agentId)
      ? prefill.agentId
      : agents[0]?.id || null,
  );
  const agent = agents.find((a) => a.id === agentId);
  const [version, setVersion] = useState(null);
  /* The version in production by default — not a draft nobody released. */
  const chosenVersion =
    version && agent?.versions.some((v) => v.label === version)
      ? version
      : agent?.production || agent?.latest;
  return (
    /* Wide (the guided setup): agents on the left in their own scroll,
       version, scenarios and Build on the right. Narrow (a drawer): stacked. */
    <Box sx={{ p: 2.5, containerType: "inline-size" }}>
      <Box
        sx={{
          display: "grid",
          gap: 2.5,
          gridTemplateColumns: "minmax(0, 1fr)",
          "@container (min-width: 760px)": {
            gridTemplateColumns: "minmax(0, 1fr) 300px",
            alignItems: "start",
            "& .continue-row": {
              mx: 0,
              px: 0,
              pt: 0,
              borderTop: "none",
              position: "static",
              flexWrap: "wrap",
              rowGap: 0.75,
            },
            "& .continue-row > .MuiButton-root": {
              flex: 1,
              ml: "0 !important",
            },
            /* The row's empty spacer (no hint to show) takes no room here. */
            "& .continue-row > .MuiBox-root:empty": { display: "none" },
          },
        }}
      >
        <Box minWidth={0}>
          <Typography
            sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}
          >{`Agent · ${agents.length}`}</Typography>
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              maxHeight: 340,
              overflowY: "auto",
              p: 0.75,
            }}
          >
            <Stack spacing={0.5}>
              {agents.map((a) => {
                const src = agentSourceOf(a);
                const on = a.id === agentId;
                return (
                  <Stack
                    key={a.id}
                    direction="row"
                    alignItems="center"
                    spacing={1.25}
                    {...clickable(
                      () => {
                        setAgentId(a.id);
                        setVersion(null);
                      },
                      { role: "radio", checked: on },
                    )}
                    sx={{
                      ...focusRing,
                      px: 1.25,
                      py: 1,
                      borderRadius: "4px",
                      cursor: "pointer",
                      border: "1px solid",
                      borderColor: on ? "text.primary" : "transparent",
                      bgcolor: on ? "action.selected" : "transparent",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Iconify
                      icon={src.icon}
                      width={16}
                      sx={{ color: "text.secondary", flexShrink: 0 }}
                    />
                    <Box flex={1} minWidth={0}>
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={0.75}
                        sx={{ minWidth: 0 }}
                      >
                        <Typography
                          noWrap
                          sx={{
                            typography: "s2",
                            fontWeight: 600,
                            minWidth: 0,
                          }}
                        >
                          {a.name}
                        </Typography>
                        <AgentStateTags agent={a} />
                      </Stack>
                      <Typography
                        noWrap
                        sx={{ typography: "s3", color: "text.subtitle" }}
                      >
                        {`${src.label} · ${a.versions.length} version${a.versions.length === 1 ? "" : "s"} · ${a.usedIn.length ? `used in ${a.usedIn.length} environment${a.usedIn.length === 1 ? "" : "s"}` : "not in an environment yet"}`}
                      </Typography>
                    </Box>
                    {on && (
                      <Iconify
                        icon="solar:check-circle-bold"
                        width={16}
                        sx={{ color: "text.primary" }}
                      />
                    )}
                  </Stack>
                );
              })}
            </Stack>
          </Box>
        </Box>

        <Stack spacing={1.75} minWidth={0}>
          {agent && (
            <Box>
              <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
                Version to test
              </Typography>
              <Select
                size="small"
                fullWidth
                value={chosenVersion}
                onChange={(e) => setVersion(e.target.value)}
                sx={{ typography: "s2" }}
              >
                {[...agent.versions].reverse().map((v) => (
                  <MenuItem
                    key={v.label}
                    value={v.label}
                    sx={{ typography: "s2" }}
                  >
                    {`${v.label}${v.pendingDraft ? " · draft" : v.label === agent.production ? " · in production" : ""}`}
                  </MenuItem>
                ))}
              </Select>
            </Box>
          )}
          <ScenarioCountRow value={scenarioCount} onChange={setScenarioCount} />
          <ContinueRow
            disabled={!agent}
            hint="Pick an agent"
            onClick={() =>
              build({
                ...sourceFromAgentRecord(agent),
                scenarioCount: scenarioCountOf(scenarioCount),
                connectedAgent: agent,
                agentVersion: chosenVersion,
              })
            }
          />
        </Stack>
      </Box>
    </Box>
  );
}

function PanelSourceRepo() {
  const build = useBuildEnvironment();
  const connect = useContext(ConnectModeContext);
  const naming = useContext(AgentNameContext);
  const preset = connect?.preset;
  /* A repository pasted on the landing screen: picked straight from the
     GitHub App when it can see it, otherwise typed in by hand. */
  const pasted = useContext(PrefillContext)?.repo || null;
  /* A GitLab or Bitbucket repository pasted on the landing screen. */
  const pastedHost = useContext(PrefillContext)?.host || "github";
  const pastedVisible =
    !!pasted &&
    pastedHost === "github" &&
    installedRepos().some((r) => r.name.toLowerCase() === pasted.toLowerCase());
  const [scenarioCount, setScenarioCount] = useState(DEFAULT_SCENARIOS);
  const [provider, setProvider] = useState(pastedHost);
  /* From GitHub (pick a repository, we find the agents) or typed by hand. */
  const [mode, setMode] = useState(
    (pasted && !pastedVisible) || pastedHost !== "github" ? "manual" : "github",
  );
  const [ghRepo, setGhRepo] = useState(
    preset?.repo || (pastedVisible ? pasted : ""),
  );
  const [ghAgent, setGhAgent] = useState(preset?.agent || null);
  const [repo, setRepo] = useState(pasted && !pastedVisible ? pasted : "");
  const [branch, setBranch] = useState("main");
  const [entry, setEntry] = useState(preset?.agent?.entry || "");
  useEffect(() => {
    if (preset?.agent && naming && !naming.name)
      naming.setName(preset.agent.name);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const viaGithub = mode === "github";
  const canGo = viaGithub ? !!(ghRepo && ghAgent) : !!repo.trim();
  const pick = ({ repo: r, agent }) => {
    setGhRepo(r);
    setGhAgent(agent);
    setEntry(agent?.entry || "");
    if (agent && naming) naming.setName(agent.name);
  };
  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      {viaGithub ? (
        <GithubRepoPicker
          repo={ghRepo}
          agentId={ghAgent?.id}
          onPick={pick}
          onManual={(prefill) => {
            setMode("manual");
            if (prefill?.repo) setRepo(prefill.repo);
          }}
        />
      ) : (
        <>
          <ProviderRow
            options={REPO_PROVIDERS}
            value={provider}
            onChange={setProvider}
          />
          <Field
            label="Repository"
            required
            placeholder="owner/repo"
            value={repo}
            onChange={setRepo}
            mono
            helper="We read it so scenarios track your real tools."
          />
          <Typography
            {...clickable(() => setMode("github"))}
            sx={{
              ...focusRing,
              typography: "s3",
              color: "text.subtitle",
              cursor: "pointer",
              mt: -1,
              width: "fit-content",
              "&:hover": { color: "text.primary", textDecoration: "underline" },
            }}
          >
            Pick from GitHub instead
          </Typography>
        </>
      )}
      <Stack direction="row" spacing={1.5}>
        <Field
          label="Branch or tag"
          value={branch}
          onChange={setBranch}
          mono
          fullWidth
        />
        <Field
          label="Entry path (optional)"
          placeholder="src/agent/index.ts"
          value={entry}
          onChange={setEntry}
          mono
          fullWidth
        />
      </Stack>
      <ScenarioCountRow value={scenarioCount} onChange={setScenarioCount} />
      <ContinueRow
        disabled={!canGo}
        hint={
          viaGithub
            ? ghRepo
              ? "Choose the agent"
              : "Choose a repository"
            : "Add a repository"
        }
        onClick={() =>
          build({
            kind: "repo",
            scenarioCount: scenarioCountOf(scenarioCount),
            provider: viaGithub ? "github" : provider,
            /* Another host's "owner/repo" is that host's URL, never GitHub's. */
            value: viaGithub
              ? repoUrlOf(ghRepo)
              : provider !== "github" && !gitHostOfUrl(repo)
                ? hostRepoUrlOf(provider, repo.trim())
                : repo.trim(),
            gitHost: viaGithub ? "github" : provider,
            ...(viaGithub && {
              githubApp: true,
              agentName: ghAgent?.name,
              framework: ghAgent?.framework,
            }),
            ref: branch.trim() || "main",
            entry: entry.trim(),
          })
        }
      />
    </Stack>
  );
}

/* Where an endpoint's reply sits in its response — a JSON path. */
const REPLY_PATH = /^\$(\.[A-Za-z_][\w-]*|\[\d+\])+$/;
const ENDPOINT_URL = {
  http: /^https?:\/\/\S+$/i,
  websocket: /^wss?:\/\/\S+$/i,
};

/*
  An agent endpoint (Phase 2): we can only call it, over HTTP or a
  WebSocket, so it needs where the reply sits in its response next to the
  URL and the token reference. It can't be copied — simulations drive the
  real agent once someone says so.
*/
function PanelRunningAgent() {
  const prefill = useContext(PrefillContext);
  const build = useBuildEnvironment();
  const [scenarioCount, setScenarioCount] = useState(DEFAULT_SCENARIOS);
  const [endpoint, setEndpoint] = useState(prefill?.endpoint || "");
  const [transport, setTransport] = useState(prefill?.transport || "http");
  /* Where the agent's reply text sits in what the endpoint sends back. */
  const [replyPath, setReplyPath] = useState("");
  /* The endpoint's auth token, by reference (AC-6.12). */
  const [tokenRef, setTokenRef] = useState("");
  /* Agent type up front, the same chips as Hosted platform. Voice asks for
     call direction; chat may override the system prompt — tucked away, since
     the endpoint alone is enough for most agents. */
  const [agentType, setAgentType] = useState("chat");
  const [callDirection, setCallDirection] = useState("inbound");
  const [showPrompt, setShowPrompt] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [linkedRepo, setLinkedRepo] = useState("");
  /* An endpoint can't be watched from outside — the team adds tracing.
     Asking here means the agent page shows how straight away. */
  /* An agent made by an environment build isn't linked to an Observe
     project here — that's set on the agent, in Finish setting up. */
  const watchProduction = false;
  /* An endpoint can't be copied, so simulations drive the real agent — not
     until someone says so (AC-1.12). { acknowledgedAt, webhookAt } */
  const [consent, setConsent] = useState(null);
  const pseudo = {
    sourceKind: "endpoint",
    key: `endpoint:${endpoint.trim().toLowerCase()}`,
    values: { endpoint: endpoint.trim(), endpointConsent: consent },
  };

  const tokenOk = !tokenRef.trim() || isSecretRef(tokenRef);
  const urlOk = ENDPOINT_URL[transport].test(endpoint.trim());
  const pathOk = REPLY_PATH.test(replyPath.trim());
  const canGo = urlOk && pathOk && !!consent?.acknowledgedAt && tokenOk;
  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        We can only call it, so improvements end in a suggested fix.
      </Typography>
      <Box>
        <Label>Agent type</Label>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
          {AGENT_TYPES.map((t) => (
            <ChipCard
              key={t.id}
              icon={t.icon}
              label={t.label}
              comingSoon={t.comingSoon}
              on={agentType === t.id}
              onClick={() => setAgentType(t.id)}
            />
          ))}
        </Box>
      </Box>
      <Box>
        <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
          Speaks
        </Typography>
        <SegmentedToggle
          value={transport}
          onChange={setTransport}
          options={[
            { value: "http", label: "HTTP" },
            { value: "websocket", label: "WebSocket" },
          ]}
        />
      </Box>
      <Stack
        direction={{ xs: "column", md: "row" }}
        spacing={1.5}
        alignItems={{ md: "flex-start" }}
      >
        <Box sx={{ flex: 1.4, minWidth: 0 }}>
          <Field
            label={transport === "websocket" ? "WebSocket URL" : "Endpoint URL"}
            required
            placeholder={
              transport === "websocket"
                ? "wss://api.yourapp.com/agent/stream"
                : "https://api.yourapp.com/agent/step"
            }
            value={endpoint}
            onChange={setEndpoint}
            mono
            helper={
              endpoint.trim() && !urlOk
                ? transport === "websocket"
                  ? "A WebSocket URL starts with wss:// or ws://"
                  : "An HTTP endpoint starts with https:// or http://"
                : transport === "websocket"
                  ? "One socket per conversation; each turn is sent on it."
                  : "Every simulated turn POSTs here."
            }
          />
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Field
            label="Where the reply sits"
            required
            placeholder="$.output.text"
            value={replyPath}
            onChange={setReplyPath}
            mono
            helper={
              replyPath.trim() && !pathOk
                ? "A JSON path, like $.output.text or $.messages[0].content"
                : "The JSON path to the agent's reply."
            }
          />
        </Box>
      </Stack>
      <SecretRefField
        label="Auth token (optional)"
        placeholder="vault://acme/sim/agent_endpoint_token"
        value={tokenRef}
        onChange={setTokenRef}
      />
      {agentType === "voice" && (
        <Box>
          <Label>Call direction</Label>
          <Box
            sx={{
              display: "grid",
              gap: 0.75,
              gridTemplateColumns: "1fr 1fr",
              mt: 0.75,
            }}
          >
            <ChipCard
              label="Inbound — we call your agent"
              on={callDirection === "inbound"}
              onClick={() => setCallDirection("inbound")}
            />
            <ChipCard
              label="Outbound — your agent dials us"
              on={callDirection === "outbound"}
              onClick={() => setCallDirection("outbound")}
            />
          </Box>
        </Box>
      )}
      {agentType === "chat" && (
        <Box>
          <Button
            size="small"
            onClick={() => setShowPrompt((o) => !o)}
            startIcon={
              <Iconify
                icon={
                  showPrompt
                    ? "solar:alt-arrow-up-linear"
                    : "solar:alt-arrow-down-linear"
                }
                width={12}
              />
            }
            sx={{ color: "text.secondary" }}
          >
            {showPrompt
              ? "Hide system prompt override"
              : "System prompt override (optional)"}
          </Button>
          {showPrompt && (
            <Box sx={{ mt: 1 }}>
              <Field
                label=""
                placeholder="Leave blank to use whatever your endpoint already does."
                value={systemPrompt}
                onChange={setSystemPrompt}
                multiline
              />
            </Box>
          )}
        </Box>
      )}
      <LinkedRepoField value={linkedRepo} onChange={setLinkedRepo} />
      {endpoint.trim() && (
        <>
          <HowWeCopyIt agent={pseudo} />
          <EndpointConsent
            value={consent}
            onChange={setConsent}
            webhookUrl={webhookUrlFor(pseudo)}
          />
        </>
      )}
      <ScenarioCountRow value={scenarioCount} onChange={setScenarioCount} />
      <ContinueRow
        disabled={!canGo}
        hint={
          !endpoint.trim()
            ? "Add the endpoint"
            : !urlOk
              ? `Use a ${transport === "websocket" ? "wss://" : "https://"} address`
              : !pathOk
                ? "Say where the reply sits"
                : !tokenOk
                  ? "Use a reference for the auth token"
                  : "Acknowledge that simulations drive the real agent"
        }
        onClick={() =>
          build({
            kind: "endpoint",
            scenarioCount: scenarioCountOf(scenarioCount),
            value: endpoint.trim(),
            transport,
            replyPath: replyPath.trim(),
            ...(tokenRef.trim() && tokenOk && { tokenRef: tokenRef.trim() }),
            agentType,
            ...(agentType === "voice" ? { callDirection } : {}),
            ...(agentType === "chat"
              ? { systemPrompt: systemPrompt.trim() || null }
              : {}),
            ...(linkedRepo.trim() && { linkedRepo: linkedRepo.trim() }),
            observability: watchProduction,
            endpointConsent: consent,
          })
        }
      />
    </Stack>
  );
}

function PanelHostedPlatform() {
  const build = useBuildEnvironment();
  const prefill = useContext(PrefillContext);
  const [scenarioCount, setScenarioCount] = useState(DEFAULT_SCENARIOS);
  /* Type gate first — the platform roster only makes sense once we know
     what kind of agent is being connected. Default to voice because
     that's what the codebase actually integrates with today. */
  const [agentType, setAgentType] = useState(prefill?.agentType || "voice");
  const platforms = HOSTED_PLATFORMS_BY_TYPE[agentType] || [];
  const [platform, setPlatform] = useState(
    prefill?.platform || firstLive(platforms)?.id || "",
  );
  const [id, setId] = useState(prefill?.agentId || "");
  /* The provider key by reference (AC-6.12) — a path in the secret manager. */
  const [keyRef, setKeyRef] = useState("");
  const [linkedRepo, setLinkedRepo] = useState("");
  /* Voice contact details — how the test call actually reaches the
     agent. `simMode` = web (WebRTC, no telephony provider) or phone
     (PSTN, needs a country code + number). `inboundCalls` mirrors the
     old callDirection binary: on = agent takes inbound, off = agent
     dials out. `agentSpeaksFirst` tells the simulator to wait for the
     agent's greeting before replying. */
  const [simMode, setSimMode] = useState("web");
  const [countryIso, setCountryIso] = useState("US");
  const [contactNumber, setContactNumber] = useState("");
  const [inboundCalls, setInboundCalls] = useState(true);
  const [agentSpeaksFirst, setAgentSpeaksFirst] = useState(false);
  /* Watch the agent's real calls too — on by default, since the provider
     key is already here and production is what tells you whether the
     simulations were the right ones. */
  /* An agent made by an environment build isn't linked to an Observe
     project here — that's set on the agent, in Finish setting up. */
  const watchProduction = false;
  /* Others-only: a pasted system prompt. We run it against the LLM
     directly — no platform, no endpoint — and seed scenarios from it. */
  const [otherPrompt, setOtherPrompt] = useState("");

  /* When agent type flips, the current platform selection is likely
     stale. Snap to the first platform of the new type and clear the
     credentials so we do not carry an OpenAI key into a Retell form. */
  const pickAgentType = (nextType) => {
    setAgentType(nextType);
    setPlatform(firstLive(HOSTED_PLATFORMS_BY_TYPE[nextType] || [])?.id || "");
    setId("");
    setKeyRef("");
    setLinkedRepo("");
    setOtherPrompt("");
  };

  const chosen =
    platforms.find((p) => p.id === platform && isLivePlatform(p)) ||
    firstLive(platforms);
  const isOther = !!chosen?.isOther;
  /* A phone number only when the test call goes over the phone. */
  const phoneRequired = agentType === "voice" && simMode === "phone";
  const phoneOk = !phoneRequired || !!contactNumber.trim();
  /* What's recorded is a reference — the one typed, or (demo) the platform's slot. */
  const savedRef = keyRefOf(
    keyRef,
    chosen ? `vault://acme/sim/${chosen.id}_api_key` : "",
  );
  const refOk = !!savedRef;
  const credsOk = isOther ? !!otherPrompt.trim() : !!id.trim() && refOk;
  /* A Vapi or Bland key without create scope can't make the clone. */
  const [scopeChoice, setScopeChoice] = useState(null);
  const lacksScope =
    !isOther &&
    !!chosen &&
    refOk &&
    canCloneProvider(chosen.id) &&
    keyLacksCreateScope(keyRef);
  /* ElevenLabs branches need versioning switched on — by the user, once. */
  const [versioningOn, setVersioningOn] = useState(false);
  const versioningOk =
    !PLATFORM_TESTING[chosen?.id]?.needsVersioning || versioningOn;
  const canGo =
    !!chosen &&
    credsOk &&
    phoneOk &&
    versioningOk &&
    (!lacksScope || scopeChoice === "mocked");
  const pseudo = isOther
    ? {
        sourceKind: "prompt",
        definition: { kind: "prompt" },
        values: { provider: "other" },
      }
    : {
        sourceKind: "platform",
        values: {
          provider: chosen?.id,
          agentId: id.trim(),
          ...(lacksScope &&
            scopeChoice === "mocked" && { replicationMode: "exported_mocked" }),
        },
      };

  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <Box>
        <Label>Agent type</Label>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
          {AGENT_TYPES.map((t) => (
            <ChipCard
              key={t.id}
              icon={t.icon}
              label={t.label}
              comingSoon={t.comingSoon}
              on={agentType === t.id}
              onClick={() => pickAgentType(t.id)}
            />
          ))}
        </Box>
      </Box>
      <Box>
        <Label>Platform</Label>
        {platforms.length === 0 ? (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}>
            None for this agent type yet — try Source repository.
          </Typography>
        ) : (
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
            {platforms.map((p) => (
              <ChipCard
                key={p.id}
                /* "Others" has no brand logo; render the Solar icon instead
                   so it doesn't collapse to a coloured monogram tile. */
                icon={p.isOther ? p.icon : undefined}
                logo={
                  p.isOther ? undefined : (
                    <PlatformLogo
                      id={p.id}
                      name={p.name}
                      brand={p.brand}
                      icon={p.icon}
                    />
                  )
                }
                /* Wordmark logos already spell the name — don't repeat it. */
                label={
                  PLATFORM_LOGOS[p.id]?.type === "wordmark" ? null : p.name
                }
                comingSoon={p.comingSoon}
                on={chosen?.id === p.id}
                onClick={() => setPlatform(p.id)}
              />
            ))}
          </Box>
        )}
      </Box>
      {chosen && (
        <>
          {isOther ? (
            <Field
              label="System prompt"
              required
              placeholder="You are a friendly returns agent for Acme…"
              value={otherPrompt}
              onChange={setOtherPrompt}
              multiline
              helper={
                <HintLine hint="We run the prompt against the LLM directly and seed matching scenarios from it.">
                  No endpoint or API key needed.
                </HintLine>
              }
            />
          ) : (
            <>
              <Field
                label={chosen.idLabel || "Agent ID"}
                required
                placeholder={chosen.idPlaceholder}
                value={id}
                onChange={setId}
                mono
                helper={
                  chosen.idNote ? <NoteLine text={chosen.idNote} /> : null
                }
              />
              <SecretRefField
                label={chosen.keyLabel || "API key"}
                required
                placeholder={`vault://acme/sim/${chosen.id}_api_key`}
                value={keyRef}
                onChange={(v) => {
                  setKeyRef(v);
                  setScopeChoice(null);
                }}
                helper={
                  <PlatformKeyScope
                    prototypeHint={canCloneProvider(chosen.id)}
                  />
                }
              />
              {lacksScope && (
                <CloneScopeChoice
                  providerName={chosen.name}
                  choice={scopeChoice}
                  onReissue={() => {
                    setKeyRef("");
                    setScopeChoice(null);
                  }}
                  onMocked={() => setScopeChoice("mocked")}
                />
              )}
              <PlatformTestingStep
                provider={chosen.id}
                versioningOn={versioningOn}
                onVersioningOn={setVersioningOn}
              />
            </>
          )}
          {agentType === "voice" && (
            <ContactInformation
              mode={simMode}
              onMode={setSimMode}
              countryIso={countryIso}
              onCountryIso={setCountryIso}
              contactNumber={contactNumber}
              onContactNumber={setContactNumber}
              inboundCalls={inboundCalls}
              onInboundCalls={setInboundCalls}
              agentSpeaksFirst={agentSpeaksFirst}
              onAgentSpeaksFirst={setAgentSpeaksFirst}
            />
          )}
          {/* A pasted prompt has no production to watch, and is its own
              instructions — only a hosted agent links a repository. */}
          {!isOther && (
            <LinkedRepoField value={linkedRepo} onChange={setLinkedRepo} />
          )}
        </>
      )}
      {chosen && credsOk && <HowWeCopyIt agent={pseudo} aside={null} />}
      <ScenarioCountRow value={scenarioCount} onChange={setScenarioCount} />
      <ContinueRow
        disabled={!canGo}
        hint={
          !chosen
            ? "Pick a platform above"
            : lacksScope && credsOk
              ? "Choose how to go on without create scope"
              : !versioningOk
                ? "Switch versioning on for the agent first"
                : !phoneOk
                  ? "Add a contact number for telephony simulation"
                  : isOther
                    ? "Paste the agent's system prompt"
                    : !id.trim()
                      ? "Add the agent ID"
                      : "Add the key's reference"
        }
        onClick={() =>
          build({
            kind: "platform",
            scenarioCount: scenarioCountOf(scenarioCount),
            agentType,
            provider: chosen?.id,
            ...(isOther
              ? { agentMode: "prompt", prompt: otherPrompt.trim() }
              : {
                  agentId: id.trim(),
                  apiKeyRef: savedRef,
                  observability: watchProduction,
                  ...(lacksScope &&
                    scopeChoice === "mocked" && {
                      replicationMode: "exported_mocked",
                    }),
                  ...(versioningOn &&
                    PLATFORM_TESTING[chosen?.id]?.needsVersioning && {
                      versioningOnAt: new Date().toISOString(),
                    }),
                }),
            ...(!isOther &&
              linkedRepo.trim() && { linkedRepo: linkedRepo.trim() }),
            ...(agentType === "voice"
              ? {
                  callDirection: inboundCalls ? "inbound" : "outbound",
                  contact: {
                    mode: simMode,
                    inboundCalls,
                    agentSpeaksFirst,
                    ...(simMode === "phone"
                      ? {
                          countryIso,
                          countryCode: COUNTRY_BY_ISO[countryIso]?.dial || "",
                          number: contactNumber.trim(),
                        }
                      : {}),
                  },
                }
              : {}),
          })
        }
      />
    </Stack>
  );
}

/*
  What testing on this platform needs from the user, said where the platform
  is picked (PRD Phase 1 and 2): Retell's live numbers are pinned before
  drafts; ElevenLabs needs versioning switched on — which can't be undone,
  so the user does it; Bland tests its new agent model by branch.
*/
export function PlatformTestingStep({
  provider,
  versioningOn,
  onVersioningOn,
}) {
  const t = PLATFORM_TESTING[provider];
  if (!t) return null;
  /* One line — how it's tested and how it ships; the why sits on hover. */
  const detail = (
    <Box sx={{ typography: "s3" }}>
      {t.note}
      {t.needsPinnedNumbers && (
        <Box sx={{ mt: 0.5 }}>
          After connecting, the Connections tab asks you to pin your live
          numbers.
        </Box>
      )}
      <Box sx={{ mt: 0.5, opacity: 0.8 }}>{t.released}</Box>
    </Box>
  );
  return (
    <Box
      sx={{
        px: 1.75,
        py: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Typography
          sx={{ typography: "s2", fontWeight: 600 }}
        >{`Tested on ${t.brief}`}</Typography>
        <Typography
          sx={{ typography: "s2", color: "text.secondary" }}
        >{`· ships as ${t.ships.charAt(0).toLowerCase()}${t.ships.slice(1)}`}</Typography>
        <Box flex={1} />
        <Tooltip arrow title={detail}>
          <Box
            component="span"
            sx={{
              display: "inline-flex",
              color: "text.disabled",
              cursor: "help",
            }}
          >
            <Iconify icon="solar:info-circle-linear" width={14} />
          </Box>
        </Tooltip>
      </Stack>
      {t.needsVersioning && (
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          role="checkbox"
          aria-checked={!!versioningOn}
          tabIndex={0}
          onClick={() => onVersioningOn?.(!versioningOn)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onVersioningOn?.(!versioningOn);
            }
          }}
          sx={{ mt: 1, cursor: "pointer", width: "fit-content" }}
        >
          <Checkbox
            size="small"
            checked={versioningOn}
            tabIndex={-1}
            disableRipple
            inputProps={{ "aria-hidden": true }}
            sx={{ p: 0, flexShrink: 0 }}
          />
          <Box>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              I&apos;ve switched versioning on for this agent
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Agent → Settings → Versioning. It can&apos;t be undone.
            </Typography>
          </Box>
        </Stack>
      )}
    </Box>
  );
}
PlatformTestingStep.propTypes = {
  provider: PropTypes.string,
  versioningOn: PropTypes.bool,
  onVersioningOn: PropTypes.func,
};

/*
  A phone number (Phase 2): we dial it, with the pasted instructions as the
  reference for what the agent should do. We can only call it, so it's test
  only — an improvement ends with a suggested fix. Closed platforms (Sierra,
  Decagon, Intercom Fin, Zendesk) come in this way, or by their chat endpoint.
*/
function PanelPhoneNumber() {
  const prefill = useContext(PrefillContext);
  const build = useBuildEnvironment();
  const [scenarioCount, setScenarioCount] = useState(DEFAULT_SCENARIOS);
  const [countryIso, setCountryIso] = useState("US");
  const [number, setNumber] = useState(prefill?.number || "");
  const [instructions, setInstructions] = useState("");
  const [closed, setClosed] = useState(null);
  /* One prompt, or a series of steps the way Bland, Retell and Vapi
     workflows are written — each step its own instructions and where it
     goes next. Steps come in as the agent's members. */
  const [shape, setShape] = useState("prompt");
  const [steps, setSteps] = useState(() => [
    { id: "s1", name: "Greet and identify", instructions: "", next: "" },
    { id: "s2", name: "", instructions: "", next: "" },
  ]);
  const editStep = (id, patch) =>
    setSteps((list) =>
      list.map((st) => (st.id === id ? { ...st, ...patch } : st)),
    );
  const stepsOk =
    steps.length >= 2 &&
    steps.every((st) => st.name.trim() && st.instructions.trim());
  const scriptOf = () =>
    steps
      .map(
        (st, i) =>
          `## Step ${i + 1} · ${st.name.trim()}\n${st.instructions.trim()}${st.next.trim() ? `\nThen: ${st.next.trim()}` : ""}`,
      )
      .join("\n\n");
  const written = shape === "steps" ? stepsOk : !!instructions.trim();
  const dial = COUNTRY_BY_ISO[countryIso]?.dial || "+1";
  const digits = number.replace(/[^\d]/g, "");
  const pseudo = {
    sourceKind: "endpoint",
    values: { phoneNumber: number.trim(), countryCode: dial },
    definition: written ? { kind: "prompt" } : { kind: "none" },
  };
  const canGo = digits.length >= 6 && written;
  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <Typography
        component="div"
        sx={{ typography: "s3", color: "text.subtitle" }}
      >
        <HintLine hint="We can only call it, so we can't change it ourselves.">
          Test only — improvements end in a suggested fix.
        </HintLine>
      </Typography>
      <Box>
        <Label>Phone number</Label>
        <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
          <Select
            size="small"
            value={countryIso}
            onChange={(e) => setCountryIso(e.target.value)}
            renderValue={(iso) =>
              `${COUNTRY_BY_ISO[iso]?.flag || ""} ${COUNTRY_BY_ISO[iso]?.dial || ""}`
            }
            sx={{ width: 110, typography: "s2" }}
            MenuProps={{ PaperProps: { sx: { maxHeight: 320 } } }}
          >
            {COUNTRY_OPTIONS.map((c) => (
              <MenuItem
                key={c.iso}
                value={c.iso}
                sx={{ typography: "s2", gap: 1 }}
              >
                <Box component="span">{c.flag}</Box>
                <Box component="span" sx={{ flex: 1 }}>
                  {c.name}
                </Box>
                <Box component="span" sx={{ color: "text.subtitle" }}>
                  {c.dial}
                </Box>
              </MenuItem>
            ))}
          </Select>
          <TextField
            size="small"
            fullWidth
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            placeholder="415 555 0142"
            InputProps={{
              sx: {
                typography: "s2",
                fontFamily: "ui-monospace, Menlo, monospace",
              },
            }}
          />
        </Stack>
      </Box>
      <Box>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ mb: 0.75 }}
        >
          <Label>How it&apos;s written</Label>
          <SegmentedToggle
            value={shape}
            onChange={setShape}
            options={[
              { value: "prompt", label: "One prompt" },
              { value: "steps", label: "Steps" },
            ]}
          />
        </Stack>
        {shape === "prompt" ? (
          <Field
            label="Its instructions"
            required
            multiline
            minRows={4}
            placeholder="Paste the agent's system prompt or the script it follows."
            value={instructions}
            onChange={setInstructions}
            helper={
              <HintLine hint="We don't change the agent; a re-run proves a fix once you've applied it.">
                Scenarios and the suggested fix are written against these.
              </HintLine>
            }
          />
        ) : (
          <Stack spacing={1}>
            {steps.map((st, i) => (
              <Box
                key={st.id}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  p: 1.25,
                }}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ mb: 1 }}
                >
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      color: "text.subtitle",
                      flexShrink: 0,
                    }}
                  >{`Step ${i + 1}`}</Typography>
                  <TextField
                    size="small"
                    fullWidth
                    value={st.name}
                    placeholder={
                      i === 0
                        ? "Greet and identify"
                        : "e.g. Book the appointment"
                    }
                    onChange={(e) => editStep(st.id, { name: e.target.value })}
                    InputProps={{ sx: { typography: "s2" } }}
                  />
                  {steps.length > 2 && (
                    <IconButton
                      size="small"
                      aria-label={`Remove step ${i + 1}`}
                      onClick={() =>
                        setSteps((list) => list.filter((x) => x.id !== st.id))
                      }
                    >
                      <Iconify
                        icon="solar:trash-bin-minimalistic-linear"
                        width={16}
                      />
                    </IconButton>
                  )}
                </Stack>
                <TextField
                  size="small"
                  fullWidth
                  multiline
                  minRows={2}
                  value={st.instructions}
                  placeholder="What the agent says and does in this step."
                  onChange={(e) =>
                    editStep(st.id, { instructions: e.target.value })
                  }
                  InputProps={{ sx: { typography: "s2" } }}
                />
                <TextField
                  size="small"
                  fullWidth
                  value={st.next}
                  sx={{ mt: 1 }}
                  placeholder={
                    i === steps.length - 1
                      ? "Then — e.g. end the call"
                      : `Then — e.g. go to step ${i + 2} once the caller is verified; otherwise transfer`
                  }
                  onChange={(e) => editStep(st.id, { next: e.target.value })}
                  InputProps={{ sx: { typography: "s3" } }}
                />
              </Box>
            ))}
            <Box>
              <Button
                size="small"
                variant="outlined"
                startIcon={<Iconify icon="solar:add-linear" width={14} />}
                onClick={() =>
                  setSteps((list) => [
                    ...list,
                    {
                      id: `s${Date.now()}`,
                      name: "",
                      instructions: "",
                      next: "",
                    },
                  ])
                }
                sx={{ color: "text.primary", borderColor: "divider" }}
              >
                Add step
              </Button>
            </Box>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Each step becomes one of the agent&apos;s members, so a suggested
              fix names the step it changes.
            </Typography>
          </Stack>
        )}
      </Box>
      <Box>
        <Label>
          <HintLine hint={CLOSED_PLATFORM_NOTE}>
            On a closed platform? (optional)
          </HintLine>
        </Label>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.75 }}>
          {CLOSED_PLATFORMS.map((c) => (
            <ChipCard
              key={c.id}
              label={c.label}
              on={closed === c.id}
              onClick={() => setClosed(closed === c.id ? null : c.id)}
            />
          ))}
        </Box>
      </Box>
      {canGo && <HowWeCopyIt agent={pseudo} />}
      <ScenarioCountRow value={scenarioCount} onChange={setScenarioCount} />
      <ContinueRow
        disabled={!canGo}
        hint={
          digits.length < 6
            ? "Add the number to call"
            : shape === "steps"
              ? "Name each step and say what it does"
              : "Paste its instructions"
        }
        onClick={() =>
          build({
            kind: "endpoint",
            phone: true,
            scenarioCount: scenarioCountOf(scenarioCount),
            agentType: "voice",
            value: `tel:${dial}${digits}`,
            phoneNumber: number.trim(),
            countryCode: dial,
            systemPrompt: shape === "steps" ? scriptOf() : instructions.trim(),
            ...(shape === "steps" && {
              members: steps.map((st, i) => ({
                id: `step-${i + 1}`,
                name: st.name.trim(),
                role: `Step ${i + 1}${st.next.trim() ? ` · then ${st.next.trim()}` : ""}`,
                instructions: st.instructions.trim(),
              })),
            }),
            callDirection: "inbound",
            ...(closed && { closedPlatform: closed }),
          })
        }
      />
    </Stack>
  );
}

/* An MCP server's tools are a world source, never an environment of their
   own: the agent the environment belongs to is picked (or connected) first,
   and its build starts from the server's tools. */
function PanelMcpServer() {
  const prefill = useContext(PrefillContext);
  const buildFor = useBuildForAgent();
  const { agents: all } = useConnectedAgents();
  const [agentId, setAgentId] = useState(null);
  const agent = buildableAgentsOf(all).find((a) => a.id === agentId) || null;
  const [scenarioCount, setScenarioCount] = useState(DEFAULT_SCENARIOS);
  /* Servers registered in the Gateway come first — no URL or token to give again.
     A pasted URL is the other way in. */
  const gwServers = gatewayServersOf();
  /* A pasted URL the Gateway already has opens on that Gateway server — its
     credentials are there, so nothing is asked twice. */
  const pastedInGateway = prefill?.target
    ? gatewayServerForHost(mcpServerHost(prefill.target))?.gateway?.serverId ||
      null
    : null;
  const [source, setSource] = useState(
    gwServers.length && (!prefill?.target || pastedInGateway)
      ? "gateway"
      : "url",
  );
  const [gwPick, setGwPick] = useState(pastedInGateway);
  const gw = gwServers.find((g) => g.id === gwPick) || null;
  const [transport, setTransport] = useState(
    prefill?.transport === "stdio" ? "http" : prefill?.transport || "http",
  );
  const [target, setTarget] = useState(
    prefill?.transport === "stdio" ? "" : prefill?.target || "",
  );
  /* A token for an HTTP server, by reference (AC-6.12). */
  const [tokenRef, setTokenRef] = useState("");
  const tokenOk = !tokenRef.trim() || isSecretRef(tokenRef);
  const canGo =
    !!agent && (source === "gateway" ? !!gw : !!target.trim() && tokenOk);
  /* A build runs in our sandbox, and a pasted command never runs there. */
  const transports = MCP_TRANSPORTS.map((t) =>
    t.id === "stdio"
      ? { ...t, disabled: true, tag: "On your machine only" }
      : t,
  );
  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <WorldAgentPicker
        value={agentId}
        onChange={setAgentId}
        subtitle="The server's tools become the world of this agent's environment."
      />
      {gwServers.length > 0 && (
        <SegmentedToggle
          value={source}
          onChange={setSource}
          options={[
            {
              value: "gateway",
              label: `From your Gateway · ${gwServers.length}`,
            },
            { value: "url", label: "Another server" },
          ]}
        />
      )}
      {source === "gateway" ? (
        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
          {gwServers.map((g) => (
            <ChipCard
              key={g.id}
              icon="solar:plug-circle-linear"
              label={g.label}
              on={gwPick === g.id}
              onClick={() => setGwPick(g.id)}
              tag={
                g.status === "connected"
                  ? `${g.toolCount} tools`
                  : "Unreachable"
              }
              disabled={g.status !== "connected"}
            />
          ))}
        </Stack>
      ) : (
        <>
          <ProviderRow
            options={transports}
            value={transport}
            onChange={setTransport}
          />
          <Field
            label="Server URL"
            required
            placeholder="https://mcp.yourapp.com/sse"
            value={target}
            onChange={setTarget}
            mono
            helper={
              <HintLine hint="We build the world from the server's tools. A stdio server only works on your machine.">
                Its tools become the world the agent acts on.
              </HintLine>
            }
          />
          <SecretRefField
            label="Auth token (optional)"
            placeholder="vault://acme/sim/mcp_server_token"
            value={tokenRef}
            onChange={setTokenRef}
          />
        </>
      )}
      <ScenarioCountRow value={scenarioCount} onChange={setScenarioCount} />
      <ContinueRow
        disabled={!canGo}
        hint={
          !agent
            ? "Pick the agent this environment is for"
            : source === "gateway"
              ? "Pick a server from your Gateway"
              : !target.trim()
                ? "Add the server"
                : "Use a reference for the auth token"
        }
        onClick={() =>
          buildFor(
            agent,
            {
              name: `${agent.name.replace(/\s+agent$/i, "")} environment`,
              version: agent.production || agent.latest,
              count:
                scenarioCountOf(scenarioCount) || Number(DEFAULT_SCENARIOS),
            },
            source === "gateway"
              ? mcpWorldSource({
                  transport: "http",
                  value: gw.url,
                  gateway: { serverId: gw.id },
                  label: gw.label,
                })
              : mcpWorldSource({
                  transport,
                  value: target.trim(),
                  tokenRef: tokenRef.trim() ? tokenRef.trim() : null,
                }),
          )
        }
      />
    </Stack>
  );
}

function PanelCodeUpload() {
  const build = useBuildEnvironment();
  /* Files dropped on New agent's upload area come along, so they're not dropped twice. */
  const dropped = useContext(PrefillContext)?.files || [];
  const [scenarioCount, setScenarioCount] = useState(DEFAULT_SCENARIOS);
  const [files, setFiles] = useState(dropped);
  const [entry, setEntry] = useState(dropped[0]?.name || "");
  /* What our sandbox needs to run the code, as for a repository. */
  const [envValues, setEnvValues] = useState([]);
  const [credFiles, setCredFiles] = useState([]);
  const [egressText, setEgressText] = useState("");
  const egress = parseEgress(egressText);
  const canGo = files.length > 0;
  /* Preflight before a build: the upload, every secret it's given, the egress list.
     Optional when the upload only connects the agent. */
  const connect = useContext(ConnectModeContext);
  const buildCtx = useConnectBuild();
  const needsPreflight = !connect || !!buildCtx;
  const preflight = usePreflight(
    [
      factCheck({
        id: "files",
        label: "Upload",
        ok: canGo,
        okText: `${files.length} file${files.length === 1 ? "" : "s"}${entry.trim() ? ` · entry ${entry.trim()}` : ""} — unpacked in the sandbox.`,
        failText: "Add at least one file.",
      }),
      ...envValues
        .filter((v) => v.secret)
        .map((v) =>
          credentialCheck({ id: `env:${v.name}`, label: v.name, ref: v.ref }),
        ),
      ...(egress.length ? [egressCheck(egress)] : []),
    ],
    [files.map((f) => f.name).join(","), entry, envValues, egressText],
  );

  const onDrop = (list) => {
    const added = Array.from(list).map((f) => ({ name: f.name, size: f.size }));
    setFiles((prev) => [...prev, ...added]);
    if (!entry && added[0]) setEntry(added[0].name);
  };

  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      <Box
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer?.files) onDrop(e.dataTransfer.files);
        }}
        sx={{
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
          p: 3,
          textAlign: "center",
        }}
      >
        <Iconify
          icon="solar:cloud-upload-linear"
          width={28}
          sx={{ color: "text.subtitle" }}
        />
        <Typography sx={{ typography: "s1", fontWeight: 600, mt: 1 }}>
          Drop code files or a zipped project
        </Typography>
        <Typography
          component="div"
          sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
        >
          <HintLine hint="The builder reads the code to derive tools, rules, and scenarios.">
            .py .ts .js .zip · up to 200 MB
          </HintLine>
        </Typography>
        <Button
          component="label"
          size="small"
          variant="outlined"
          sx={{ mt: 1.5 }}
        >
          Choose files
          <input
            hidden
            multiple
            type="file"
            onChange={(e) => e.target.files && onDrop(e.target.files)}
          />
        </Button>
      </Box>
      {files.length > 0 && (
        <Box>
          <Label>Uploaded ({files.length})</Label>
          <Stack spacing={0.5} sx={{ mt: 0.75 }}>
            {files.map((f, i) => (
              <Stack
                key={`${f.name}-${i}`}
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{
                  px: 1,
                  py: 0.75,
                  borderRadius: 1,
                  bgcolor: (th) => alpha(th.palette.text.primary, 0.04),
                }}
              >
                <Iconify
                  icon="solar:document-linear"
                  width={14}
                  sx={{ color: "text.subtitle" }}
                />
                <Typography
                  sx={{
                    typography: "s2",
                    fontFamily: "ui-monospace, Menlo, monospace",
                    flex: 1,
                    minWidth: 0,
                  }}
                  noWrap
                >
                  {f.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {formatSize(f.size)}
                </Typography>
                <IconButton
                  size="small"
                  onClick={() =>
                    setFiles((prev) => prev.filter((_, x) => x !== i))
                  }
                >
                  <Iconify
                    icon="solar:trash-bin-minimalistic-linear"
                    width={14}
                  />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        </Box>
      )}
      {files.length > 0 && (
        <Field
          label="Entry file"
          value={entry}
          onChange={setEntry}
          mono
          helper="Which file defines the agent's step function?"
        />
      )}
      {files.length > 0 && (
        <EnvironmentValues
          values={envValues}
          onValues={setEnvValues}
          files={credFiles}
          onFiles={setCredFiles}
          egress={egressText}
          onEgress={setEgressText}
        />
      )}
      <ScenarioCountRow value={scenarioCount} onChange={setScenarioCount} />
      <ConnectPreflight
        preflight={preflight}
        required={needsPreflight}
        canRun={canGo}
        blockedReason={
          !canGo
            ? "Add the files — then preflight checks them and their credentials."
            : null
        }
      />
      <ContinueRow
        disabled={!canGo || (needsPreflight && !preflight.ready)}
        hint={!canGo ? "Add at least one file" : "Run preflight first"}
        onClick={() =>
          build({
            kind: "upload",
            scenarioCount: buildCtx
              ? Number(buildCtx.scenarioCount) || 10
              : scenarioCountOf(scenarioCount),
            entry: entry.trim(),
            files: files.map((f) => ({ name: f.name, size: f.size })),
            ...envValuesSourceOf({ values: envValues, files: credFiles }),
            ...(egress.length && { allowedEgressDomains: egress }),
          })
        }
      />
    </Stack>
  );
}

function PanelScratch() {
  const prefill = useContext(PrefillContext);
  const navigate = useNavigate();
  const { dispatch } = useSimStore();
  const [name, setName] = useState("");
  const [flowText, setFlowText] = useState(prefill?.flowText || "");
  const [attachments, setAttachments] = useState([]);
  /* Name is optional — the intake describes the flow well enough on its
     own, so if the user skips the name field we derive a reasonable one
     from the description before building. Build is only gated on the
     intake itself being non-trivial. */
  const canGo = flowText.trim().length >= 10 || attachments.length > 0;

  /* Scratch flow diverges from the six connection panels — no agent
     source to derive from, so we mint a blank env in the store from the
     intake instead. Mirrors ScratchEnvironment.jsx in the other
     variation, minus the intermediate building animation. */
  const buildBlank = () => {
    if (!canGo) return;
    const envId = `env-scratch-${Date.now().toString(36)}`;
    const finalName = name.trim() || deriveScratchName(flowText, attachments);
    /* Derive the environment's world from the flow description so the
       review layout has real material to show — the user described
       what the agent does; we materialise that into rules, scenarios,
       and suggested evals. Tools stay empty because we intentionally
       have no agent connected yet; Overview shows its
       empty state and the way to connect one. */
    const derivedRules = deriveScratchRules(flowText);
    const derivedScenarios = deriveScratchScenarios(flowText, finalName);
    const derivedEvals = deriveScratchEvals(flowText);
    const env = {
      id: envId,
      agentType: null,
      name: finalName,
      /* A description of calls builds a voice world; anything else, chat. */
      surface: /\b(calls?|calling|phone|voice|dial)\b/i.test(flowText)
        ? "voice"
        : "chat",
      domain: "custom",
      tagline: attachments.length
        ? `Scratch-built · ${attachments.length} reference file${attachments.length === 1 ? "" : "s"}`
        : "Scratch-built",
      description:
        flowText.trim() ||
        "A scratch-built environment. Connect an agent from Overview to run simulations.",
      difficulty: "Balanced",
      popularity: 1,
      builtFrom: {
        kind: "scratch",
        intake: {
          flow: flowText.trim(),
          attachments: attachments.map((f) => ({
            name: f.name,
            size: f.size,
            type: f.type,
          })),
        },
      },
      custom: true,
      seed: { tables: [] },
      tools: [],
      rules: derivedRules,
      evalPreset: derivedEvals,
      buildStatus: "ready",
      starterScenarios: derivedScenarios,
    };
    const now = new Date().toISOString();
    dispatch({ type: "adoptEnvironment", env, now });
    /* Stamped like every other add: written by the system from the
       description just now, each checked against v1 — not back-filled with
       made-up authors, and not waiting on a re-check. */
    const batch = stampNewBatch(derivedScenarios, {
      env,
      envState: {},
      source: "derived",
      actor: SOURCES.derived.actor,
      at: now,
    });
    /* Seed the workspace envState with the derived scenarios only.
       Evals stay empty on purpose — the derived list lives on
       env.evalPreset and surfaces on the Evaluations tab under
       "Suggested evaluations". Added evaluations must be an
       explicit user action (either "Add" on a suggested row or the
       "Add evaluations" button), so we don't pre-fill envState.evals
       and make the choice for them. Agents stays empty too. */
    dispatch({
      type: "patchEnvState",
      envId,
      patch: {
        scenarios: batch.rows,
      },
    });
    /* Route to the scratch build-in-progress screen instead of straight
       to the workspace — user sees TemplateReviewLayout stream the
       stages (Understanding → Building world → Proving scenarios)
       before Finish setup ships them off to /environments/:envId. */
    navigate(paths.dashboard.simulate.environmentScratchBuild(envId));
  };

  const addFiles = (list) => {
    setAttachments((prev) => {
      const seen = new Set(prev.map((a) => a.id));
      return [...prev, ...list.filter((a) => !seen.has(a.id))];
    });
  };
  const removeAttachment = (id) =>
    setAttachments((prev) => prev.filter((a) => a.id !== id));

  return (
    <Stack spacing={2} sx={{ p: 2.5 }}>
      <Typography
        component="div"
        sx={{ typography: "s2", color: "text.subtitle" }}
      >
        <HintLine hint="We set up the world, personas and evals from your description. Connect the agent from Overview when it's ready — runs start once it is.">
          No agent yet? Fine — connect it later.
        </HintLine>
      </Typography>
      <Field
        label={
          <HintLine hint="Leave it blank and we'll name it from your description.">
            Environment name (optional)
          </HintLine>
        }
        placeholder="e.g. Support desk sandbox"
        value={name}
        onChange={setName}
      />
      <DescribeFlowStep
        step={0}
        total={1}
        prompt="Describe the flow, or drop files to learn from"
        description="Trigger, tools, hand-offs — or attach SOPs, CSVs or calls."
        placeholder="e.g. A customer emails a refund request. The agent looks up the order, checks the policy and drafts a reply. Over $500 it escalates to a human."
        chips={[
          "The agent gets triggered when…",
          "It reaches for these tools…",
          "It's done when…",
          "It escalates if…",
        ]}
        accept=".csv,.tsv,.json,.md,.txt,.pdf,.docx,.wav,.mp3,.m4a"
        value={flowText}
        onChange={setFlowText}
        files={attachments}
        onAdd={addFiles}
        onRemove={removeAttachment}
        onBack={null}
        onSkip={undefined}
        onNext={buildBlank}
        canSubmit={canGo}
        submitLabel="Build environment"
      />
    </Stack>
  );
}

/*
  Your own machine: the environment is built in the terminal, so the setup is
  the steps to run there — no Build button. The same commands as a prebuilt
  world's On your machine (TemplateBuildPanel): install, init for an agent,
  up. It shows up in My environments once the CLI connects.
*/
/* From code, three ways: the CLI (build it on this machine — unchanged),
   Python (define it with Environment(...) and run it), or the developer's
   coding agent over MCP. All three make the same kind of environment; it
   shows up in My environments, managed in code. */

function PanelBuildLocally() {
  const [mode, setMode] = useState("cli");
  const [os, setOs] = useState("mac");
  const install =
    os === "windows"
      ? "py -m pip install agent-learning-kit"
      : "pip install agent-learning-kit";
  const steps = [
    {
      title: "Install the CLI",
      body: "Once per machine — it adds agent-learn to your path. Then sign in with agent-learn auth login.",
      extra: (
        <Box sx={{ mb: 1 }}>
          <ProviderRow options={OS_OPTIONS} value={os} onChange={setOs} />
        </Box>
      ),
      cmd: install,
    },
    {
      title: "Register your agent and build for it",
      body: "Every environment belongs to one agent in Agents — register it, then build its environment.",
      hint: "--request says what to test; you review the proposed world and scenarios before it's saved.",
      cmd: 'agent-learn agent add my-agent --type process --command python --arg agent.py\nagent-learn env build my-environment --agent my-agent --request "Refunds and cancellations"',
    },
    {
      title: "Run it on your machine",
      body: "Runs your agent and the world locally; results stay on your machine until you export them.",
      hint: "--mode hybrid keeps your agent local and runs the world here instead.",
      cmd: "agent-learn run --env my-environment --mode local --attempts 3 --budget 100 --budget-unit credits",
    },
  ];
  return (
    <Box>
      <Box sx={{ px: 2.5, pt: 2.5 }}>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={mode}
          onChange={(_, v) => v && setMode(v)}
          aria-label="From code"
        >
          <ToggleButton value="cli">CLI</ToggleButton>
          <ToggleButton value="python">Python</ToggleButton>
          <ToggleButton value="agent">Your coding agent</ToggleButton>
        </ToggleButtonGroup>
      </Box>
      <Box sx={{ p: 2.5 }}>
        {mode === "cli" && <LocalSteps steps={steps} />}
        {mode === "python" && (
          <Stack spacing={1.5}>
            <Typography
              sx={{ fontSize: 13, lineHeight: "20px", color: "text.secondary" }}
            >
              Define the environment next to your agent and run it from Python.
              Install with pip install agent-learning-kit.
            </Typography>
            <CodeBlock code={PYTHON_DEFINE} />
            <Typography
              sx={{ fontSize: 12, lineHeight: "16px", color: "text.tertiary" }}
            >
              Every setting in the builder has a code form. Without push() it
              still runs; push() gives it versions and a place in My
              environments.
            </Typography>
          </Stack>
        )}
        {mode === "agent" && (
          <Stack spacing={2}>
            <LocalSteps
              steps={[
                {
                  title: "Add Future AGI to your coding agent",
                  body: "Claude Code shown; Cursor, VS Code and Windsurf are in Settings → MCP Server. Once per machine.",
                  cmd: MCP_ADD_CMD,
                },
              ]}
            />
            <Box>
              <Typography
                sx={{
                  fontSize: 13,
                  lineHeight: "20px",
                  fontWeight: 500,
                  mb: 0.75,
                }}
              >
                Then ask it, in your agent&apos;s repo
              </Typography>
              <CodeBlock code={AGENT_PROMPT} wrap />
            </Box>
            <Typography
              sx={{ fontSize: 12, lineHeight: "16px", color: "text.tertiary" }}
            >
              It reads your agent, writes the environment and scenarios next to
              your code, runs it and proposes fixes. What it builds shows up
              here.
            </Typography>
          </Stack>
        )}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2.5,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="solar:laptop-2-linear"
          width={14}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          {mode === "cli"
            ? "Once it's built, the environment shows up in My environments."
            : "It shows up in My environments, marked Managed in code."}
        </Typography>
      </Stack>
    </Box>
  );
}

function PanelNotImplemented() {
  return (
    <Box sx={{ p: 2.5 }}>
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        Coming soon.
      </Typography>
    </Box>
  );
}

/* ── shared bits ───────────────────────────────────────────────────────── */

export function Label({ children }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: 600,
        letterSpacing: 0.4,
        textTransform: "none",
        color: "text.subtitle",
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };

export function Field({
  label,
  required,
  value,
  onChange,
  placeholder,
  helper,
  mono,
  type,
  multiline,
  minRows,
  fullWidth,
}) {
  return (
    <Box sx={{ flex: fullWidth ? 1 : undefined }}>
      {label && (
        <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
          {label}
          {required && (
            <Box component="span" sx={{ color: "error.main", ml: 0.5 }}>
              *
            </Box>
          )}
        </Typography>
      )}
      <TextField
        fullWidth
        size="small"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        type={type}
        multiline={multiline}
        minRows={multiline ? minRows || 2 : undefined}
        sx={{
          "& .MuiInputBase-input": {
            typography: "s2",
            ...(mono && { fontFamily: "ui-monospace, Menlo, monospace" }),
          },
        }}
      />
      {helper && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
          {helper}
        </Typography>
      )}
    </Box>
  );
}
Field.propTypes = {
  label: PropTypes.node,
  required: PropTypes.bool,
  value: PropTypes.string,
  onChange: PropTypes.func,
  placeholder: PropTypes.string,
  helper: PropTypes.node,
  mono: PropTypes.bool,
  type: PropTypes.string,
  multiline: PropTypes.bool,
  minRows: PropTypes.number,
  fullWidth: PropTypes.bool,
};

/* Contact Information — how test calls actually reach the agent.
   Layout mirrors the prod v1 flow:
     • one header card: current mode + description on the left, a
       segmented Web/Phone toggle on the right.
     • when Phone: Country Code + Contact Number as two bordered
       inputs stacked side-by-side below.
     • Inbound Calls toggle row (agent takes inbound calls).
     • Agent speaks first toggle row.
   No section header — reads as a run of stacked cards, matching the
   rest of the form. */
export function ContactInformation({
  mode,
  onMode,
  countryIso,
  onCountryIso,
  contactNumber,
  onContactNumber,
  inboundCalls,
  onInboundCalls,
  agentSpeaksFirst,
  onAgentSpeaksFirst,
}) {
  const header =
    mode === "phone"
      ? {
          title: "Telephony simulation (PSTN)",
          body: "Places a real call — needs a telephony provider.",
        }
      : {
          title: "Web simulation (WebRTC)",
          body: "No phone call or telephony provider needed.",
        };
  const selected = COUNTRY_BY_ISO[countryIso] || COUNTRY_BY_ISO.US;

  return (
    <Stack spacing={1}>
      {/* mode header card + segmented toggle */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 1.75,
          py: 1.25,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {header.title}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
          >
            {header.body}
          </Typography>
        </Box>
        <SegmentedToggle
          value={mode}
          onChange={onMode}
          options={[
            { value: "web", label: "Web" },
            { value: "phone", label: "Phone" },
          ]}
        />
      </Stack>

      {/* phone fields — only for a call over the phone */}
      {mode === "phone" && (
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Box sx={{ width: { xs: "100%", sm: 180 } }}>
            <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>
              Country Code
            </Typography>
            <Select
              fullWidth
              size="small"
              value={countryIso}
              onChange={(e) => onCountryIso(e.target.value)}
              MenuProps={{ PaperProps: { sx: { maxHeight: 320 } } }}
              renderValue={() => (
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Box component="span" sx={{ fontSize: 16, lineHeight: 1 }}>
                    {selected.flag}
                  </Box>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontFamily: "ui-monospace, Menlo, monospace",
                    }}
                  >
                    {selected.dial}
                  </Typography>
                </Stack>
              )}
              sx={{
                "& .MuiSelect-select": {
                  display: "flex",
                  alignItems: "center",
                  py: 0.75,
                },
              }}
            >
              {COUNTRY_OPTIONS.map((c, idx) => (
                <MenuItem
                  key={c.iso}
                  value={c.iso}
                  sx={{
                    typography: "s2",
                    py: 0.75,
                    borderTop:
                      idx > 0 &&
                      !c.suggested &&
                      COUNTRY_OPTIONS[idx - 1]?.suggested
                        ? "1px solid"
                        : "none",
                    borderColor: "divider",
                  }}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={1.25}
                    sx={{ width: "100%" }}
                  >
                    <Box
                      component="span"
                      sx={{ fontSize: 16, lineHeight: 1, flexShrink: 0 }}
                    >
                      {c.flag}
                    </Box>
                    <Typography
                      sx={{
                        typography: "s2",
                        flex: 1,
                        minWidth: 0,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {c.name}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s2",
                        color: "text.subtitle",
                        fontFamily: "ui-monospace, Menlo, monospace",
                        flexShrink: 0,
                      }}
                    >
                      {c.dial}
                    </Typography>
                  </Stack>
                </MenuItem>
              ))}
            </Select>
          </Box>
          <Box sx={{ flex: 1 }}>
            <Field
              label="Contact Number"
              required
              placeholder="Number to call for the simulation"
              value={contactNumber}
              onChange={onContactNumber}
              mono
            />
          </Box>
        </Stack>
      )}

      {/* Inbound Calls only belongs to the Phone mode — a WebRTC
          simulation has no phone number for anyone to dial in on. */}
      {mode === "phone" && (
        <ToggleRow
          checked={inboundCalls}
          onChange={onInboundCalls}
          title="Inbound Calls"
          body="Allows the agent to take inbound calls."
        />
      )}

      <ToggleRow
        checked={agentSpeaksFirst}
        onChange={onAgentSpeaksFirst}
        title="Agent speaks first"
        body="The simulator waits for its greeting."
      />
    </Stack>
  );
}
ContactInformation.propTypes = {
  mode: PropTypes.oneOf(["web", "phone"]),
  onMode: PropTypes.func,
  countryIso: PropTypes.string,
  onCountryIso: PropTypes.func,
  contactNumber: PropTypes.string,
  onContactNumber: PropTypes.func,
  inboundCalls: PropTypes.bool,
  onInboundCalls: PropTypes.func,
  agentSpeaksFirst: PropTypes.bool,
  onAgentSpeaksFirst: PropTypes.func,
};

/* Two-value segmented control — active pill uses the same tinted
   selected surface as the ChipCard elsewhere so it doesn't feel like
   a foreign primitive. */
export function SegmentedToggle({ value, onChange, options }) {
  return (
    <SegmentedTabs
      value={value}
      onChange={(_, v) => onChange(v)}
      sx={{ alignSelf: "flex-start" }}
    >
      {options.map((o) => (
        <Tab key={o.value} value={o.value} label={o.label} />
      ))}
    </SegmentedTabs>
  );
}
SegmentedToggle.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  options: PropTypes.arrayOf(
    PropTypes.shape({ value: PropTypes.string, label: PropTypes.node }),
  ),
};

export function ToggleRow({ checked, onChange, title, body, hint }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 1.75,
        py: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography
          component="div"
          sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
        >
          {hint ? <HintLine hint={hint}>{body}</HintLine> : body}
        </Typography>
      </Box>
      <Switch
        size="small"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
    </Stack>
  );
}
ToggleRow.propTypes = {
  checked: PropTypes.bool,
  onChange: PropTypes.func,
  title: PropTypes.node,
  body: PropTypes.node,
  /* The rest of the body's sentence, behind an ⓘ. */
  hint: PropTypes.node,
};

/* How many scenarios the build generates — 10 by default, up to 1,000 (RSI
   PRD: "A build makes 10 scenarios by default and up to 1,000"). */
const MAX_SCENARIOS = 1000;
const DEFAULT_SCENARIOS = "10";

function ScenarioCountRow({ value: ownValue, onChange: ownOnChange }) {
  const connect = useContext(ConnectModeContext);
  /* Connecting an agent to build an environment: the count is the connect form's. */
  const buildCtx = useConnectBuild();
  const value = buildCtx ? String(buildCtx.scenarioCount) : ownValue;
  const onChange = buildCtx ? buildCtx.setScenarioCount : ownOnChange;
  const n = Number(value) || 0;
  /* Connecting an agent and nothing else generates nothing — scenarios belong to an environment. */
  if (connect && !buildCtx) return null;
  const onInput = (raw) => {
    const digits = raw.replace(/\D/g, "").replace(/^0+/, "").slice(0, 3);
    onChange(
      digits && Number(digits) > MAX_SCENARIOS ? String(MAX_SCENARIOS) : digits,
    );
  };
  return (
    <Box
      sx={{
        px: 1.75,
        py: 1.25,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        Scenarios to generate
      </Typography>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ xs: "flex-start", sm: "center" }}
        spacing={{ xs: 0.75, sm: 1.5 }}
        sx={{ mt: 1 }}
      >
        <TextField
          size="small"
          value={value}
          onChange={(e) => onInput(e.target.value)}
          placeholder="e.g. 25"
          inputProps={{
            inputMode: "numeric",
            "aria-label": "Number of scenarios to generate",
          }}
          InputProps={{
            endAdornment: n ? (
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  ml: 0.5,
                  whiteSpace: "nowrap",
                }}
              >
                {n === 1 ? "scenario" : "scenarios"}
              </Typography>
            ) : null,
          }}
          sx={{
            width: 150,
            flexShrink: 0,
            "& .MuiInputBase-input": {
              typography: "s2",
              fontVariantNumeric: "tabular-nums",
            },
          }}
        />
        <Typography
          sx={{ typography: "s3", color: "text.subtitle", minWidth: 0 }}
        >
          {`10 by default, up to ${MAX_SCENARIOS.toLocaleString()}.`}
        </Typography>
      </Stack>
    </Box>
  );
}
ScenarioCountRow.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
};

const scenarioCountOf = (value) => (Number(value) > 0 ? Number(value) : null);

function ProviderRow({ options, value, onChange }) {
  return (
    <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
      {options.map((o) => (
        <ChipCard
          key={o.id}
          icon={o.icon}
          label={o.name}
          on={value === o.id}
          onClick={() => onChange(o.id)}
          tag={o.tag}
          disabled={o.disabled}
        />
      ))}
    </Stack>
  );
}
ProviderRow.propTypes = {
  options: PropTypes.array,
  value: PropTypes.string,
  onChange: PropTypes.func,
};

/* A platform note cut to its first sentence — the rest behind an ⓘ. */
export function NoteLine({ text }) {
  const cut = String(text || "").indexOf(". ");
  if (cut < 0) return text;
  return (
    <HintLine hint={text.slice(cut + 2)}>{text.slice(0, cut + 1)}</HintLine>
  );
}
NoteLine.propTypes = { text: PropTypes.string };

/* A platform key's helper line: where it lives and what it may do, in one
   short line — the full sentence on hover. Pass it as the key field's helper. */
export function PlatformKeyScope({ prototypeHint = false }) {
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
    >
      <span>
        Stored encrypted · used only to read the agent and place test calls
      </span>
      <Tooltip
        arrow
        title={
          <Box sx={{ typography: "s3" }}>
            Stored encrypted — never shown again, never sent to the improver.{" "}
            {PLATFORM_KEY_SCOPE}
          </Box>
        }
      >
        <Box
          component="span"
          sx={{
            display: "inline-flex",
            color: "text.disabled",
            cursor: "help",
          }}
        >
          <Iconify icon="solar:info-circle-linear" width={14} />
        </Box>
      </Tooltip>
    </Box>
  );
}
PlatformKeyScope.propTypes = { prototypeHint: PropTypes.bool };

/**
 * A platform's brand mark.
 *
 * Renders the vendor's official logo (inlined, so it inherits `currentColor`
 * and adapts to light/dark). Platforms without a bundled logo — e.g. the chat
 * providers — fall back to a brand-coloured monogram tile so the picker still
 * reads as branded.
 */
export function PlatformLogo({ id, name, brand, icon }) {
  const spec = PLATFORM_LOGOS[id];
  /* A brand mark from Iconify, for platforms without a bundled logo. */
  if (!spec && /^(simple-icons|logos):/.test(icon || "")) {
    return (
      <Iconify
        icon={icon}
        width={16}
        aria-label={name}
        sx={{ color: "text.primary", flexShrink: 0 }}
      />
    );
  }
  if (spec) {
    const isMark = spec.type === "mark";
    return (
      <Box
        aria-label={name}
        dangerouslySetInnerHTML={{ __html: spec.svg }}
        sx={{
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          ...(spec.keepColor ? {} : { color: "text.primary" }),
          "& svg": {
            height: isMark ? 15 : 14,
            width: isMark ? 15 : "auto",
            maxWidth: spec.maxWidth || 60,
            display: "block",
            ...(spec.keepColor ? {} : { fill: "currentColor" }),
          },
        }}
      />
    );
  }
  return (
    <Box
      sx={{
        width: 15,
        height: 15,
        borderRadius: "4px",
        flexShrink: 0,
        bgcolor: brand || "text.disabled",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Typography
        sx={{
          fontSize: 12,
          fontWeight: 600,
          color: "common.white",
          lineHeight: 1,
        }}
      >
        {(name || "?").trim()[0]?.toUpperCase()}
      </Typography>
    </Box>
  );
}
PlatformLogo.propTypes = {
  id: PropTypes.string,
  name: PropTypes.string,
  brand: PropTypes.string,
  icon: PropTypes.string,
};

function ContinueRow({ disabled, hint, label = "Build environment", onClick }) {
  const connect = useContext(ConnectModeContext);
  const rt = useContext(RuntimeContext);
  const blocked = disabled || !!connect?.blocked;
  const shownHint = disabled
    ? hint
    : connect?.blocked
      ? "Add the endpoint the agent runs at"
      : hint;
  return (
    <>
      {connect?.extra}
      {!connect && rt && (
        <WhereItRuns
          choice={rt.choice}
          runtime={rt.runtime}
          setRuntime={rt.setRuntime}
        />
      )}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={2}
        className="continue-row"
        sx={{
          pt: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
          mx: -2.5,
          px: 2.5,
          pb: 0,
        }}
      >
        {blocked && shownHint ? (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {shownHint}
          </Typography>
        ) : (
          <Box />
        )}
        <Button
          variant="contained"
          color="primary"
          disabled={blocked}
          onClick={onClick}
          startIcon={
            <Iconify
              icon={
                connect
                  ? "solar:link-circle-linear"
                  : "solar:magic-stick-3-linear"
              }
              width={14}
            />
          }
          endIcon={<Iconify icon="solar:alt-arrow-right-linear" width={14} />}
          sx={{ flexShrink: 0 }}
        >
          {connect ? connect.submitLabel || "Connect agent" : label}
        </Button>
      </Stack>
    </>
  );
}
ContinueRow.propTypes = {
  disabled: PropTypes.bool,
  hint: PropTypes.node,
  label: PropTypes.node,
  onClick: PropTypes.func,
};

/* ── data ──────────────────────────────────────────────────────────────── */

const OPTIONS = [
  {
    id: "templates",
    group: "bring",
    title: "Prebuilt Environments",
    icon: "solar:widget-linear",
    blurb:
      "Skip world setup — pick a prebuilt world, then connect your agent to it.",
    setupSubtitle:
      "Prebuilt environments you can adapt in minutes — pick one, then wire your agent.",
  },
  {
    id: "connected",
    group: "bring",
    title: "Connected agent",
    icon: "solar:link-circle-linear",
    blurb:
      "Reuse an agent you've already connected — from the Agents page or another environment. Same agent, same version history; nothing to re-enter.",
    setupSubtitle:
      "Pick an agent and the version this environment should test.",
  },
  {
    id: "source",
    group: "bring",
    title: "Source repository",
    icon: "solar:code-linear",
    blurb:
      "Read the code straight from a git host. Scenarios and tools stay in sync with the real code as it changes — no manual updates when your agent evolves.",
    preview: ["GitHub", "GitLab", "Bitbucket"],
    setupSubtitle:
      "We read the code so scenarios stay in sync with your actual tools.",
  },
  {
    id: "hosted",
    group: "bring",
    title: "Hosted platform",
    icon: "solar:cloud-linear",
    blurb: "Connect an agent on a managed voice or chat platform by its ID.",
    preview: ["Vapi", "Retell", "ElevenLabs", "Bland", "Google CX"],
    setupSubtitle: "Point at an agent living on a managed platform.",
  },
  {
    id: "mcp",
    group: "bring",
    title: "MCP server",
    icon: "solar:plug-circle-linear",
    blurb:
      "Any MCP server can be an agent's world. Its tools become what the agent acts on, one-to-one — no adapter code required.",
    preview: ["HTTP", "SSE", "stdio"],
    setupSubtitle:
      "Pick the agent, then the server — its tools become the world of that agent's environment.",
  },
  {
    id: "upload",
    group: "bring",
    title: "Code upload",
    icon: "solar:cloud-upload-linear",
    blurb:
      "Drop a folder or zip when you can't push to a git host. Good for prototypes, private research code, or one-off experiments you want to spin up fast.",
    preview: [".py", ".ts", ".js", ".zip"],
    setupSubtitle: "Upload your agent code and we'll analyze it in place.",
  },
  {
    id: "local",
    group: "bring",
    title: "From code",
    icon: "solar:code-linear",
    blurb:
      "Build and run environments from your terminal, Python or your coding agent.",
    preview: ["CLI", "Python", "Coding agent"],
    setupSubtitle:
      "From the CLI, Python or your coding agent. Environments made this way are managed in code.",
  },
  /* An agent reached only by its endpoint — on the Agents page and
     when building an environment alike. */
  {
    id: "running",
    group: "bring",
    title: "Agent endpoint",
    icon: "solar:global-linear",
    blurb:
      "Only a URL? We call it on every simulated turn — the real agent, once you allow it.",
    preview: ["HTTP", "WebSocket"],
    setupSubtitle: "Point us at the endpoint your agent answers on.",
  },
  /* A number we can only call (Phase 2) — test only, ends in a suggested fix.
     Closed platforms come in this way too. */
  {
    id: "phone",
    group: "bring",
    title: "Phone number",
    icon: "solar:phone-linear",
    blurb:
      "Only a number to call? Paste its instructions and we test it by phone. We can't change it ourselves, so improvements end in a suggested fix you apply.",
    preview: ["Test only", "Sierra", "Decagon", "Zendesk"],
    setupSubtitle: "The number we dial, and the instructions it follows.",
  },
  /* Not in the picker grid — reached via the "Start from scratch" button in
     the page header. Kept in OPTIONS so FlowPanel can look up its title +
     subtitle when triggered. */
  {
    id: "scratch",
    group: "hidden",
    title: "Build from scratch",
    icon: "solar:document-add-linear",
    blurb:
      "Design the world, personas, and evals first — connect the agent later.",
    setupSubtitle:
      "Describe the world to test in — connect the agent when it's ready.",
  },
];

/* How the setup drawer is titled when Build from scratch opens it. */
const SCRATCH_META = {
  name: "Build from scratch",
  icon: "solar:document-add-linear",
  withSubtitle: true,
};

const PANELS = {
  connected: PanelConnectedAgent,
  source: PanelSourceRepo,
  running: PanelRunningAgent,
  phone: PanelPhoneNumber,
  hosted: PanelHostedPlatform,
  mcp: PanelMcpServer,
  upload: PanelCodeUpload,
  scratch: PanelScratch,
  local: PanelBuildLocally,
};

const REPO_PROVIDERS = [
  { id: "github", name: "GitHub", icon: "eva:github-fill" },
  { id: "gitlab", name: "GitLab", icon: "logos:gitlab" },
  { id: "bitbucket", name: "Bitbucket", icon: "logos:bitbucket" },
];

const MCP_TRANSPORTS = [
  { id: "http", name: "HTTP", icon: "solar:global-linear" },
  { id: "sse", name: "SSE", icon: "solar:transfer-vertical-linear" },
  { id: "stdio", name: "stdio", icon: "solar:code-square-linear" },
];

const OS_OPTIONS = [
  { id: "mac", name: "macOS", icon: "logos:apple" },
  { id: "linux", name: "Linux", icon: "logos:linux-tux" },
  { id: "windows", name: "Windows", icon: "logos:microsoft-windows-icon" },
];

/*
  Extract simple escalation / policy sentences from the flow so the
  Contract tab has real rules to show. Splits on sentence boundaries
  and keeps the ones that read like enforceable constraints. Caps
  the list so the tab stays scannable.
*/
function deriveScratchRules(flowText) {
  const text = String(flowText || "").trim();
  if (!text) return [];
  const sentences = text
    .split(/[.!?]\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const constraintish =
    /(escalat|must|never|always|require|only|if\b|unless|before|after|approve|reject|forbid|deny|need|not allowed|when the|when a)/i;
  const rules = sentences.filter((s) => constraintish.test(s)).slice(0, 4);
  /* Ensure at least one rule so the Contract tab isn't empty. If the
     description had none, fall back to a generic "task is done when"
     rule pulled from the last sentence. */
  if (rules.length === 0 && sentences.length) {
    rules.push(sentences[sentences.length - 1]);
  }
  return rules;
}

/*
  Generate a richer scenario pool (~15-22 stubs) that reference the
  flow description. Each carries the shape DerivedPanels /
  ScenarioTable expect: id, name, title, task, persona, subTasks,
  branchCategory. Purely mocked — a real generator would run an LLM
  against the intake and produce a similar spread.
*/
function deriveScratchScenarios(flowText, envName) {
  const t = String(flowText || "").toLowerCase();
  const short = (envName || "flow")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .slice(0, 24);

  /* Baseline shape every flow gets — happy path, three persona
     variants, three adversarial pushes, three edge cases, three
     failure modes, and a done-state check. */
  const base = [
    {
      key: "happy",
      branch: "core",
      situation: "Standard case the flow was described around.",
      task: "Complete the described flow end-to-end.",
      subTasks: ["Trigger the flow", "Reach for the right tool", "Report done"],
    },
    {
      key: "happy-rushed",
      branch: "core",
      situation:
        "The requester is in a hurry but the flow still needs every step.",
      task: "Complete the flow correctly despite pressure to skip steps.",
      subTasks: [
        "Acknowledge the hurry",
        "Run the full flow",
        "Report done clearly",
      ],
    },
    {
      key: "happy-quiet",
      branch: "core",
      situation:
        "The requester provides the bare minimum and expects the agent to pick it up.",
      task: "Fill in the missing intent from context and complete the flow.",
      subTasks: ["Infer the ask", "Confirm before acting", "Complete the flow"],
    },
    {
      key: "happy-verbose",
      branch: "core",
      situation:
        "The requester wanders through backstory before landing on the real ask.",
      task: "Keep track of the real ask and complete the flow.",
      subTasks: ["Filter the noise", "Extract the ask", "Complete the flow"],
    },

    {
      key: "edge-anomaly",
      branch: "edge case",
      situation:
        "An awkward variant of the described case where one input is unusual.",
      task: "Notice the awkward part and handle it rather than treat it as ordinary.",
      subTasks: ["Detect the anomaly", "Adjust the tool call", "Report done"],
    },
    {
      key: "edge-missing-info",
      branch: "edge case",
      situation:
        "A required detail from the described flow is missing from the request.",
      task: "Ask for the missing detail before running the tool.",
      subTasks: ["Detect the gap", "Ask concisely", "Resume once answered"],
    },
    {
      key: "edge-conflicting",
      branch: "edge case",
      situation: "Two details in the request contradict each other.",
      task: "Surface the conflict rather than pick one silently.",
      subTasks: ["Detect the conflict", "Restate both sides", "Get a decision"],
    },
    {
      key: "edge-stale",
      branch: "edge case",
      situation:
        "The referenced record is stale or has moved on since the request was written.",
      task: "Reconcile against the current state before acting.",
      subTasks: [
        "Fetch the latest state",
        "Compare with the request",
        "Decide next step",
      ],
    },

    {
      key: "escalate-condition",
      branch: "core",
      situation: "The described escalation condition is met.",
      task: "Escalate rather than resolve locally.",
      subTasks: [
        "Detect the trigger",
        "Route to the correct owner",
        "Leave the case in the expected state",
      ],
    },
    {
      key: "escalate-loop",
      branch: "core",
      situation:
        "The agent has already tried the same step twice without progress.",
      task: "Break the loop and escalate.",
      subTasks: ["Detect the loop", "Explain the block", "Escalate"],
    },

    {
      key: "adversarial-skip",
      branch: "adversarial",
      situation: "The user pushes to skip a step the flow requires.",
      task: "Hold the described policy despite pressure.",
      subTasks: [
        "Recognise the ask",
        "Refuse politely",
        "Offer the correct route",
      ],
    },
    {
      key: "adversarial-social",
      branch: "adversarial",
      situation: "The user claims a manager already approved an exception.",
      task: "Verify or refuse — never take the claim at face value.",
      subTasks: [
        "Recognise the claim",
        "Attempt verification",
        "Refuse if unverified",
      ],
    },
    {
      key: "adversarial-urgency",
      branch: "adversarial",
      situation: "The user manufactures urgency to bypass a required check.",
      task: "Hold the required check without escalating the emotion.",
      subTasks: [
        "Recognise the pressure",
        "Explain the check",
        "Complete the check",
      ],
    },
    {
      key: "adversarial-authority",
      branch: "adversarial",
      situation:
        "The user claims to be an internal admin and asks the agent to break policy.",
      task: "Refuse and route to the correct authenticated channel.",
      subTasks: [
        "Refuse the ask",
        "Explain the correct channel",
        "Log the attempt",
      ],
    },

    {
      key: "failure-tool-error",
      branch: "failure mode",
      situation: "The main tool call returns an error.",
      task: "Recover, retry, or escalate — don't silently drop the request.",
      subTasks: [
        "Read the error",
        "Try the recovery path",
        "Report the outcome",
      ],
    },
    {
      key: "failure-timeout",
      branch: "failure mode",
      situation: "The tool call times out.",
      task: "Handle the timeout without abandoning the request.",
      subTasks: [
        "Detect the timeout",
        "Retry once",
        "Escalate if it fails again",
      ],
    },
    {
      key: "failure-invalid-input",
      branch: "failure mode",
      situation: "The tool rejects the input the agent constructed.",
      task: "Fix the input rather than pass the rejection through.",
      subTasks: ["Read the rejection", "Correct the input", "Retry"],
    },

    {
      key: "off-topic",
      branch: "core",
      situation: "The user brings up a request the flow doesn't cover.",
      task: "Route out of scope politely without pretending to handle it.",
      subTasks: [
        "Recognise out-of-scope",
        "State it clearly",
        "Point to the right channel",
      ],
    },
    {
      key: "done-state-check",
      branch: "core",
      situation:
        "The requester asks for confirmation that the task is truly complete.",
      task: "Report the concrete end-state that the flow described as done.",
      subTasks: ["Read the end state", "Restate concisely", "Close the case"],
    },
  ];

  /* Keyword-driven extensions layered on top so the pool grows with
     the specificity of the description. */
  if (/refund|money|charge|payment|invoice/.test(t)) {
    base.push(
      {
        key: "money-over-limit",
        branch: "core",
        situation:
          "The request exceeds the policy limit implied by the description.",
        task: "Refuse or escalate according to the stated rule.",
        subTasks: [
          "Look up the value",
          "Compare against the limit",
          "Take the correct action",
        ],
      },
      {
        key: "money-duplicate",
        branch: "edge case",
        situation:
          "A near-identical request has already been processed within a short window.",
        task: "Detect the duplicate and refuse the second attempt.",
        subTasks: [
          "Look up recent activity",
          "Match on the identifiers",
          "Refuse and explain",
        ],
      },
    );
  }
  if (/security|sensitive|leak|pii|password|token|api key|secret/.test(t)) {
    base.push(
      {
        key: "sensitive-payload",
        branch: "core",
        situation:
          "The task mentions sensitive material called out in the description.",
        task: "Handle the sensitive material per the stated rule.",
        subTasks: [
          "Detect the material",
          "Apply the rule",
          "Complete without leaking",
        ],
      },
      {
        key: "sensitive-echo",
        branch: "failure mode",
        situation:
          "The user pastes a credential and asks the agent to repeat it back.",
        task: "Never echo the credential; acknowledge without repeating.",
        subTasks: [
          "Detect the credential",
          "Refuse the echo",
          "Redact the transcript",
        ],
      },
    );
  }
  if (/escalat|human|maintainer|manager|supervisor/.test(t)) {
    base.push({
      key: "escalate-wrong-owner",
      branch: "adversarial",
      situation:
        "The described escalation path routes the case to the wrong owner.",
      task: "Correct the routing before handing off.",
      subTasks: [
        "Recognise the mismatch",
        "Pick the right owner",
        "Hand off with context",
      ],
    });
  }

  /* Grouped by what they test, the way a built environment's are. */
  const USE_CASE = {
    core: "The flow as described",
    "edge case": "Edge cases",
    adversarial: "Callers who push",
    "failure mode": "When a tool fails",
  };
  return base.map((s, i) => ({
    id: `sc-scratch-${short}-${s.key}`,
    name: `${short}-${s.key}`,
    title: s.situation,
    summary: s.task,
    task: s.task,
    situation: s.situation,
    useCase: USE_CASE[s.branch] || "The flow as described",
    /* What a pass looks like — the reference outcome the gates prove against. */
    expected: s.task,
    outcome: s.task,
    fromDescription: true,
    /* SubTasksCell expects { id, label } objects, not bare strings —
       otherwise rows render numbers only ("1. 2. 3.") and the label
       disappears. */
    subTasks: s.subTasks.map((label, j) => ({ id: `${s.key}-${j}`, label })),
    persona: {
      name: personaFor(i),
      role: "user",
      gender: null,
      ageGroup: null,
      /* Empty array (not undefined) — downstream spreads and joins
         expect an iterable, not a missing field. */
      traits: [],
    },
    branchCategory: s.branch,
  }));
}

function personaFor(i) {
  const names = [
    "Ellie Marsh",
    "Marcus Reid",
    "Aditi Rao",
    "Harper Lang",
    "Priya Shah",
    "Kevin Delaney",
    "Isabela Ferreira",
    "Nathan Cole",
    "Lucas Reyes",
    "Rachel Greene",
    "Douglas Kim",
    "Sasha Novak",
    "Rowan Bishop",
    "Tyler Brooks",
    "Elena Vargas",
    "Ben Alvarado",
  ];
  return names[i % names.length];
}

/*
  Suggest evaluators by scanning the flow for what actually needs
  grading. task_success is always on; the rest layer in when the
  description implies rules, escalations, PII, or tone.
*/
function deriveScratchEvals(flowText) {
  const t = String(flowText || "").toLowerCase();
  const evals = ["task_success"];
  if (/must|never|always|require|policy|rule|escalat/.test(t))
    evals.push("policy_adherence");
  if (/security|leak|pii|sensitive|password|token|api key|secret/.test(t))
    evals.push("pii_leakage");
  if (/tone|polite|angry|frustrat|calm|empath/.test(t)) evals.push("tone");
  if (/hallucinat|fabricat|made.up|invent|guess/.test(t))
    evals.push("hallucination");
  if (/step|efficient|quick|latency|fast|slow/.test(t))
    evals.push("step_efficiency");
  return evals;
}

/*
  Cheap keyword-based namer for the scratch flow. Same shape as
  ScratchEnvironment's deriveName in the other variation: scan the
  intake for common domain terms and produce a short, human name.
  Falls back to a generic "Scratch environment" so we never mint a blank one.
*/
function deriveScratchName(flowText, attachments) {
  const t = String(flowText || "").toLowerCase();
  const has = (...needles) => needles.some((n) => t.includes(n));

  /* Most specific first — "change a hotel booking … refunds over $300" is a
     bookings line, not a refunds one. */
  if (has("hotel")) return "Hotel bookings environment";
  if (has("flight", "airline", "rebook")) return "Flight changes environment";
  if (has("booking", "reservation")) return "Bookings environment";
  if (has("appointment", "clinic", "patient"))
    return "Appointments environment";
  if (has("insurance", "claim")) return "Claims environment";
  if (has("refund", "return")) return "Refunds & returns environment";
  if (has("support", "ticket", "helpdesk", "help desk"))
    return "Support agent environment";
  if (
    has(
      "github",
      " pr ",
      "pull request",
      "merge request",
      "gitlab",
      "bitbucket",
    )
  )
    return "Repo triage environment";
  if (has("linear", "jira", "issue")) return "Issue triage environment";
  if (has("slack", "discord")) return "Chat ops environment";
  if (has("gmail", "email", "inbox")) return "Inbox agent environment";
  if (has("schedule", "meeting", "calendar"))
    return "Scheduling agent environment";
  if (has("code", "diff", "review")) return "Code review environment";
  if (has("draft", "report", "write", "summar"))
    return "Doc drafting environment";
  if (has("sync", "migrat")) return "Data sync environment";
  if (has("call", "voice", "phone", "sip", "telephony"))
    return "Voice agent environment";

  if (attachments?.length)
    return `Scratch environment · ${attachments.length} file${attachments.length === 1 ? "" : "s"}`;
  return "Scratch environment";
}

function formatSize(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let n = bytes;
  let u = 0;
  while (n >= 1024 && u < units.length - 1) {
    n /= 1024;
    u += 1;
  }
  return `${n.toFixed(n >= 10 || u === 0 ? 0 : 1)} ${units[u]}`;
}

export { ChipCard };
