import PropTypes from "prop-types";
import {
  Fragment,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useSnackbar } from "notistack";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  Tooltip,
  IconButton,
  TextField,
  Popover,
  Checkbox,
  InputBase,
  Menu,
  MenuItem,
  ListItemIcon,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { alpha } from "@mui/material/styles";
import { PersonaBadge } from "../components/primitives";
import { generatedPool, goalLabel } from "../_mock/scenarios";
import { runScopeOf } from "../_mock/runScope";
import {
  staleScenarios,
  proofStatus,
  autoReprove,
  markEdited,
} from "../_mock/proofs";
import { subTasksFor } from "../_mock/contract";
import ScenarioDetail from "../components/ScenarioDetail";
import CoverageMatrix from "./scenarios/CoverageMatrix";
import AddScenariosDrawer from "./scenarios/AddScenariosDrawer";
import ScenarioEditor from "./scenarios/ScenarioEditor";
import {
  BatchContainers,
  BatchHistoryPanel,
  AsOfBanner,
} from "./scenarios/BatchDesigns";
import { batchesFrom } from "./scenarios/batchDesignModel";
import {
  stampNewBatch,
  unanswerableNote,
  rejectedNote,
} from "../_mock/addScenarios";
import SelectionBar from "./scenarios/SelectionBar";
import { PickRouteIllustration } from "./scenarios/RouteThumbs";
import {
  publishScenarioSelection,
  clearScenarioSelection,
  subscribeScenarioSelection,
  getScenarioSelection,
} from "../_mock/scenarioSelectionBus";
import { injectComposerScaffold } from "../_mock/composerScaffoldBus";
import {
  ensureProvenance,
  sourceOf,
  groupByBatch,
  currentUser,
} from "../_mock/scenarioProvenance";

/* AddScenariosDrawer emits `route` ids (generate | error-feed | failing |
   twin | dataset | script | personas); provenance vocab is different, so
   translate at the boundary. Chat never comes through here — the chat
   stamps its own batches. */
const SOURCE_MAP = {
  generate: "generated",
  "error-feed": "error-feed",
  failing: "failing-case",
  twin: "template",
  production: "production",
  replay: "production",
  dataset: "dataset-import",
  script: "manual",
  manual: "manual",
  personas: "persona-library",
};
import { ObjectFilterPanel } from "src/components/filter";
import {
  currentAgentVersion,
  currentEnvVersion,
  environmentVersions,
} from "../_mock/versions";
import {
  agentToolsFor,
  knownToolsFor,
  requiredToolsOf,
  worldToolsFor,
} from "../_mock/toolFit";
import { scenarioStatus } from "../_mock/scenarioStatus";
import { isConnectOnly } from "../_mock/envModel";
import { envLabelOf } from "../_mock/envVersionKinds";
import { firstSnapshotOfWorld } from "../_mock/envSnapshot";
import { suiteGateOf, recordSpotCheck } from "../_mock/suiteGate";
import QualityReviewPanel, {
  QualityReviewChip,
  QualityReviewFacts,
} from "./scenarios/QualityReviewPanel";
import {
  scenarioStateFor,
  needsReview,
  stateActionOf,
  STATE_CHOICES,
  archiveScenario,
  restoreScenario,
  liveScenarios,
  reproveScenarios,
  rewrittenScenario,
  archivedOf,
} from "../_mock/scenarioStates";
import { subGoalUsageOf } from "../_mock/scenarioSubGoals";
import { ArchiveControl } from "./scenarios/ScenarioStateBits";
import { suiteLogPatch } from "../_mock/suiteLog";
import { applyEditBump } from "../_mock/scenarioEdits";
import { setBlocker } from "../_mock/releaseBlocker";
import ScenarioDrawer from "./scenarios/ScenarioDrawer";
import { ImprovementScenariosDialog } from "./scenarios/ImprovementScenariosStrip";
import { pendingImprovementScenarios } from "../_mock/improvementScenarios";
import GroupByMenu from "./scenarios/GroupByMenu";
import NeedsReviewMenu from "./scenarios/NeedsReviewMenu";
import { ProtectionLine } from "./scenarios/MustPassBits";
import { protectionTally } from "../_mock/mustPassProtection";
import { coverageSummary } from "./scenarios/coverageSummary";
import SubGoalsPanel from "./SubGoalsPanel";
import useEnvViewAs from "./useEnvViewAs";
import { runReadiness } from "../_mock/setupGaps";
import SideDrawer from "../components/SideDrawer";
import { ScenarioRowContext } from "./scenarios/scenarioRowContext";
import { runSummaries } from "../_mock/comparison";
import AlsoRunIn from "../run/AlsoRunIn";
import {
  tagsOf as orgTagsOf,
  groupsOf as orgGroupsOf,
  tagPatch,
  groupPatch,
  duplicatePatch,
  validateRows,
  validatePatch,
  archivePatch,
  promotePatch,
  exportScenarios,
} from "../_mock/scenarioOrg";
import {
  TagsGroupsManager,
  BulkOrgDialog,
} from "./scenarios/ScenarioOrgDialogs";
import { clickable, focusRing } from "../components/clickable";

/**
 * Scenarios.
 *
 * The scenarios are already here: they are derived from the agent when the
 * environment is built. So the page leads with them, and the ways to add more
 * live behind a button — five route cards across the top said the opposite,
 * that nothing had happened yet and a route had to be chosen first.
 *
 * Two views of the same rows. The list expands one scenario into its brief,
 * its checks and the proof it is passable. The table puts thirty of them side
 * by side on the derived axes, which is the view you want when the question is
 * what is in here rather than what is this.
 */
export default function ScenariosStep({
  env,
  envState,
  patch,
  buildMode,
  onBuilderPrompt,
  onStartRun,
  groupCandidates = [],
  locked = false,
  onFork,
  selectionRun = null,
}) {
  /* `?add=production` (from an agent's Production tab) lands straight on
     that Add scenarios route. */
  const [searchParams] = useSearchParams();
  const addRoute = searchParams.get("add");
  /* `?viewAs=v2` shows the suite as an older environment version reads it —
     its statuses and proofs against that world, and read-only, since edits
     belong to the version the environment is on. */
  const { viewAs } = useEnvViewAs(env, envState);
  const viewing = !!viewAs;
  const viewState = useMemo(
    () => (viewing ? { ...envState, activeEnvVersion: viewAs } : envState),
    [viewing, viewAs, envState],
  );
  const ro = locked || viewing;
  const roReason = locked
    ? "Fork this environment to edit."
    : "Viewing an older environment version — read-only.";
  /* Scenarios, or the shared sub-goal definitions they grade with —
     `?view=subgoals` (the Contract tab links there) opens on the latter. */
  const [mode, setMode] = useState(() =>
    searchParams.get("view") === "subgoals" ? "subgoals" : "scenarios",
  );
  const viewParam = searchParams.get("view");
  useEffect(() => {
    if (viewParam === "subgoals") setMode("subgoals");
  }, [viewParam]);
  /* The scenario open in the detail drawer (table view). `?scenario=<id>`
     (chat's "Open scenario") opens straight onto it. */
  const scenarioParam = searchParams.get("scenario");
  const [openId, setOpenId] = useState(() => scenarioParam || null);
  useEffect(() => {
    if (scenarioParam) {
      setMode("scenarios");
      setOpenId(scenarioParam);
    }
  }, [scenarioParam]);
  const [adding, setAdding] = useState(() => !!addRoute);
  /* Environments the baseline also runs in — every environment testing this
     agent gets its baseline from one press. */
  const [baselineAlso, setBaselineAlso] = useState([]);
  const [editing, setEditing] = useState(null);
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  /* The table is the one layout — the list view was retired. */
  const view = "table";
  /* The shared definition a scenario's "used by N" link asked to open. */
  const [goalId, setGoalId] = useState(null);
  /* The history drawer, the suite "as of" a batch, and the batch the
     history just jumped to. */
  const [historyOpen, setHistoryOpen] = useState(false);
  const [asOf, setAsOf] = useState(null);
  const [flashId, setFlashId] = useState(null);
  const flashTimer = useRef(null);
  /* The sticky header: its height (so group headers and history jumps land
     below it) and whether it is stuck (for the hairline under it). */
  /* Held in state, not refs, so the observers attach once the header has
     actually mounted — the panel can render before it does. */
  const [headEl, headRef] = useState(null);
  const [sentinelEl, headSentinel] = useState(null);
  const [headH, setHeadH] = useState(0);
  /*
    The toolbar gives way as it narrows (builder chat open, a small screen):
    full → labels shorten and Tags & groups / Coverage become icons →
    Tags & groups, Coverage and History fold into ⋯. Search, Filter, Group
    by, Needs review and Add scenarios always stay in the row.
  */
  const [barW, setBarW] = useState(1200);
  const barObserver = useRef(null);
  const barRef = useCallback((el) => {
    barObserver.current?.disconnect();
    if (!el) return;
    barObserver.current = new ResizeObserver(([e]) =>
      setBarW(e.contentRect.width),
    );
    barObserver.current.observe(el);
  }, []);
  /* Steps sized to what each one actually needs, search at a readable width. */
  const barFit = barW >= 1000 ? "full" : barW >= 860 ? "compact" : "narrow";
  const [moreAnchor, setMoreAnchor] = useState(null);
  const [headStuck, setHeadStuck] = useState(false);
  useEffect(() => {
    if (!headEl || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => setHeadH(headEl.offsetHeight));
    ro.observe(headEl);
    return () => ro.disconnect();
  }, [headEl]);
  useEffect(() => {
    if (!sentinelEl || typeof IntersectionObserver === "undefined")
      return undefined;
    const io = new IntersectionObserver(([e]) =>
      setHeadStuck(!e.isIntersecting),
    );
    io.observe(sentinelEl);
    return () => io.disconnect();
  }, [sentinelEl]);
  /*
    Search + use-case filter are shared by both views now. They used to
    live inside the list-only toolbar, which meant switching to the
    table lost your filters. Lifting to the parent lets the toolbar sit
    between the page heading and either body, and both views read the
    same `shown` rows.
  */
  const [query, setQuery] = useState("");
  /* Hidden groups — click-to-hide directly on the group header replaces
     the old filter modal. IDs are dimension-specific (a "persona:polite"
     id means nothing under Goal grouping) so switching Group by clears
     the hidden set. */
  const [hiddenGroupIds, setHiddenGroupIds] = useState([]);
  /* When set, the list narrows to just the broken scenarios. Turned on by
     the red banner's "Show them" button so a user can go from "N broke"
     straight to those exact rows without hunting through 68. */
  const [coverageOpen, setCoverageOpen] = useState(false);
  /* The quality-review drawer — the quality gate, the human spot-check, coverage. */
  const [gateOpen, setGateOpen] = useState(false);
  /* The review of scenarios improvements wrote (opened from Needs review). */
  const [improvOpen, setImprovOpen] = useState(false);
  /* Row-level facet filter — same shape the rest of the product uses
     (Runs list, Improvements list). Four dimensions, each a multi-select. */
  const [filters, setFilters] = useState({});
  /* Tags & groups manager, and one bulk operation awaiting its confirm. */
  const [orgOpen, setOrgOpen] = useState(false);
  const [bulk, setBulk] = useState(null);
  const [filterAnchor, setFilterAnchor] = useState(null);
  const filterCount = Object.values(filters).reduce(
    (sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0),
    0,
  );
  /*
    Filter output arrives from the shared FilterPanel as either
    { field: [values] } (Basic tab) or [{field, operator, value}]
    (Query tab). Flatten both to { field: [values] } to match the
    predicate below.
  */
  const applyFilters = (result) => {
    if (!result) {
      setFilters({});
      return;
    }
    if (Array.isArray(result)) {
      const flat = {};
      result.forEach((r) => {
        const values = Array.isArray(r.value)
          ? r.value
          : r.value != null
            ? [r.value]
            : [];
        if (!values.length) return;
        flat[r.field] = [...(flat[r.field] || []), ...values];
      });
      setFilters(flat);
    } else {
      setFilters(result);
    }
  };
  /* Grouping mode — matches the trace-table pattern from the run view.
     Users read the scenarios differently depending on what they're
     debugging: by goal for coverage, by persona to spot a caller type
     the agent handles badly, by sub-goal to see which step everyone
     lands on. */
  const [groupBy, setGroupByRaw] = useState("goal");
  const setGroupBy = (next) => {
    setGroupByRaw(next);
    setHiddenGroupIds([]);
  };
  const toggleGroupHidden = (id) =>
    setHiddenGroupIds((prev) =>
      prev.includes(id) ? prev.filter((v) => v !== id) : [...prev, id],
    );
  const selected = useMemo(
    () => envState?.scenarios || [],
    [envState?.scenarios],
  );

  /* What each row is checked against: the pinned environment version and
     the tools its world answers. One status per row, read by the chips and
     the table's Status column alike. */
  const envLabel = currentEnvVersion(env, viewState).label;
  /* Shown as the environment version (a snapshot of world, scenarios, evals …). */
  const envDisplay = envLabelOf(env, envState, envLabel);
  const answers = useMemo(
    () => new Set(worldToolsFor(env, viewState).map((t) => t.name)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState.envVersions, viewState.activeEnvVersion],
  );
  const knownTools = useMemo(
    () => knownToolsFor(env, envState),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState.envVersions, envState.agentVersions],
  );
  const toolsOf = (r) => requiredToolsOf(r, knownTools);
  /* The agent version under test — a scenario needing a tool an agent change
     removed reads Stale, with the reason. */
  const agentLabel = currentAgentVersion(envState)?.label;
  const statusCtx = {
    env,
    envState: viewState,
    envVersion: envLabel,
    answers,
    buildMode,
  };
  const statusById = useMemo(
    () =>
      new Map(
        selected.map((r) => [
          r.id,
          scenarioStatus(
            { ...r, requiredTools: requiredToolsOf(r, knownTools) },
            statusCtx,
          ),
        ]),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      selected,
      env,
      envState.envVersions,
      viewState.activeEnvVersion,
      answers,
      knownTools,
      buildMode,
    ],
  );
  const statusOf = (r) => statusById.get(r.id) || scenarioStatus(r, statusCtx);
  /* The scenario's state — Valid · Re-check · Outdated · Stale · Archived — read from the status above, refined by rule changes on the
     scenario's flow and by sub-goal edits it hasn't been re-proved for. */
  const stateById = useMemo(
    () =>
      new Map(
        selected.map((r) => [
          r.id,
          scenarioStateFor(r, statusById.get(r.id), {
            env,
            envState: viewState,
            tools: requiredToolsOf(r, knownTools),
            agentLabel,
          }),
        ]),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [statusById, viewState.ruleChanges, viewState.applicability, agentLabel],
  );
  const stateOf = (r) =>
    stateById.get(r.id) ||
    scenarioStateFor(r, statusOf(r), {
      env,
      envState: viewState,
      tools: toolsOf(r),
      agentLabel,
    });
  /* Archived rows stay listed but don't run or count. */
  const live = liveScenarios(selected);
  const archivedCount = selected.length - live.length;
  const staleCount = live.filter((r) => stateOf(r).id === "stale").length;
  const proposed = live.filter((r) => r.proposed);
  /* The scenario quality review. Read on the version being viewed. */
  const qualityReview = useMemo(
    () => (buildMode ? null : suiteGateOf(env, viewState)),
    [buildMode, env, viewState],
  );
  const answerSpotCheck = (id, answer) => {
    if (ro) return;
    patch(
      recordSpotCheck(env, envState, id, { ...answer, by: currentUser().name }),
    );
  };
  /* How many scenarios use each shared sub-goal — the detail says so. */
  const usage = useMemo(() => subGoalUsageOf(env, envState), [env, envState]);
  /* Which tools the agent version under test calls — a scenario needing
     one it doesn't still runs, and measures that gap. */
  const agentCalls = useMemo(
    () => new Set(agentToolsFor(env, envState, agentLabel).map((t) => t.name)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState.agentVersions, agentLabel],
  );
  /* Each scenario in the environment's last eight finished runs, oldest
     first — the table's dots. A run it wasn't in reads as hollow. */
  const runHistory = useMemo(() => {
    const runs = runSummaries(env, envState)
      .filter((r) => r.finishedAt && !r.synthetic)
      .slice(-8);
    const byScenario = new Map();
    /* The latest run each scenario was in — its sub-goals column reads it. */
    const latestTask = new Map();
    runs.forEach((r, i) =>
      (r.tasks || []).forEach((t) => {
        const key = t.scenarioId || t.id;
        if (!byScenario.has(key))
          byScenario.set(key, new Array(runs.length).fill(null));
        byScenario.get(key)[i] = t.pulled ? "unscored" : t.status;
        latestTask.set(key, { task: t, name: `Run ${r.ordinal}`, runId: r.id });
      }),
    );
    return { runs, byScenario, latestTask };
  }, [env, envState]);
  const historyOf = (row) => {
    if (!runHistory.runs.length) return null;
    const statuses = runHistory.byScenario.get(row.id) || [];
    return runHistory.runs.map((r, i) => ({
      runId: r.id,
      name: `Run ${r.ordinal}`,
      status: statuses[i] || null,
    }));
  };
  const latestOf = (row) => runHistory.latestTask.get(row.id) || null;
  const rowContext = {
    statusOf,
    stateOf,
    toolsOf,
    answers,
    agentCalls,
    agentLabel,
    historyOf,
    latestOf,
    usageOf: (id) => usage.get(id),
    onStateAction: (kind, row, arg) => onStateAction(kind, row, arg),
    onOpen: (row) => setOpenId(row.id),
  };

  const q = query.trim().toLowerCase();

  /* Row-facet extractors — each returns the id(s) a scenario belongs to
     for one filter dimension. Kept as pure helpers so the popover can
     use the same functions to count how many scenarios each option
     covers. */
  const statusFacets = (r) => {
    const ids = [];
    /* The scenario's state first. Needs attention is a question people
       arrive with, but it narrows the list, so it's a filter like the rest.
       Needs attention = Outdated, Stale, or Re-check that isn't mid-build. */
    const st = stateOf(r);
    ids.push(st.label);
    if (r.proposed) ids.push("Proposed");
    if (needsReview(st)) ids.push("Needs attention");
    if (proofStatus(r, env, viewState).edited) ids.push("Edited");
    return ids;
  };
  const personaFacet = (r) => r?.persona?.name || null;
  const KIND_LABEL = {
    rule: "Rule test",
    trap: "Trap",
    adversarial: "Adversarial",
    edge: "Edge case",
    happy: "Happy path",
  };
  const kindFacet = (r) => {
    const id = String(r?.id || "");
    if (id.includes("-rule-")) return KIND_LABEL.rule;
    if (id.includes("-trap-")) return KIND_LABEL.trap;
    if (id.includes("-adversarial-")) return KIND_LABEL.adversarial;
    if (id.includes("-edge-")) return KIND_LABEL.edge;
    return KIND_LABEL.happy;
  };
  const subgoalFacet = (r) => {
    const subs = subTasksFor(r, env);
    return subs?.[0]?.label || null;
  };

  const orgTags = orgTagsOf(envState);
  const orgGroups = orgGroupsOf(envState);
  const tagNamesOf = (r) =>
    orgTags.filter((t) => (r.tagIds || []).includes(t.id)).map((t) => t.name);
  const groupNamesOf = (r) =>
    orgGroups.filter((g) => g.ids.includes(r.id)).map((g) => g.name);
  const matchesFilters = (r) => {
    /* Tags match any or all, as asked; a group selects its identities. */
    if (
      filters.tag?.length &&
      !filters.tag.some((t) => tagNamesOf(r).includes(t))
    )
      return false;
    if (
      filters.tagAll?.length &&
      !filters.tagAll.every((t) => tagNamesOf(r).includes(t))
    )
      return false;
    if (
      filters.group?.length &&
      !filters.group.some((g) => groupNamesOf(r).includes(g))
    )
      return false;
    if (filters.status?.length) {
      const ids = statusFacets(r);
      if (!filters.status.some((s) => ids.includes(s))) return false;
    }
    if (filters.persona?.length && !filters.persona.includes(personaFacet(r)))
      return false;
    if (filters.kind?.length && !filters.kind.includes(kindFacet(r)))
      return false;
    if (filters.subgoal?.length && !filters.subgoal.includes(subgoalFacet(r)))
      return false;
    return true;
  };

  /*
    filterFields — the shape the shared FilterPanel wants. Every
    dimension is an enum with a fixed choice list. Persona and
    sub-goal come from the actual scenarios so a template's own
    names show up rather than a hard-coded list.
  */
  const personaChoices = useMemo(
    () =>
      [
        ...new Set(
          (selected || []).map((r) => r?.persona?.name).filter(Boolean),
        ),
      ].sort(),
    [selected],
  );
  const subgoalChoices = useMemo(() => {
    const s = new Set();
    (selected || []).forEach((r) =>
      subTasksFor(r, env).forEach((sg) => s.add(sg.label)),
    );
    return [...s].sort();
  }, [selected, env]);
  const filterFields = useMemo(
    () => [
      {
        value: "status",
        label: "State",
        type: "enum",
        choices: [...STATE_CHOICES, "Needs attention", "Edited", "Proposed"],
      },
      {
        value: "persona",
        label: "Persona",
        type: "enum",
        choices: personaChoices,
      },
      {
        value: "kind",
        label: "Kind",
        type: "enum",
        choices: Object.values(KIND_LABEL),
      },
      {
        value: "subgoal",
        label: "Sub-goal",
        type: "enum",
        choices: subgoalChoices,
      },
      {
        value: "group",
        label: "Group",
        type: "enum",
        choices: orgGroups.map((g) => g.name),
      },
      {
        value: "tag",
        label: "Tag — any of",
        type: "enum",
        choices: orgTags.map((t) => t.name),
      },
      {
        value: "tagAll",
        label: "Tag — all of",
        type: "enum",
        choices: orgTags.map((t) => t.name),
      },
      // eslint-disable-next-line react-hooks/exhaustive-deps
    ],
    [personaChoices, subgoalChoices, orgTags, orgGroups],
  );

  const shown = selected.filter((r) => {
    if (!matchesFilters(r)) return false;
    if (!q) return true;
    const hay =
      `${r.name || ""} ${r.summary || ""} ${r.title || ""} ${r.task || ""} ${r.useCase || ""}`.toLowerCase();
    return hay.includes(q);
  });
  /* Groups first, then apply the hide list. Row-level narrowing lives in
     `shown` (search) and everything else — hiding — is expressed as
     "which groups to show" so the interaction stays purely visual. */
  const allGroups = groupScenarios(shown, groupBy, {
    ...env,
    __org: { tagNamesOf, groupNamesOf },
  });
  const shownGroups = allGroups.filter((g) => !hiddenGroupIds.includes(g.id));
  const hiddenCount = allGroups.length - shownGroups.length;

  /*
    Adding scenarios in real life isn't instant — every row goes
    through pre-verification (proved solvable, non-vacuous, pointed
    at a rule) before it lands on the environment. We stand in for
    that here with a scripted ~800ms delay so the "Adding…" toast
    reads as a real status, then swap it for the success toast
    once the patch actually fires. If none of the rows were new
    (all duplicates against what's already selected) we say so
    instead of claiming a nonexistent add.
  */
  const addScenarios = (rows, source) => {
    const existing = new Set(selected.map((s) => s.id));
    const fresh = (rows || []).filter((r) => !existing.has(r.id));
    const attempted = rows?.length || 0;
    const dupeCount = attempted - fresh.length;

    /* Toast up front, so the user knows the click landed even though
       the drawer is already sliding closed. */
    const pendingKey = enqueueSnackbar(
      attempted === 1 ? "Adding 1 scenario…" : `Adding ${attempted} scenarios…`,
      { variant: "info", persist: true },
    );

    setTimeout(() => {
      closeSnackbar(pendingKey);
      if (fresh.length === 0) {
        enqueueSnackbar(
          attempted === 1
            ? "That scenario was already added."
            : "Those scenarios were already added.",
          { variant: "info" },
        );
        return;
      }
      /* Stamp provenance so the row provenance form + "Recent
         additions" strip can attribute this batch. The `source` arg
         from the drawer maps 1:1 onto our source vocabulary; if the
         drawer passes something we don't recognise, fall back to
         manual (the honest thing to say when a human clicked add). */
      const stampSource = SOURCE_MAP[source] || "manual";
      /* A new scenario is proved against the world it is added to, says which
         tools it needs, and records the environment version it was added
         under — the same stamping every add route uses. */
      const batch = stampNewBatch(fresh, {
        env,
        envState,
        source: stampSource,
      });
      /* Proved on add: a row that fails a gate isn't added — it's kept as a
         blocked draft you can open, fix and validate, never silently dropped. */
      const at0 = new Date().toISOString();
      const blockedRows = batch.rejected
        .map((x) => {
          const row = fresh.find((r) => r.id === x.id);
          return row
            ? {
                ...row,
                proposed: { at: at0, source: stampSource },
                blocked: { gate: x.gate, reason: x.reason },
              }
            : null;
        })
        .filter(Boolean);
      const history = [
        ...(envState.importHistory || []),
        {
          at: at0,
          source: stampSource,
          attempted,
          created: batch.rows.length,
          blocked: blockedRows.length,
          duplicate: dupeCount,
        },
      ].slice(-12);
      const kept = batch.rows.length;
      if (!kept) {
        patch({
          scenarios: [...selected, ...blockedRows],
          importHistory: history,
        });
        enqueueSnackbar(
          `${blockedRows.length} kept as blocked drafts — none passed their checks yet. Open them to see why.`,
          { variant: "warning", autoHideDuration: 7000 },
        );
        return;
      }
      /* Sources propose; the user adds. The ones nobody previewed (generated)
         wait as proposals until someone adds them. The rest were picked one
         by one in the drawer — that pick added them.
         Routes that build rows themselves (failing-case variations, the
         Error Feed, production replays) mark them proposed already. */
      const waiting =
        stampSource === "generated" || fresh.every((r) => r.proposed);
      const at = new Date().toISOString();
      const fresh2 = batch.rows.map((r) => ({
        ...r,
        ...(waiting &&
          !r.proposed && { proposed: { at, source: stampSource } }),
      }));
      /* A variation's scenario group: the scenario it came from joins the
         group too (`groupId` = the source scenario's id). */
      const groupSources = new Set(
        fresh2.map((r) => r.groupId).filter(Boolean),
      );
      const grouped = selected.map((s) =>
        groupSources.has(s.id) && !s.groupId ? { ...s, groupId: s.id } : s,
      );
      /* The environment's own source (how it was first populated) is not
         whichever add route was used last. A proposal isn't part of the set
         yet, so it writes no new version — adding it does (approve below). */
      const nextScenarios = {
        scenarios: [...grouped, ...fresh2, ...blockedRows],
        importHistory: history,
        ...(!envState.scenarioSource && { scenarioSource: source }),
      };
      patch(
        waiting
          ? nextScenarios
          : suiteLogPatch(envState, nextScenarios, {
              summary: `Added ${fresh2.length} scenario${fresh2.length === 1 ? "" : "s"}`,
              by: currentUser().name,
            }),
      );
      if (batch.needing) {
        enqueueSnackbar(unanswerableNote(batch), {
          variant: "warning",
          autoHideDuration: 7000,
        });
      }
      if (batch.rejected.length) {
        enqueueSnackbar(
          `${rejectedNote(batch)} Kept as blocked drafts — open them to see why.`,
          { variant: "warning", autoHideDuration: 7000 },
        );
      }
      const base = waiting
        ? `${kept} proposed — review and add them to the environment`
        : kept === 1
          ? "1 scenario added"
          : `${kept} scenarios added`;
      const suffix =
        dupeCount > 0 ? ` · ${dupeCount} already on this environment` : "";
      enqueueSnackbar(`${base}${suffix}`, { variant: "success" });
    }, 800);
  };

  const removeScenario = (id) =>
    patch({ scenarios: selected.filter((s) => s.id !== id) });

  /*
    Bulk-action selection — checkboxes on the table are for acting on
    a group of rows. Source of truth is scenarioSelectionBus so the
    selection survives tab changes: a user can pick scenarios on
    the Scenarios tab, hop over to Evaluations to add an evaluator,
    and come back with the same rows still checked. This tab hydrates
    from the bus on mount instead of resetting to [].
  */
  const busSelection = useSyncExternalStore(
    subscribeScenarioSelection,
    getScenarioSelection,
    getScenarioSelection,
  );
  const selectedIds = useMemo(() => {
    const validIds = new Set((selected || []).map((s) => s.id));
    return (busSelection?.ids || []).filter((id) => validIds.has(id));
  }, [busSelection, selected]);
  const selectedRows = useMemo(
    () => (selected || []).filter((s) => selectedIds.includes(s.id)),
    [selected, selectedIds],
  );
  const handleSelectionChange = (ids) => {
    const rows = (selected || []).filter((s) => ids.includes(s.id));
    publishScenarioSelection({ ids, rows });
  };
  const clearSelection = () => {
    clearScenarioSelection();
  };
  /* One bulk operation on exactly the selected scenarios, after its confirm. */
  const runBulk = (kind, value) => {
    const ids = bulk?.ids || [];
    const rowsSel = bulk?.rows || [];
    const n = ids.length;
    const say = (m) => enqueueSnackbar(m, { variant: "success" });
    if (kind === "tag") {
      patch(tagPatch(envState, ids, value));
      say(`Tagged ${n} scenario${n === 1 ? "" : "s"} ${value}.`);
    } else if (kind === "group") {
      patch(groupPatch(envState, ids, value));
      say(`${n} scenario${n === 1 ? "" : "s"} in ${value}.`);
    } else if (kind === "duplicate") {
      const { copies, ...p } = duplicatePatch(envState, rowsSel);
      patch(p);
      say(
        `${copies.length} draft cop${copies.length === 1 ? "y" : "ies"} made — review them as proposals.`,
      );
    } else if (kind === "validate") {
      const res = validateRows(rowsSel, env, envLabel);
      patch(validatePatch(envState, res));
      const bad = res.filter((x) => !x.result.pass).length;
      say(
        `${n - bad} passed validation${bad ? ` · ${bad} blocked — open them to see why` : ""}.`,
      );
    } else if (kind === "archive") {
      patch(archivePatch(envState, ids));
      clearSelection();
      say(`Archived ${n}. They stay in history and past runs.`);
    } else if (kind === "promote") {
      patch(promotePatch(envState, ids, "regression"));
      say(
        `Promoted ${n} to regression — their checks are frozen and they're must-pass.`,
      );
    } else if (kind === "export") {
      const blob = new Blob([exportScenarios(rowsSel)], {
        type: "application/json",
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${env.id}-scenarios.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      say(`Exported ${n} scenario definitions.`);
    }
    setBulk(null);
  };

  /* Escape key + click outside the *table* both drop the selection.
     "Outside the table" here means anywhere that isn't the scenarios
     list itself or the SelectionBar that acts on it — both carry a
     `data-scenarios-safe` marker below and count as "inside" for this
     check. Portalled UI (menus, popovers, dialogs, drawers, snackbar)
     is also treated as inside so opening the Delete confirmation or
     the group-by menu doesn't kill the selection those actions are
     about to work on.

     Uses `click` (bubble phase) so the target's own React onClick has
     already run by the time we check — a click on Delete inside the
     SelectionBar fires bulkDelete first, then this fires and clears
     what's left. */
  useEffect(() => {
    if (selectedIds.length === 0) return undefined;
    const isSafe = (target) => {
      if (!(target instanceof Element)) return false;
      if (target.closest("[data-scenarios-safe]")) return true;
      return !!target.closest(
        [
          '[role="dialog"]',
          '[role="menu"]',
          '[role="tooltip"]',
          ".MuiPopover-root",
          ".MuiMenu-root",
          ".MuiModal-root",
          ".MuiDrawer-root",
          ".SnackbarContainer-root",
          ".notistack-SnackbarContainer",
          ".notistack-Snackbar",
        ].join(","),
      );
    };
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        clearSelection();
      }
    };
    const onDown = (e) => {
      if (isSafe(e.target)) return;
      clearSelection();
    };
    window.addEventListener("keydown", onKey);
    /* Capture phase on mousedown — fires before any other handler on
       the way down so an ancestor's stopPropagation on click can't hide
       the click from us. Everything that should keep the selection
       alive (rows, group headers, SelectionBar, portalled menus and
       dialogs) is marked as safe below, so this is fine to run first. */
    document.addEventListener("mousedown", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown, true);
    };
  }, [selectedIds.length]);

  /* Edit with builder — pins a Claude-style skill chip inside the
     composer instead of stuffing text into the draft. The chip's
     visible label stays compact ("Edit 2 scenarios"), while the
     full context (scenario names) rides along invisibly as the
     scaffold's prompt so the AI still knows what to act on. User
     types their instruction, the two get concatenated on send. */
  const handleEditSelected = () => {
    if (selectedRows.length === 0) return;
    const count = selectedRows.length;
    const names = selectedRows.map((r) => r.name).filter(Boolean);
    const preview = names.slice(0, 3).join(", ");
    const more = names.length > 3 ? `, +${names.length - 3} more` : "";
    injectComposerScaffold({
      label: `Edit ${count} scenario${count === 1 ? "" : "s"}`,
      prompt: `Edit these ${count} scenarios (${preview}${more}):`,
      icon: "solar:pen-2-linear",
    });
  };

  const bulkDelete = () => {
    if (selectedIds.length === 0) return;
    const removed = (selected || []).filter((s) => selectedIds.includes(s.id));
    const keptIds = new Set(selectedIds);
    patch({ scenarios: (selected || []).filter((s) => !keptIds.has(s.id)) });
    clearSelection();
    const label =
      removed.length === 1
        ? `Removed "${removed[0].name || removed[0].title || "scenario"}"`
        : `Removed ${removed.length} scenarios`;
    const key = enqueueSnackbar(label, {
      variant: "info",
      autoHideDuration: 6000,
      action: (id) => (
        <Button
          size="small"
          sx={{ color: "common.white" }}
          onClick={() => {
            /* Restore in original order — prepend the removed rows to
               whatever the store currently holds. Preserves the row
               order the user was looking at when they clicked delete. */
            patch({
              scenarios: [
                ...removed,
                ...(selected || []).filter((s) => !keptIds.has(s.id)),
              ],
            });
            closeSnackbar(id || key);
          }}
        >
          Undo
        </Button>
      ),
    });
  };

  /* A scenario keeps its id and everything keyed off it — coverage, run
     history, the evals mapped to it — but an edit is never in place: it is a
     new scenario version, bumped by kind (patch · minor · major), and a new
     S version whose summary says the largest bump it caused.

     A minor or major edit is an unproved one: the proof was of the task as
     it read before. Stamping the edit is what makes it show up in the banner
     above rather than keeping a green tick it no longer earns. A cosmetic
     (patch) edit leaves the proof standing. A rewrite of an Outdated
     scenario answers the spec change that made it so. */
  const saveScenario = (row, { metaOnly, bump } = {}) => {
    const name = row.title || row.name || "scenario";
    const next = metaOnly
      ? row
      : applyEditBump(
          bump?.level === "patch"
            ? rewrittenScenario(row)
            : markEdited(rewrittenScenario(row)),
          bump,
        );
    patch(
      suiteLogPatch(
        envState,
        {
          scenarios: selected.map((s) => (s.id === row.id ? next : s)),
        },
        {
          summary: bump?.level
            ? `${name} edited — ${bump.why}`
            : `${name} updated`,
          largestBump: bump?.level || null,
          by: currentUser().name,
        },
      ),
    );
  };

  /* Rows replaced in place, by id — what every state action writes. */
  const replaceRows = (next) => {
    const byId = new Map(next.map((r) => [r.id, r]));
    patch({ scenarios: selected.map((s) => byId.get(s.id) || s) });
  };
  const reprove = (rows) => {
    if (!rows.length) return;
    replaceRows(reproveScenarios(rows, { env, envState: viewState, toolsOf }));
    enqueueSnackbar(
      rows.length === 1
        ? `Re-proved "${rows[0].name || rows[0].title || "scenario"}" on ${envDisplay}`
        : `Re-proved ${rows.length} scenarios on ${envDisplay}`,
      { variant: "success" },
    );
  };
  /* Archived, not deleted: it stays in the set, listed and filterable, and
     the set moves to a new version. Undo puts the row back as it was. */
  const archive = (row, note) => {
    const next = archiveScenario(row, note);
    const name = row.title || row.name || "scenario";
    patch(
      suiteLogPatch(
        envState,
        {
          scenarios: selected.map((s) => (s.id === row.id ? next : s)),
        },
        {
          summary: `Archived ${name}${note ? ` — ${note}` : ""}`,
          by: currentUser().name,
        },
      ),
    );
    const key = enqueueSnackbar(`Archived "${name}" — it won't run or count`, {
      variant: "info",
      autoHideDuration: 6000,
      action: (id) => (
        <Button
          size="small"
          sx={{ color: "common.white" }}
          onClick={() => {
            replaceRows([row]);
            closeSnackbar(id || key);
          }}
        >
          Undo
        </Button>
      ),
    });
  };
  const restore = (row) => {
    patch(
      suiteLogPatch(
        envState,
        {
          scenarios: selected.map((s) =>
            s.id === row.id ? restoreScenario(row) : s,
          ),
        },
        {
          summary: `Restored ${row.title || row.name || "scenario"}`,
          by: currentUser().name,
        },
      ),
    );
  };
  /* Must-pass (a release blocker): a mark on the set, not the scenario's
     content (PRD v7 §4.10.3) — the scenario set moves to a new S version,
     logged with who marked what; the scenario keeps its version and proof.
     An improvement that breaks a must-pass scenario is rejected. */
  const setMustPass = (rows, on) => {
    const targets = rows.filter((r) => !!r.critical !== on && !r.archived);
    if (!targets.length) return;
    const byId = new Map(targets.map((r) => [r.id, setBlocker(r, on)]));
    const one = targets[0].name || targets[0].title || "scenario";
    patch(
      suiteLogPatch(
        envState,
        {
          scenarios: selected.map((s) => byId.get(s.id) || s),
        },
        {
          summary:
            targets.length === 1
              ? `${one} ${on ? "marked must-pass" : "no longer must-pass"}`
              : `${targets.length} scenarios ${on ? "marked must-pass" : "no longer must-pass"}`,
          by: currentUser().name,
        },
      ),
    );
    enqueueSnackbar(
      targets.length === 1
        ? on
          ? `"${one}" is must-pass — an improvement that breaks it is rejected`
          : `"${one}" is no longer must-pass`
        : on
          ? `${targets.length} scenarios marked must-pass`
          : `${targets.length} scenarios no longer must-pass`,
      { variant: "success" },
    );
  };

  /* Proposals join the environment only once someone adds them. */
  const approve = (input) => {
    /* A blocked draft can't join the set until it passes. */
    const rows = input.filter((r) => !r.blocked);
    if (input.length > rows.length)
      enqueueSnackbar(
        `${input.length - rows.length} blocked — open ${input.length - rows.length === 1 ? "it" : "them"}, fix what failed and validate first.`,
        { variant: "warning" },
      );
    if (!rows.length) return;
    const ids = new Set(rows.map((r) => r.id));
    patch(
      suiteLogPatch(
        envState,
        {
          scenarios: selected.map((s) => {
            if (!ids.has(s.id)) return s;
            const { proposed: _p, ...rest } = s;
            return rest;
          }),
        },
        {
          summary: `Added ${rows.length} proposed scenario${rows.length === 1 ? "" : "s"}`,
          by: currentUser().name,
        },
      ),
    );
    enqueueSnackbar(
      rows.length === 1
        ? "Added — it joins the environment and runs from now on"
        : `Added ${rows.length} — they join the environment and run from now on`,
      { variant: "success" },
    );
  };
  /* What each state asks for, from the table, the list and the drawer alike. */
  const onStateAction = (kind, row, arg) => {
    if (ro && kind !== "subgoal") return;
    if (kind === "reprove") reprove([row]);
    else if (kind === "rewrite" || kind === "edit") {
      setOpenId(null);
      setEditing(row);
    } else if (kind === "archive") archive(row, arg);
    else if (kind === "restore") restore(row);
    else if (kind === "update") replaceRows([row]);
    else if (kind === "approve") approve([row]);
    else if (kind === "blocker") setMustPass([row], arg ?? !row.critical);
    else if (kind === "subgoal") {
      setOpenId(null);
      setGoalId(arg || null);
      setMode("subgoals");
    }
  };

  /*
    The baseline. An environment's first run is what every improvement is
    measured against, so until one has finished the tab says so and puts
    the run first. Once any run exists there is nothing extra to show.
  */
  const finishedRuns = (envState.runs || []).filter((r) => !r.synthetic).length;
  const needsBaseline =
    !buildMode &&
    !viewing &&
    !!onStartRun &&
    live.length > 0 &&
    finishedRuns === 0;
  /* What the baseline takes — the header Run's count (runScopeOf). */
  const baselineScope = needsBaseline ? runScopeOf(env, envState) : null;
  const readiness = runReadiness(envState);
  const baselineWith = baselineAlso.filter((id) =>
    groupCandidates.some((c) => c.envId === id && c.ready),
  );

  /* The chosen depth overrides the environment's own, and every generation
     route is handed the adjusted environment rather than the original. */
  const depth = envState.difficulty || env.difficulty || "Advanced";
  const genEnv = { ...env, difficulty: depth };

  /*
    Scenarios are proved against a version of the world, and this environment
    has moved since some of them were proved. Nothing about that breaks loudly
    — the runs still produce numbers — so it has to be said here.

    In buildMode this is suppressed: the environment was just derived, it is
    on v1 by definition and nothing has drifted. Showing "12 of 32 need
    re-proving · this environment is on v3" on a freshly-built environment
    was reading a workspace demo story into a screen where none of it had
    happened yet.
  */
  const stale =
    buildMode || viewing ? [] : staleScenarios(selected, env, envState);

  /*
    The change review. After any change — to the suite or the environment —
    every scenario answers the doc's two questions, and the ones that need
    someone are counted by state. Re-check only needs its proof re-run;
    Outdated needs a rewrite, or retiring.
  */
  const review = useMemo(() => {
    if (buildMode) return null;
    const by = { outdated: [], stale: [], recheck: [] };
    selected.forEach((r) => {
      const st = stateOf(r);
      if (needsReview(st)) by[st.id].push(r);
    });
    return {
      ...by,
      reprovable: by.recheck.filter(
        (r) => stateActionOf(stateOf(r)) === "reprove",
      ),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildMode, selected, stateById]);

  /* Auto re-prove the moment we notice drift. Every scenario whose proof
     still holds gets restamped to the current env version; the ~20% that
     don't survive keep the old stamp plus a `provedBroke` flag so the
     banner and the row treatment can call them out. Deterministic per
     scenario id + env version, so a reload lands on the same result. */
  const autoRunRef = useRef(null);
  useEffect(() => {
    if (buildMode || viewing) return;
    /* Forget the last pass once nothing is stale, so coming back to a version
       re-proves again — keyed on the count alone, a return visit with the
       same number of stale rows was skipped and stayed "Needs re-proof". */
    if (stale.length === 0) {
      autoRunRef.current = null;
      return;
    }
    const key = `${env.id}::${envState?.activeEnvVersion || "v1"}::${stale.map((s) => s.id).join(",")}`;
    if (autoRunRef.current === key) return;
    autoRunRef.current = key;
    patch({ scenarios: autoReprove(selected, env, envState) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildMode, viewing, env.id, envState?.activeEnvVersion, stale.length]);

  /* Search, a filter or a hidden group narrows the list — the footer says so. */
  const narrowed = q.length > 0 || hiddenCount > 0 || filterCount > 0;

  /* The Coverage button's hover: the drawer's three numbers, on the same rows. */
  const coverageRows = useMemo(() => liveScenarios(selected), [selected]);
  const coverage = useMemo(
    () => (selected.length ? coverageSummary(coverageRows, env) : null),
    [selected.length, coverageRows, env],
  );

  /*
    The notices that used to stack above the table, as rows of the toolbar's
    Needs review list — each with the actions its banner had. `attention`
    counts toward the button's number.
  */
  const improvementTotal = useMemo(
    () =>
      buildMode
        ? 0
        : pendingImprovementScenarios(envState).reduce(
            (n, g) => n + g.rows.length,
            0,
          ),
    [buildMode, envState],
  );
  /* How much of the set improvements can't break, and why. */
  const protection = protectionTally(selected, latestOf);
  const notices = [
    review &&
      review.outdated.length + review.stale.length + review.recheck.length >
        0 && {
        id: "change-review",
        icon: "solar:refresh-circle-linear",
        attention: true,
        title: [
          review.outdated.length > 0 && {
            n: review.outdated.length,
            label: "Outdated",
            red: true,
          },
          review.stale.length > 0 && { n: review.stale.length, label: "Stale" },
          review.recheck.length > 0 && {
            n: review.recheck.length,
            label: "Re-check",
          },
        ]
          .filter(Boolean)
          .map((c, i) => (
            <Fragment key={c.label}>
              {i > 0 && (
                <Box
                  component="span"
                  sx={{ color: "text.disabled", fontWeight: 400 }}
                >
                  {" · "}
                </Box>
              )}
              <Box
                component="span"
                sx={{ color: c.red ? "status.danger.fg" : "text.primary" }}
              >{`${c.n} ${c.label}`}</Box>
            </Fragment>
          )),
        sub: `Checked against environment ${envDisplay}. Flagged ones run but don't count until fixed.`,
        actions: [
          {
            label: "Review",
            primary: true,
            onClick: () =>
              setFilters({ status: ["Outdated", "Stale", "Re-check"] }),
          },
          !ro &&
            review.reprovable.length > 0 && {
              label: `Re-prove ${review.reprovable.length}`,
              onClick: () => reprove(review.reprovable),
            },
        ].filter(Boolean),
      },
    proposed.length > 0 && {
      id: "proposed",
      icon: "solar:inbox-linear",
      attention: true,
      title: `${proposed.length} proposed scenario${proposed.length === 1 ? "" : "s"} waiting to be added`,
      sub: "Generated, failing-case, Error Feed and production scenarios join the environment only once you add them. Until then they don't run.",
      actions: [
        {
          label: "Review",
          onClick: () => setFilters({ status: ["Proposed"] }),
        },
        !ro && {
          label: `Add ${proposed.length}`,
          primary: true,
          onClick: () => approve(proposed),
        },
      ].filter(Boolean),
    },
    improvementTotal > 0 && {
      id: "improvements",
      icon: "solar:magic-stick-3-linear",
      attention: true,
      title: `${improvementTotal} scenario${improvementTotal === 1 ? "" : "s"} from improvements ${improvementTotal === 1 ? "isn't" : "aren't"} in this environment`,
      sub: "Improvements wrote these to reproduce what they were fixing. They stay with the improvement until you add them.",
      actions: [{ label: "Review", onClick: () => setImprovOpen(true) }],
    },
    qualityReview &&
      selected.length > 0 && {
        id: "quality",
        icon: "solar:shield-check-linear",
        attention: qualityReview.state !== "ready",
        title: (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            component="span"
          >
            <span>Quality review</span>
            <QualityReviewChip gate={qualityReview} />
          </Stack>
        ),
        sub: <QualityReviewFacts gate={qualityReview} />,
        actions: [
          {
            label:
              qualityReview.state === "spot-check" ? "Spot-check" : "Review",
            onClick: () => setGateOpen(true),
          },
        ],
      },
  ].filter(Boolean);

  return (
    /* The build pane already frames this panel, so it only needs a sliver of
       side padding there; the workspace keeps the full inset. */
    <Box sx={{ py: 2, px: buildMode ? 1 : 2, "--scn-head": `${headH}px` }}>
      {/*
        No heading — the tab names the page, and its hover hint says what a
        scenario is. Everything that used to stack above the table sits in
        one toolbar row: search, Filter, Display, Coverage, History, the
        notices (Needs review) and Add scenarios. The set's counts live in
        the footer under the results.

        The toolbar pins to the top while the list scrolls, so search,
        grouping and the selection actions are always in reach through 80+
        rows. A hairline appears once it is actually stuck.
      */}
      <Box ref={headSentinel} sx={{ height: 0 }} />
      <Box
        ref={headRef}
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 4,
          bgcolor: "background.paper",
          mt: -2,
          pt: 2,
          mx: buildMode ? -1 : -2,
          px: buildMode ? 1 : 2,
          mb: 1.5,
          borderBottom: "1px solid",
          borderColor: headStuck ? "divider" : "transparent",
          transition: "border-color .15s ease",
        }}
      >
        {(selected.length > 0 || mode === "subgoals") && (
          <Box sx={{ pb: 1.25 }}>
            {/* Selecting rows swaps the toolbar for the selection bar in the
              same row and height, so the table never jumps. Marked safe so
              a click on Edit / Delete inside the bar doesn't trip the
              outside-the-table clear-selection. */}
            {!ro && selectedIds.length > 0 && mode === "scenarios" ? (
              <Box data-scenarios-safe="selection-bar">
                <SelectionBar
                  count={selectedIds.length}
                  onBulk={
                    ro
                      ? null
                      : (kind) =>
                          /* The exact scenarios, frozen when the dialog opens —
                             a click inside it can't change what it acts on. */
                          setBulk({
                            kind,
                            ids: selectedIds,
                            rows: selectedRows,
                          })
                  }
                  matching={{
                    count: shown.length,
                    onSelectAll: () =>
                      handleSelectionChange(shown.map((r) => r.id)),
                  }}
                  onEdit={handleEditSelected}
                  onDelete={bulkDelete}
                  onMustPass={() =>
                    setMustPass(
                      selectedRows,
                      !selectedRows.every((r) => r.critical),
                    )
                  }
                  allMustPass={
                    selectedRows.length > 0 &&
                    selectedRows.every((r) => r.critical)
                  }
                  onClear={clearSelection}
                  run={
                    selectionRun && onStartRun
                      ? {
                          ...selectionRun,
                          onRun: () =>
                            onStartRun(
                              selectedIds,
                              Number(selectionRun.repeats) || 3,
                            ),
                        }
                      : null
                  }
                />
              </Box>
            ) : (
              <Stack
                ref={barRef}
                direction="row"
                alignItems="center"
                sx={{ gap: 1, minHeight: 30 }}
              >
                {mode === "scenarios" && (
                  <TextField
                    size="small"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={
                      barFit === "narrow" ? "Search…" : "Search scenarios…"
                    }
                    InputProps={{
                      /* Same 30px as the buttons beside it. */
                      sx: {
                        typography: "s2",
                        height: 30,
                        "& .MuiInputBase-input": { py: 0 },
                      },
                      startAdornment: (
                        <Box
                          sx={{
                            pr: 0.75,
                            pl: 0.25,
                            display: "flex",
                            color: "text.tertiary",
                          }}
                        >
                          <Iconify icon="solar:magnifer-linear" width={14} />
                        </Box>
                      ),
                    }}
                    /* Search keeps a readable width — the toolbar folds its
                       other controls before it squeezes this. */
                    sx={{
                      width: barFit === "narrow" ? 160 : 220,
                      flexShrink: 0,
                    }}
                  />
                )}
                {mode === "scenarios" && (
                  <>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={(e) => setFilterAnchor(e.currentTarget)}
                      startIcon={
                        <Iconify icon="solar:filter-linear" width={14} />
                      }
                      endIcon={
                        <Iconify
                          icon="solar:alt-arrow-down-linear"
                          width={12}
                        />
                      }
                      sx={{
                        flexShrink: 0,
                        color: filterCount ? "primary.main" : "text.primary",
                        borderColor: filterCount ? "primary.main" : "divider",
                      }}
                    >
                      Filter{filterCount ? ` (${filterCount})` : ""}
                    </Button>
                    <ObjectFilterPanel
                      anchorEl={filterAnchor}
                      open={!!filterAnchor}
                      onClose={() => setFilterAnchor(null)}
                      filterFields={filterFields}
                      currentFilters={filters}
                      onApply={applyFilters}
                      aiPlaceholder="Ask AI — e.g. 'must-pass scenarios with the impatient persona'"
                      placement="bottom-start"
                    />
                  </>
                )}
                {/* Grouping is the one display choice; the shared sub-goal list
                (opened from a scenario's "used by N") has its way back here. */}
                {mode === "scenarios" ? (
                  <>
                    <GroupByMenu
                      value={groupBy}
                      onChange={setGroupBy}
                      options={SCENARIO_GROUPINGS}
                      compact={barFit !== "full"}
                    />
                    {barFit === "full" ? (
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => setOrgOpen(true)}
                        startIcon={
                          <Iconify icon="solar:tag-linear" width={14} />
                        }
                        sx={{
                          flexShrink: 0,
                          color: "text.primary",
                          borderColor: "divider",
                        }}
                      >
                        Tags &amp; groups
                      </Button>
                    ) : barFit === "compact" ? (
                      <Tooltip arrow title="Tags & groups">
                        <IconButton
                          size="small"
                          aria-label="Tags & groups"
                          onClick={() => setOrgOpen(true)}
                          sx={{
                            flexShrink: 0,
                            border: "1px solid",
                            borderColor: "divider",
                            color: "text.primary",
                          }}
                        >
                          <Iconify icon="solar:tag-linear" width={16} />
                        </IconButton>
                      </Tooltip>
                    ) : null}
                  </>
                ) : (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => {
                      setGoalId(null);
                      setMode("scenarios");
                    }}
                    startIcon={
                      <Iconify icon="solar:arrow-left-linear" width={14} />
                    }
                    sx={{
                      flexShrink: 0,
                      color: "text.primary",
                      borderColor: "divider",
                    }}
                  >
                    Scenarios
                  </Button>
                )}
                {mode === "scenarios" && (
                  <>
                    {barFit !== "narrow" && (
                      <>
                        {/* Coverage — the three numbers on hover, the full breakdown
                    (axes, forced overlays, pairwise grid) in the drawer. */}
                        <Tooltip
                          arrow
                          title={
                            coverage
                              ? `Coverage — axes ${coverage.axes} · pairs ${coverage.pairs} · forced ${coverage.forced}`
                              : "Coverage"
                          }
                        >
                          <IconButton
                            size="small"
                            aria-label="Coverage"
                            onClick={() => setCoverageOpen(true)}
                            sx={{
                              flexShrink: 0,
                              border: "1px solid",
                              borderColor: coverageOpen
                                ? "text.primary"
                                : "divider",
                              color: "text.primary",
                              bgcolor: coverageOpen
                                ? "action.selected"
                                : "transparent",
                            }}
                          >
                            <Iconify
                              icon="solar:chart-square-linear"
                              width={16}
                            />
                          </IconButton>
                        </Tooltip>
                        {/* How the suite grew — the history drawer, as an icon. */}
                        <Tooltip
                          arrow
                          title={
                            historyOpen
                              ? "Close history"
                              : "History — how the suite grew"
                          }
                        >
                          <IconButton
                            size="small"
                            aria-label="History"
                            onClick={() => setHistoryOpen((v) => !v)}
                            sx={{
                              flexShrink: 0,
                              border: "1px solid",
                              borderColor: historyOpen
                                ? "text.primary"
                                : "divider",
                              color: "text.primary",
                              bgcolor: historyOpen
                                ? "action.selected"
                                : "transparent",
                            }}
                          >
                            <Iconify icon="solar:history-linear" width={16} />
                          </IconButton>
                        </Tooltip>
                      </>
                    )}
                    {barFit === "narrow" && (
                      <>
                        <Tooltip
                          arrow
                          title="More — tags & groups, coverage, history"
                        >
                          <IconButton
                            size="small"
                            aria-label="More"
                            onClick={(e) => setMoreAnchor(e.currentTarget)}
                            sx={{
                              flexShrink: 0,
                              border: "1px solid",
                              borderColor: moreAnchor
                                ? "text.primary"
                                : "divider",
                              color: "text.primary",
                            }}
                          >
                            <Iconify icon="solar:menu-dots-bold" width={16} />
                          </IconButton>
                        </Tooltip>
                        <Menu
                          anchorEl={moreAnchor}
                          open={!!moreAnchor}
                          onClose={() => setMoreAnchor(null)}
                        >
                          {[
                            [
                              "Tags & groups",
                              "solar:tag-linear",
                              () => setOrgOpen(true),
                            ],
                            [
                              "Coverage",
                              "solar:chart-square-linear",
                              () => setCoverageOpen(true),
                            ],
                            [
                              historyOpen ? "Close history" : "History",
                              "solar:history-linear",
                              () => setHistoryOpen((v) => !v),
                            ],
                          ].map(([label, icon, act]) => (
                            <MenuItem
                              key={label}
                              onClick={() => {
                                setMoreAnchor(null);
                                act();
                              }}
                              sx={{ typography: "s2", gap: 1 }}
                            >
                              <Iconify
                                icon={icon}
                                width={16}
                                sx={{ color: "text.secondary" }}
                              />
                              {label}
                            </MenuItem>
                          ))}
                        </Menu>
                      </>
                    )}
                    <NeedsReviewMenu
                      notices={notices}
                      compact={barFit !== "full"}
                    />
                  </>
                )}
                <Box sx={{ flex: 1 }} />
                {/* Outlined while the baseline hasn't run — that is the one
                primary action on the tab until it has. When the list is
                empty, the placeholder below carries its own Add button. */}
                {selected.length > 0 && mode === "scenarios" && (
                  <Tooltip arrow title={ro ? roReason : ""}>
                    <span>
                      <Button
                        variant={needsBaseline ? "outlined" : "contained"}
                        color={needsBaseline ? "inherit" : "primary"}
                        size="small"
                        disabled={ro}
                        onClick={() => setAdding(true)}
                        startIcon={
                          <Iconify icon="solar:add-circle-linear" width={16} />
                        }
                        sx={{
                          flexShrink: 0,
                          whiteSpace: "nowrap",
                          ...(needsBaseline && {
                            color: "text.primary",
                            borderColor: "divider",
                          }),
                        }}
                      >
                        {barFit === "narrow" ? "Add" : "Add scenarios"}
                      </Button>
                    </span>
                  </Tooltip>
                )}
              </Stack>
            )}
          </Box>
        )}

        {needsBaseline && selectedIds.length === 0 && mode === "scenarios" && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              mb: 1.25,
              px: 2,
              py: 1.25,
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.neutral",
            }}
          >
            <Iconify
              icon="solar:flag-linear"
              width={16}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                No baseline yet
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                The first run is the baseline improvements are measured against.
              </Typography>
            </Box>
            <AlsoRunIn
              candidates={groupCandidates}
              value={baselineAlso}
              onChange={setBaselineAlso}
              agentVersion={currentAgentVersion(envState)?.label}
            />
            <Tooltip
              arrow
              title={
                readiness.ready
                  ? `${baselineScope?.sitOut ? `Runs ${baselineScope.count} of the ${baselineScope.runnable} scenarios that can run (${baselineScope.sitOut} wait on Needs input)` : `Runs all ${baselineScope?.count ?? live.length} scenarios`}, 3 times each`
                  : readiness.reason
              }
            >
              <span>
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  disabled={!readiness.ready}
                  onClick={() =>
                    onStartRun(
                      baselineScope?.sitOut && !baselineWith.length
                        ? baselineScope.ids
                        : undefined,
                      3,
                      { also: baselineWith },
                    )
                  }
                  startIcon={<Iconify icon="solar:play-bold" width={14} />}
                  sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
                >
                  {baselineWith.length
                    ? `Run baseline in ${baselineWith.length + 1} environments`
                    : "Run baseline"}
                </Button>
              </span>
            </Tooltip>
          </Stack>
        )}
      </Box>

      {/* The shared definitions the suite grades with, one entry per meaning. */}
      {mode === "subgoals" && (
        <SubGoalsPanel
          embedded
          openGoalId={goalId}
          onGoalClosed={() => setGoalId(null)}
          env={env}
          envState={envState}
          patch={patch}
          locked={ro}
          onGo={() => setMode("scenarios")}
        />
      )}

      {mode !== "scenarios" ? null : selected.length === 0 ? (
        <RoutePlaceholder env={genEnv} onAdd={() => setAdding(true)} />
      ) : (
        /*
          No title/subtitle on the section card — the page heading above
          already names this list. The card's top row is a shared
          toolbar (search · filter · list/table tabs) that both views
          read from, so filters survive a view switch.
        */
        /* No frame around the toolbar — it is a row of controls over the
           batch containers, not a container of its own. */
        <Box>
          {shownGroups.length === 0 ? (
            <Box sx={{ px: 2.5, py: 6, textAlign: "center" }}>
              <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                {hiddenCount > 0 && q.length === 0
                  ? "Every group is hidden — click Show all to bring them back."
                  : "No scenarios match your search."}
              </Typography>
            </Box>
          ) : (
            <>{/* Batches render as a timeline below this toolbar card. */}</>
          )}
        </Box>
      )}

      {/* Each batch in its own container, newest first — who added it and
          when on top, its scenarios under the Group by inside.
          Marked safe so clicks on rows, checkboxes, and group headers
          keep the selection intact. */}
      {shown.length > 0 && mode === "scenarios" && (
        <Box data-scenarios-safe="list">
          <ScenarioRowContext.Provider value={rowContext}>
            {(() => {
              const all = batchesFrom(shownGroups);
              /* History is of the whole suite — not of what the search or a
               filter happens to show. */
              const history = groupByBatch(selected);
              const asOfBatch = asOf
                ? history.find((b) => b.batchId === asOf)
                : null;
              const cutoff = asOfBatch ? Date.parse(asOfBatch.addedAt) : null;
              const batches =
                cutoff == null
                  ? all
                  : all.filter((b) => Date.parse(b.meta.addedAt) <= cutoff);
              const shownCount =
                cutoff == null
                  ? selected.length
                  : history
                      .filter((b) => Date.parse(b.addedAt) <= cutoff)
                      .reduce((a, b) => a + b.scenarios.length, 0);
              const pick = (id) => {
                const target = history.find((b) => b.batchId === id);
                /* A batch hidden by the as-of view comes back into view first. */
                if (asOfBatch && target && Date.parse(target.addedAt) > cutoff)
                  setAsOf(null);
                setFlashId(id);
                clearTimeout(flashTimer.current);
                flashTimer.current = setTimeout(() => setFlashId(null), 1600);
                requestAnimationFrame(() =>
                  requestAnimationFrame(() => {
                    document
                      .getElementById(`batch-${id}`)
                      ?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }),
                );
              };
              const containers = (
                <BatchContainers
                  batches={batches}
                  flashId={flashId}
                  latestId={history[0]?.batchId}
                  view={view}
                  tableProps={{
                    env,
                    envState: viewState,
                    envVersion: envLabel,
                    onEdit: setEditing,
                    onRemove: removeScenario,
                    onHideGroup: toggleGroupHidden,
                    selectedIds,
                    onSelectionChange: handleSelectionChange,
                    locked: ro,
                  }}
                  renderList={(innerGroups) => (
                    <GroupedScenarioList
                      groups={innerGroups}
                      env={env}
                      envState={viewState}
                      buildMode={buildMode}
                      onEdit={setEditing}
                      onRemove={removeScenario}
                      onHideGroup={toggleGroupHidden}
                      selectedIds={selectedIds}
                      onSelectionChange={handleSelectionChange}
                      locked={ro}
                    />
                  )}
                />
              );
              return (
                <>
                  <AsOfBanner
                    batch={asOfBatch}
                    shown={shownCount}
                    total={selected.length}
                    hiddenBatches={
                      history.filter(
                        (b) => cutoff != null && Date.parse(b.addedAt) > cutoff,
                      ).length
                    }
                    onBack={() => setAsOf(null)}
                  />
                  {!asOfBatch && <ProtectionLine tally={protection} />}
                  {containers}
                  {/* Opens from the right edge like Figma's version history, and
                    stays open while you scroll and click the batches. */}
                  <SideDrawer
                    persistent
                    open={historyOpen}
                    onClose={() => setHistoryOpen(false)}
                    width={480}
                  >
                    <BatchHistoryPanel
                      batches={history}
                      runs={(envState.runs || []).filter((r) => !r.synthetic)}
                      versions={environmentVersions(env, envState).map((v) => ({
                        ...v,
                        shownAs: firstSnapshotOfWorld(env, envState, v.label)
                          ?.label,
                      }))}
                      total={live.filter((r) => !r.proposed).length}
                      asOf={asOfBatch ? asOf : null}
                      onPick={pick}
                      onViewAsOf={(id) => {
                        setAsOf(id);
                        if (id) pick(id);
                      }}
                      onClose={() => setHistoryOpen(false)}
                    />
                  </SideDrawer>
                  <TagsGroupsManager
                    open={orgOpen}
                    onClose={() => setOrgOpen(false)}
                    envState={envState}
                    patch={patch}
                    readOnly={ro}
                    onShowGroup={(name) => setFilters({ group: [name] })}
                  />
                  {bulk && (
                    <BulkOrgDialog
                      kind={bulk.kind}
                      ids={bulk.ids}
                      env={env}
                      envState={envState}
                      onClose={() => setBulk(null)}
                      onConfirm={runBulk}
                    />
                  )}
                  <ScenarioDrawer
                    row={selected.find((r) => r.id === openId) || null}
                    env={env}
                    envState={viewState}
                    state={
                      openId && selected.some((r) => r.id === openId)
                        ? stateOf(selected.find((r) => r.id === openId))
                        : null
                    }
                    onClose={() => setOpenId(null)}
                    onAction={onStateAction}
                    onEdit={(r) => {
                      setOpenId(null);
                      setEditing(r);
                    }}
                    locked={ro}
                  />
                </>
              );
            })()}
          </ScenarioRowContext.Provider>
        </Box>
      )}

      {/* The footer under the results. When search, a filter or a hidden
          group narrows the list, the count and Clear sit here rather than
          crowding the toolbar; otherwise it carries the set's own counts,
          which used to be the heading. Marked safe so Clear doesn't drop a
          selection. */}
      {mode === "scenarios" && selected.length > 0 && (
        <Stack
          data-scenarios-safe="footer"
          direction="row"
          alignItems="center"
          sx={{
            mt: 1.25,
            pt: 1,
            px: 0.5,
            gap: 1,
            minHeight: 30,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {narrowed ? (
            <>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.secondary",
                  whiteSpace: "nowrap",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`${shown.length} of ${selected.length} scenarios${hiddenCount > 0 ? ` · ${hiddenCount} group${hiddenCount === 1 ? "" : "s"} hidden` : ""}`}
              </Typography>
              <Button
                size="small"
                onClick={() => {
                  setQuery("");
                  setHiddenGroupIds([]);
                  setFilters({});
                }}
                sx={{ color: "text.secondary", minWidth: 0 }}
              >
                {hiddenCount > 0 && !q && !filterCount ? "Show all" : "Clear"}
              </Button>
            </>
          ) : (
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {`${live.length} scenario${live.length === 1 ? "" : "s"}`}
              {[
                staleCount > 0 &&
                  ` · ${staleCount} stale for agent ${agentLabel}`,
                archivedCount > 0 && ` · ${archivedCount} archived`,
                viewing && ` · viewing environment ${envDisplay}, read-only`,
              ]
                .filter(Boolean)
                .join("")}
            </Typography>
          )}
        </Stack>
      )}

      <SideDrawer
        open={coverageOpen}
        onClose={() => setCoverageOpen(false)}
        width={{ xs: "100%", md: 960 }}
      >
        <CoverageMatrix
          scenarios={live}
          env={env}
          onClose={() => setCoverageOpen(false)}
        />
      </SideDrawer>

      <SideDrawer
        open={gateOpen && !!qualityReview}
        onClose={() => setGateOpen(false)}
        width={{ xs: "100%", md: 640 }}
      >
        {gateOpen && qualityReview && (
          <QualityReviewPanel
            gate={qualityReview}
            rows={selected}
            env={env}
            connect={isConnectOnly(env)}
            locked={ro}
            onAnswer={answerSpotCheck}
            onOpenScenario={(id) => {
              setGateOpen(false);
              setOpenId(id);
            }}
            onClose={() => setGateOpen(false)}
          />
        )}
      </SideDrawer>

      {/*
        Gate-rejects panel intentionally suppressed: the table shows
        the full scenario list directly, so a "10 drafted · 5 kept ·
        5 rejected" filter below it just reads as if we're hiding
        rows. Kept the import around in case we want to bring it
        back behind a flag later.
      */}

      {/* Coverage now lives at the top of the tab (collapsed by
          default) so users see the three summary numbers on landing
          instead of having to scroll past the whole list. */}

      {/* Scenarios improvements wrote — reviewed from the Needs review list. */}
      {!buildMode && (
        <ImprovementScenariosDialog
          envState={envState}
          patch={patch}
          locked={ro}
          open={improvOpen}
          onClose={() => setImprovOpen(false)}
        />
      )}
      <AddScenariosDrawer
        open={adding}
        onClose={() => setAdding(false)}
        env={genEnv}
        envState={envState}
        selected={selected}
        onAdd={addScenarios}
        onBuilderPrompt={onBuilderPrompt}
        initialRoute={addRoute}
      />
      <ScenarioEditor
        open={!!editing}
        onClose={() => setEditing(null)}
        row={editing}
        env={env}
        envState={envState}
        onSave={saveScenario}
        onBuilderPrompt={onBuilderPrompt}
      />
    </Box>
  );
}

ScenariosStep.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  buildMode: PropTypes.bool,
  onBuilderPrompt: PropTypes.func,
  onStartRun: PropTypes.func,
  selectionRun: PropTypes.object,
  groupCandidates: PropTypes.array,
  locked: PropTypes.bool,
  onFork: PropTypes.func,
};

/**
 * Shown when the environment has no scenarios at all.
 *
 * Rare — they are normally derived from the agent — but a blank half-screen
 * makes the step look broken rather than empty, so this shows what the step
 * produces: the shape of a scenario, built from this environment's own derived
 * rows rather than invented ones.
 */
function RoutePlaceholder({ env, onAdd }) {
  const sample = generatedPool(env).slice(0, 3);

  return (
    <Box sx={{ py: 5, px: 2, textAlign: "center" }}>
      <PickRouteIllustration />
      <Typography sx={{ typography: "m2", fontWeight: 600, mt: 2 }}>
        No scenarios yet
      </Typography>
      <Typography sx={{ typography: "s1", color: "text.tertiary", mt: 0.5 }}>
        Whichever route you add from, you end up with a list of tasks like this.
      </Typography>
      <Button
        variant="contained"
        color="primary"
        size="small"
        onClick={onAdd}
        startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
        sx={{ mt: 2 }}
      >
        Add scenarios
      </Button>

      <Box
        sx={{
          maxWidth: 640,
          mx: "auto",
          mt: 3,
          textAlign: "left",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          overflow: "hidden",
          bgcolor: "background.paper",
        }}
      >
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {sample.map((r) => (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{ px: 2, py: 1 }}
            >
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
                  {r.title}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {r.task}
                </Typography>
              </Box>
              <Box
                sx={{
                  width: 180,
                  flexShrink: 0,
                  display: { xs: "none", md: "block" },
                }}
              >
                <PersonaBadge persona={r.persona} compact />
              </Box>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                ~{r.turns}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>

      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          fontStyle: "italic",
          mt: 1.5,
        }}
      >
        Sample preview — add scenarios to build your own.
      </Typography>
    </Box>
  );
}
RoutePlaceholder.propTypes = {
  env: PropTypes.object.isRequired,
  onAdd: PropTypes.func,
};

/* ── grouping ────────────────────────────────────────────────────────────── */

/**
 * Use-case-based grouping.
 *
 * Kind buckets (Tool use / Rule enforcement / Data traps / …) group by
 * how the scenario was *derived*, which is a builder concept. A reader
 * scanning 30+ scenarios cares about *what task the agent is being
 * asked to do*: verify identity, look up an order, refuse a refund,
 * handle a saved-card record. Those are the real use cases and they
 * cut across the derivation kinds.
 *
 * The scenario's title carries this cleanly:
 *   Core   → "Routine task using {tool_name}"  → group by the tool
 *   Rule   → the rule text (short, one per rule)
 *   Trap   → "{table_name}: {note}"            → group by the table
 *   Adv    → the template title (Instruction override, Authority claim…)
 *   Edge   → the template title
 *
 * So every group's label is what the scenarios in it are actually
 * doing, not the builder bucket they fell out of.
 */
const humanize = (s = "") =>
  s
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();

const deriveUseCase = (row) => {
  /*
    The scenario now carries its own use-case sentence — the mock stamps
    `row.useCase` with a spec-line description of what the group tests
    ("Verify a caller's identity before touching account state"). Group
    key is a slug of that sentence so scenarios with matching text land
    in the same group. Legacy scenarios that predate the field fall back
    to the previous id-based derivation.
  */
  if (row?.useCase) {
    const slug = row.useCase
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return { id: slug, label: goalLabel(row.useCase) };
  }
  const id = row?.id || "";
  const title = row?.title || "";
  if (id.includes("-core-")) {
    const m = title.match(/(?:using|needing|landing on)\s+([\w_-]+)/i);
    if (m) return { id: `tool:${m[1].toLowerCase()}`, label: humanize(m[1]) };
    return { id: "tool:other", label: "Tool use" };
  }
  if (id.includes("-rule-")) {
    const short = title.split(/[.:]/)[0].slice(0, 42).trim();
    return {
      id: `rule:${short.toLowerCase()}`,
      label: short || "Rule enforcement",
    };
  }
  if (id.includes("-trap-")) {
    const table = title.split(":")[0].trim();
    if (table)
      return {
        id: `trap:${table.toLowerCase()}`,
        label: `${humanize(table)} data`,
      };
    return { id: "trap:other", label: "Data traps" };
  }
  if (id.includes("-adversarial-"))
    return { id: `adv:${title.toLowerCase()}`, label: title || "Adversarial" };
  if (id.includes("-edge-"))
    return { id: `edge:${title.toLowerCase()}`, label: title || "Edge case" };
  return { id: "other", label: "Other" };
};

/**
 * Group key + label for one row, in a given mode.
 *
 * The trace table on the run view uses the same three axes ("Goal",
 * "Persona", "Failure sub-goal"), so we mirror them here — same
 * derivation, so a scenarios-tab "Persona" bucket matches the trace
 * table's "Persona" bucket after a run.
 */
const groupKeyOf = (row, mode, env) => {
  /* A scenario in two groups is listed under its first — still one identity. */
  if (mode === "group") {
    const g = env?.__org?.groupNamesOf(row)?.[0];
    return g
      ? { id: `group:${g}`, label: g }
      : { id: "group:none", label: "Not in a group" };
  }
  if (mode === "tag") {
    const t = env?.__org?.tagNamesOf(row)?.[0];
    return t
      ? { id: `tag:${t}`, label: t }
      : { id: "tag:none", label: "No tags" };
  }
  if (mode === "persona") {
    const name = row?.persona?.name;
    if (!name) return { id: "persona:none", label: "No persona" };
    return { id: `persona:${name.toLowerCase()}`, label: name };
  }
  if (mode === "subgoal") {
    const subs = subTasksFor(row, env);
    const first = subs?.[0]?.label;
    if (!first) return { id: "subgoal:none", label: "No sub-goals" };
    const slug = first
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return { id: `subgoal:${slug}`, label: first };
  }
  if (mode === "source") {
    const s = ensureProvenance(row);
    const src = sourceOf(s.source);
    return { id: `source:${src.id}`, label: src.label };
  }
  if (mode === "addedBy") {
    const s = ensureProvenance(row);
    const who = s.addedBy?.name || "System";
    return { id: `addedBy:${who.toLowerCase()}`, label: `Added by ${who}` };
  }
  if (mode === "batch") {
    const s = ensureProvenance(row);
    const src = sourceOf(s.source);
    const when = new Date(s.addedAt).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
    return { id: `batch:${s.batchId}`, label: `${src.label} · ${when}` };
  }
  return deriveUseCase(row);
};

/* Batches are ALWAYS the outer grouping — this dropdown selects the
   INNER grouping shown within each batch. "Batch" itself is not an
   option here because everything is batched by default. */
const SCENARIO_GROUPINGS = [
  { id: "goal", label: "Goal", icon: "solar:target-linear" },
  { id: "persona", label: "Persona", icon: "solar:user-rounded-linear" },
  { id: "subgoal", label: "Sub-goal", icon: "solar:map-linear" },
  { id: "source", label: "Source", icon: "solar:magic-stick-3-linear" },
  { id: "addedBy", label: "Added by", icon: "solar:user-plus-linear" },
  { id: "group", label: "Group", icon: "solar:folder-2-linear" },
  { id: "tag", label: "Tag", icon: "solar:tag-linear" },
];

const groupScenarios = (rows, mode = "goal", env) => {
  /* Two-level grouping (per user directive 2026-09-22): batches are
     always the outer container carrying who added them + when. The
     inner grouping is the current `mode` (Goal / Persona / Sub-goal /
     Source / Added by). We flatten to a single sorted list of groups,
     each tagged with its `batchMeta`, and let the renderers insert a
     batch-header row on batch transitions — that avoids rewriting the
     whole table/list to a nested-structure API. */
  const batches = groupByBatch(rows);
  const out = [];
  batches.forEach((batch) => {
    const buckets = new Map();
    batch.scenarios.forEach((r) => {
      const key = groupKeyOf(r, mode, env);
      if (!buckets.has(key.id))
        buckets.set(key.id, {
          id: `${batch.batchId}__${key.id}`,
          label: key.label,
          rows: [],
          batchMeta: batch,
        });
      buckets.get(key.id).rows.push(r);
    });
    /* Within a batch, bigger inner groups first. */
    [...buckets.values()]
      .sort((a, b) => b.rows.length - a.rows.length)
      .forEach((g) => out.push(g));
  });
  return out;
};

/**
 * Just the grouped body — search, filter and view tabs live at the
 * page level now so the same toolbar drives both list and table
 * views. This renders each use-case section as a collapsible block
 * with a sticky header.
 */
function GroupedScenarioList({
  groups,
  env,
  envState,
  buildMode,
  onEdit,
  onRemove,
  onHideGroup,
  selectedIds = [],
  onSelectionChange,
  locked = false,
}) {
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const toggleRow = (id) => {
    if (!onSelectionChange) return;
    onSelectionChange(
      selectedSet.has(id)
        ? selectedIds.filter((v) => v !== id)
        : [...selectedIds, id],
    );
  };
  const toggleGroup = (groupRows, allSelected) => {
    if (!onSelectionChange) return;
    const ids = groupRows.map((r) => r.id);
    onSelectionChange(
      allSelected
        ? selectedIds.filter((id) => !ids.includes(id))
        : [...new Set([...selectedIds, ...ids])],
    );
  };
  return (
    <Box>
      {groups.map((g) => (
        <CollapsibleGroup
          key={g.id}
          group={g}
          env={env}
          envState={envState}
          buildMode={buildMode}
          onEdit={onEdit}
          onRemove={onRemove}
          onHideGroup={onHideGroup}
          selectedSet={selectedSet}
          onToggleRow={toggleRow}
          onToggleGroup={toggleGroup}
          selectable={!!onSelectionChange && !locked}
          locked={locked}
        />
      ))}
    </Box>
  );
}
GroupedScenarioList.propTypes = {
  locked: PropTypes.bool,
  groups: PropTypes.array,
  env: PropTypes.object,
  envState: PropTypes.object,
  buildMode: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onHideGroup: PropTypes.func,
  selectedIds: PropTypes.array,
  onSelectionChange: PropTypes.func,
};

/**
 * One collapsible group section. Header stays sticky when expanded so
 * scanning a big group keeps the current use case pinned at the top.
 * Chevron flips right → down on toggle. Header row is the whole click
 * target so there's no tiny hit area.
 */
function CollapsibleGroup({
  group,
  env,
  envState,
  buildMode,
  onEdit,
  onRemove,
  selectedSet,
  onToggleRow,
  onToggleGroup,
  selectable,
  locked = false,
}) {
  const [open, setOpen] = useState(true);
  const { stateOf, onStateAction } = useContext(ScenarioRowContext);

  const selectedInGroup = group.rows.filter((r) =>
    selectedSet?.has(r.id),
  ).length;
  const allSelected =
    selectable &&
    selectedInGroup === group.rows.length &&
    group.rows.length > 0;
  const someSelected = selectable && selectedInGroup > 0 && !allSelected;

  return (
    <Box>
      {/*
        Group header is a distinctive strip — neutral background, larger
        label typography, count as a pill — so the eye can tell at a
        glance "this is a use case group" versus "this is a scenario
        inside it". Before this both used the same s2/700/primary and
        the two levels blended into one long list.
      */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        {...clickable(() => setOpen((o) => !o), { expanded: open })}
        sx={{
          ...focusRing,
          position: "sticky",
          top: "var(--scn-head, 0px)",
          zIndex: 2,
          cursor: "pointer",
          px: 2.5,
          py: 1.75,
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
          borderTop: "1px solid",
          borderTopColor: "divider",
          /* The hover tint is laid over the solid background, not swapped in
             for it — this header pins over the rows, and a see-through
             background let them show through it while hovered. */
          "&:hover": {
            backgroundImage: (t) => {
              const tint = t.palette.action.selected;
              return `linear-gradient(${tint}, ${tint})`;
            },
          },
        }}
      >
        {selectable && (
          <Checkbox
            size="small"
            checked={allSelected}
            indeterminate={someSelected}
            onClick={(e) => e.stopPropagation()}
            onChange={() => onToggleGroup(group.rows, allSelected)}
            sx={{
              p: 0,
              flexShrink: 0,
              color: "text.disabled",
              "&.Mui-checked": { color: "text.primary" },
              "&.MuiCheckbox-indeterminate": { color: "text.primary" },
            }}
          />
        )}
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography
          sx={{
            typography: "s1",
            fontWeight: 600,
            color: "text.primary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {group.label}
        </Typography>
        <Typography
          sx={{
            px: 1,
            py: 0.25,
            borderRadius: "4px",
            typography: "s3",
            fontWeight: 600,
            color: "text.secondary",
            bgcolor: "action.selected",
            fontVariantNumeric: "tabular-nums",
            flexShrink: 0,
            letterSpacing: 0.2,
          }}
        >
          {group.rows.length}{" "}
          {group.rows.length === 1 ? "scenario" : "scenarios"}
        </Typography>
      </Stack>

      {open && (
        /*
          Scenario rows are indented and share a subtle left rail so
          they visibly nest inside the group above. The rail is the
          single strongest signal that "these all belong together
          under the header you just read".
        */
        <Box sx={{ pl: 3 }}>
          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
            sx={{
              borderLeft: "2px solid",
              borderColor: "divider",
            }}
          >
            {group.rows.map((s) => {
              const checked = !!selectedSet?.has(s.id);
              return (
                <Stack key={s.id} direction="row" alignItems="flex-start">
                  {/* Centred on the scenario's name line (not the top of the row),
                      and sitting under the group's chevron — one level in. */}
                  {selectable && (
                    <Box sx={{ pt: "5px", pl: 3, flexShrink: 0 }}>
                      <Checkbox
                        size="small"
                        checked={checked}
                        onChange={() => onToggleRow(s.id)}
                        sx={{
                          p: 0,
                          color: "text.disabled",
                          "&.Mui-checked": { color: "text.primary" },
                        }}
                      />
                    </Box>
                  )}
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <ScenarioDetail
                      row={s}
                      env={env}
                      envState={envState}
                      buildMode={buildMode}
                      state={stateOf ? stateOf(s) : null}
                      onAction={onStateAction}
                      locked={locked}
                    />
                  </Box>
                  <Tooltip
                    arrow
                    title={
                      locked
                        ? "Fork this environment to edit."
                        : "Edit scenario"
                    }
                  >
                    <span>
                      <IconButton
                        size="small"
                        disabled={locked}
                        onClick={() => onEdit(s)}
                        sx={{ mt: 1, flexShrink: 0 }}
                      >
                        <Iconify
                          icon="solar:pen-new-square-linear"
                          width={16}
                          sx={{ color: "text.tertiary" }}
                        />
                      </IconButton>
                    </span>
                  </Tooltip>
                  {/* Archive keeps it listed and stops it running; Restore undoes. */}
                  <Box sx={{ mt: 1, flexShrink: 0 }}>
                    {archivedOf(s) ? (
                      <Tooltip
                        arrow
                        title={
                          locked
                            ? "Read-only."
                            : "Restore — run and count it again"
                        }
                      >
                        <span>
                          <IconButton
                            size="small"
                            disabled={locked}
                            onClick={() => onStateAction?.("restore", s)}
                          >
                            <Iconify
                              icon="solar:restart-linear"
                              width={16}
                              sx={{ color: "text.tertiary" }}
                            />
                          </IconButton>
                        </span>
                      </Tooltip>
                    ) : (
                      <ArchiveControl
                        disabled={locked}
                        onArchive={(note) =>
                          onStateAction?.("archive", s, note)
                        }
                      />
                    )}
                  </Box>
                  <Tooltip
                    arrow
                    title={
                      locked
                        ? "Fork this environment to edit."
                        : "Delete scenario"
                    }
                  >
                    <span>
                      <IconButton
                        size="small"
                        disabled={locked}
                        onClick={() => onRemove(s.id)}
                        sx={{ mt: 1, mr: 1.5, flexShrink: 0 }}
                      >
                        <Iconify
                          icon="solar:trash-bin-trash-linear"
                          width={16}
                          sx={{ color: "text.tertiary" }}
                        />
                      </IconButton>
                    </span>
                  </Tooltip>
                </Stack>
              );
            })}
          </Stack>
        </Box>
      )}
    </Box>
  );
}
CollapsibleGroup.propTypes = {
  locked: PropTypes.bool,
  group: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  buildMode: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onHideGroup: PropTypes.func,
  selectedSet: PropTypes.object,
  onToggleRow: PropTypes.func,
  onToggleGroup: PropTypes.func,
  selectable: PropTypes.bool,
};

/* ── filter popover ──────────────────────────────────────────────────────── */

/**
 * Use-case filter popover.
 *
 * The old MUI Menu clipped long use-case sentences off the right edge
 * and read as one dense list. This is a small custom popover instead:
 * fixed width, wrapping labels, an inline search for long lists, and
 * a clear header + footer treatment so the frame reads as a real
 * filter panel rather than a menu.
 */
/**
 * Multi-dimension scenario filter menu — same MUI Menu + section-heading +
 * MenuItem shape the Improvements list uses. Sections: Status, Persona,
 * Kind, Sub-goal. Multi-select: a check mark on the right of each row
 * indicates selection; clicking a row toggles it without closing the menu.
 */
function ScenarioFilterMenu({
  anchorEl,
  onClose,
  filters,
  onToggle,
  onClearAll,
  scenarios,
  statusOf,
  personaOf,
  kindOf,
  subgoalOf,
}) {
  const STATUS_OPTIONS = [
    { id: "broken", label: "Broken" },
    { id: "critical", label: "Critical" },
    { id: "edited", label: "Edited" },
  ];
  const KIND_OPTIONS = [
    { id: "happy", label: "Happy path" },
    { id: "rule", label: "Rule enforcement" },
    { id: "trap", label: "Data trap" },
    { id: "adversarial", label: "Adversarial" },
    { id: "edge", label: "Edge case" },
  ];

  const counts = (getter, options) => {
    const c = {};
    scenarios.forEach((r) => {
      const val = getter(r);
      const list = Array.isArray(val) ? val : [val];
      list.forEach((v) => {
        if (v == null) return;
        c[v] = (c[v] || 0) + 1;
      });
    });
    return options.map((o) => ({ ...o, count: c[o.id] || 0 }));
  };
  const dynamicOptions = (getter) => {
    const c = new Map();
    scenarios.forEach((r) => {
      const v = getter(r);
      if (!v) return;
      c.set(v, (c.get(v) || 0) + 1);
    });
    return [...c.entries()]
      .map(([id, count]) => ({ id, label: id, count }))
      .sort((a, b) => b.count - a.count);
  };

  const totalSelected =
    filters.status.length +
    filters.persona.length +
    filters.kind.length +
    filters.subgoal.length;

  const renderSection = (title, dim, options, isFirst) => {
    if (!options.length) return null;
    return (
      <Box key={title}>
        <Stack
          direction="row"
          alignItems="center"
          sx={{ px: 1, pt: isFirst ? 0 : 1, pb: 0.5 }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontWeight: 600,
              textTransform: "none",
              letterSpacing: 0.4,
              flex: 1,
            }}
          >
            {title}
          </Typography>
        </Stack>
        {options.map((o) => {
          const isSelected = filters[dim].includes(o.id);
          return (
            <MenuItem
              key={o.id}
              selected={isSelected}
              onClick={() => onToggle(dim, o.id)}
              sx={{ typography: "s2", pr: 1.25 }}
            >
              <Box
                sx={{
                  flex: 1,
                  minWidth: 0,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {o.label}
              </Box>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                  mr: isSelected ? 0.75 : 2.25,
                }}
              >
                {o.count}
              </Typography>
              {isSelected && (
                <ListItemIcon
                  sx={{ minWidth: "auto !important", color: "primary.main" }}
                >
                  <Iconify icon="solar:check-linear" width={16} />
                </ListItemIcon>
              )}
            </MenuItem>
          );
        })}
      </Box>
    );
  };

  return (
    <Menu
      anchorEl={anchorEl}
      open={!!anchorEl}
      onClose={onClose}
      slotProps={{ paper: { sx: { minWidth: 320, maxHeight: 520, p: 1.5 } } }}
    >
      {totalSelected > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          sx={{
            px: 1,
            pb: 0.75,
            mb: 0.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
          >
            {totalSelected} selected
          </Typography>
          <Button
            size="small"
            onClick={onClearAll}
            sx={{ color: "text.secondary" }}
          >
            Clear all
          </Button>
        </Stack>
      )}
      {renderSection(
        "Status",
        "status",
        counts(statusOf, STATUS_OPTIONS),
        true,
      )}
      {renderSection("Persona", "persona", dynamicOptions(personaOf))}
      {renderSection("Kind", "kind", counts(kindOf, KIND_OPTIONS))}
      {renderSection("Sub-goal", "subgoal", dynamicOptions(subgoalOf))}
    </Menu>
  );
}
ScenarioFilterMenu.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  filters: PropTypes.object,
  onToggle: PropTypes.func,
  onClearAll: PropTypes.func,
  scenarios: PropTypes.array,
  statusOf: PropTypes.func,
  personaOf: PropTypes.func,
  kindOf: PropTypes.func,
  subgoalOf: PropTypes.func,
};

function UseCaseFilterPopover({
  anchorEl,
  onClose,
  allUseCases,
  countBy,
  selected,
  onChange,
  dimensionLabel = "use case",
}) {
  const [q, setQ] = useState("");

  /* Reset the internal search when the popover closes so it opens
     fresh next time. */
  const handleClose = () => {
    setQ("");
    onClose();
  };

  const filtered = q.trim()
    ? allUseCases.filter((uc) =>
        uc.label.toLowerCase().includes(q.trim().toLowerCase()),
      )
    : allUseCases;

  const toggle = (id) =>
    onChange(
      selected.includes(id)
        ? selected.filter((v) => v !== id)
        : [...selected, id],
    );

  return (
    <Popover
      open={!!anchorEl}
      anchorEl={anchorEl}
      onClose={handleClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      transformOrigin={{ vertical: "top", horizontal: "left" }}
      slotProps={{
        paper: {
          sx: {
            width: 420,
            mt: 0.75,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            overflow: "hidden",
          },
        },
      }}
    >
      {/* header */}
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="solar:filter-linear"
          width={14}
          sx={{ color: "text.secondary", mr: 0.75 }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
          {`Filter by ${dimensionLabel.toLowerCase()}`}
        </Typography>
        {selected.length > 0 && (
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mr: 0.75 }}
          >
            {selected.length} selected
          </Typography>
        )}
      </Stack>

      {/* inline search — proper bordered input so it reads as a real
          field. The old borderless neutral pill looked like a
          placeholder that never rendered. Height and padding match
          the other TextFields in this flow. */}
      {allUseCases.length > 6 && (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              px: 1.25,
              height: 34,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
              transition: "border-color .12s ease",
              "&:focus-within": {
                borderColor: "primary.main",
              },
            }}
          >
            <Iconify
              icon="solar:magnifer-linear"
              width={14}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
            <InputBase
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={`Search ${dimensionLabel.toLowerCase()}s…`}
              autoFocus
              sx={{ typography: "s2", flex: 1, color: "text.primary" }}
            />
            {q && (
              <IconButton size="small" onClick={() => setQ("")}>
                <Iconify
                  icon="solar:close-circle-linear"
                  width={14}
                  sx={{ color: "text.tertiary" }}
                />
              </IconButton>
            )}
          </Box>
        </Box>
      )}

      {/* list */}
      <Box sx={{ maxHeight: 340, overflowY: "auto", py: 0.5 }}>
        {filtered.length === 0 ? (
          <Box sx={{ px: 2, py: 4, textAlign: "center" }}>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              No use cases match &ldquo;{q}&rdquo;.
            </Typography>
          </Box>
        ) : (
          filtered.map((uc) => {
            const on = selected.includes(uc.id);
            const count = countBy(uc.id);
            return (
              <Stack
                key={uc.id}
                direction="row"
                alignItems="flex-start"
                spacing={1.25}
                {...clickable(() => toggle(uc.id), {
                  role: "checkbox",
                  checked: on,
                })}
                sx={{
                  ...focusRing,
                  px: 2,
                  py: 1,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                {/* The row is the checkbox; this one only draws it. */}
                <Checkbox
                  size="small"
                  checked={on}
                  disableRipple
                  tabIndex={-1}
                  inputProps={{ "aria-hidden": true }}
                  sx={{
                    p: 0,
                    mt: "1px",
                    flexShrink: 0,
                    color: "text.disabled",
                    "&.Mui-checked": { color: "text.primary" },
                    "&.MuiCheckbox-indeterminate": { color: "text.primary" },
                  }}
                />
                <Typography
                  sx={{
                    typography: "s2",
                    color: "text.primary",
                    flex: 1,
                    minWidth: 0,
                    /* wrap long use-case sentences instead of clipping
                       them off the right edge */
                    whiteSpace: "normal",
                    lineHeight: 1.4,
                  }}
                >
                  {uc.label}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                    mt: "1px",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {count}
                </Typography>
              </Stack>
            );
          })
        )}
      </Box>

      {/* footer */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ px: 2, py: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button
          size="small"
          onClick={() => onChange([])}
          disabled={selected.length === 0}
          sx={{
            color: selected.length ? "text.secondary" : "text.disabled",
            minWidth: 0,
            "&:hover": { bgcolor: "transparent", color: "text.primary" },
          }}
        >
          Clear all
        </Button>
        <Button
          size="small"
          variant="contained"
          onClick={handleClose}
          sx={{
            /* White in both themes — matches the neutral chrome of the
               popover and reads as the primary action without pulling
               in the purple brand tone. */
            bgcolor: "common.white",
            color: "grey.900",
            border: "1px solid",
            borderColor: (t) => alpha(t.palette.common.black, 0.08),
            "&:hover": {
              bgcolor: "common.white",
              boxShadow: "none",
              borderColor: (t) => alpha(t.palette.common.black, 0.2),
            },
          }}
        >
          Done
        </Button>
      </Stack>
    </Popover>
  );
}
UseCaseFilterPopover.propTypes = {
  dimensionLabel: PropTypes.string,
  anchorEl: PropTypes.any,
  onClose: PropTypes.func,
  allUseCases: PropTypes.array,
  countBy: PropTypes.func,
  selected: PropTypes.array,
  onChange: PropTypes.func,
};
