import { useMemo, useState } from "react";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { alpha, useTheme } from "@mui/material/styles";
import ReactApexChart from "../components/SafeApexChart";
import { useSnackbar } from "notistack";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  TextField,
  MenuItem,
  Select,
  Tooltip,
  Menu,
  Checkbox,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { getEnvironment } from "../_mock/environments";
import { neutralCheckboxSx } from "../components/primitives";
import { protoRunId } from "../_mock/executionAdapter";
import { runIdentityOf } from "../_mock/runIdentity";
import useRunFitCheck from "./useRunFitCheck";
import {
  buildComparison,
  distributionFor,
  changedCount,
  runSummaries,
  runSummary,
  trialSummaries,
  chipIdentity,
} from "../_mock/comparison";
import {
  emptyFilters,
  filterRows,
  sortRows,
  applyQuick,
  groupRows,
  defaultView,
  bandRows,
  ROW_HEIGHTS,
  viewSnapshot,
  snapshotsEqual,
  newViewId,
} from "../_mock/compareView";
import {
  withPairs,
  bridgeOf,
  priceOf,
  priceLine,
  verdictWithMustPass,
} from "../_mock/pairs";
import { useEnvState, useSimStore } from "../store";
import { mustPassRegressionsOf, promotePatch } from "../_mock/promote";
import { useRoleGates } from "../_mock/previewRole";
import PromoteDialog from "./PromoteDialog";
import { runCreditsOf } from "../_mock/usageLedger";
import { SectionCard, EmptyState } from "../components/primitives";
import CallCompare from "./CallCompare";
import CompareActions, {
  CompareSearchBar,
  SavedViews,
} from "./CompareControls";
import CompareGrid from "./CompareGrid";
import CompareSummary from "./CompareSummary";
import CompareVerdict from "./CompareVerdict";
import CompareRunColumns from "./CompareRunColumns";
import CompareLetter from "./CompareLetter";
import { comparedWithText, rowLabelOf, runLabelOf } from "../_mock/scoreDelta";
import RuleChangeDialog from "../components/RuleChangeDialog";
import { agentSourceOf } from "../_mock/agentRegistry";
import { agentVersions, behaviourLabelOf } from "../_mock/versions";
import { versionOriginOf } from "../_mock/specChange";
import { subGoalGroups, heldFixedAcross } from "../_mock/compareHeld";
import { snapshotOfRun, compareEnvHeadline } from "../_mock/envSnapshot";
import { runStatsOf, alignedStatsOf } from "../_mock/runCompare";

/* "62% 67/108" — scenario pass and its fraction, from runStatsOf like the Runs
   table's Scenario pass cell. An improvement's run is read from its own
   per-scenario outcomes (trialTasks), even once Compare has hydrated it with
   the source run's calls, so the menu, the pill and the table agree. */
const scenarioPassText = (r, ctx) => {
  /* Read the way the run's own page reads it (alignedStatsOf). */
  const st = alignedStatsOf(
    r?.kind === "trial" && r.trialTasks?.length
      ? { ...r, tasks: r.trialTasks }
      : r,
    ctx,
  );
  return st.measured
    ? `${st.scenarioRate}% ${st.scenarioPassed}/${st.measured}`
    : `${r?.passRate ?? "—"}%`;
};

/**
 * Two or more runs of the same scenarios.
 *
 * This is the run detail screen with a second question: not "what happened"
 * but "what changed". Every row is one scenario, and every run gets its own
 * line in it — its verdict, and the single thing that decided it.
 *
 * A whole transcript per run per row would be the honest rendering and an
 * unreadable one, so the row carries the deciding moment and the transcripts
 * are one click away. Turning on Show diff swaps that line for what this run
 * did *differently* — which tools it stopped calling, where it first diverged —
 * because for two versions of one agent the change in behaviour is the finding,
 * and it is the thing a transcript hides.
 *
 * The first run in the URL is the baseline. Everything else is read against it,
 * pair by pair (PRD v7 §4.10.7): each scenario is exact, re-graded, rechecked,
 * changed, new, not applicable, archived or removed, and the verdict is read
 * over the comparable pairs with its interval. It is never blocked — a
 * mismatch between the runs is a warning with a priced offer.
 *
 * A run from a forked environment rides in the URL as `<envId>::<runId>` and
 * compares on carried scenario ids, labelled cross-environment.
 */
export default function CompareRuns() {
  const { envId } = useParams();
  const navigate = useNavigate();
  /* Opened from an improvement's runs, Back goes there; else the Runs tab. */
  const from = useLocation().state?.back || null;
  const theme = useTheme();
  const { enqueueSnackbar } = useSnackbar();
  const [params, setParams] = useSearchParams();
  const { state } = useSimStore();
  const { envState, patch } = useEnvState(envId);

  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState(emptyFilters);
  const [selected] = useState([]);
  const [openRow, setOpenRow] = useState(null);
  const [addAnchor, setAddAnchor] = useState(null);
  const [pickedEval, setPickedEval] = useState(null);
  /* The rule question, open in its dialog. Unanswered, it's a warning —
     the comparison is never held back for it. */
  const [answering, setAnswering] = useState(null);
  /* Rechecked pairs count in the delta unless this session leaves them out. */
  const [excludeRechecked, setExcludeRechecked] = useState(false);
  /*
    The view is derived from the saved default unless this session has changed
    it. Seeding state from `envState` instead would read the store before it
    hydrates and lock the screen to the built-in default.
  */
  const [viewOverride, setViewOverride] = useState(null);
  const [promoting, setPromoting] = useState(false);
  const gates = useRoleGates();
  const view = viewOverride || {
    ...defaultView(),
    ...(envState.compareView || {}),
  };
  /* Staging selection for the Add-run picker — the menu supports
     multi-select via checkboxes and commits everything in one go. */
  const [addStaging, setAddStaging] = useState([]);
  const openAddMenu = (el) => {
    setAddStaging([]);
    setAddAnchor(el);
  };
  const closeAddMenu = () => {
    setAddStaging([]);
    setAddAnchor(null);
  };
  const toggleAddStaging = (id) =>
    setAddStaging((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  const commitAdd = () => {
    if (addStaging.length) setRuns([...runIds, ...addStaging]);
    closeAddMenu();
  };

  /*
    Saved views live on the environment and the active one lives in the URL, so
    a link carries the reading as well as the runs — the same contract Observe
    has with `?tab=view-…`.
  */
  const savedViews = envState.compareViews || [];
  const activeViewId = params.get("view") || null;
  const activeView = savedViews.find((v) => v.id === activeViewId) || null;
  const snapshot = viewSnapshot(filters, view);
  const dirty = activeView
    ? !snapshotsEqual(snapshot, activeView.config)
    : !snapshotsEqual(
        snapshot,
        viewSnapshot(emptyFilters(), {
          ...defaultView(),
          ...(envState.compareView || {}),
        }),
      );

  const setActiveView = (id) => {
    const next = new URLSearchParams(params);
    if (id) next.set("view", id);
    else next.delete("view");
    setParams(next, { replace: true });
  };

  const applyView = (id) => {
    const v = savedViews.find((x) => x.id === id);
    setFilters(v ? [...(v.config.filters || [])] : emptyFilters());
    setViewOverride(
      v
        ? { ...v.config.view }
        : { ...defaultView(), ...(envState.compareView || {}) },
    );
    setActiveView(id);
  };

  const saveView = (name) => {
    const id = newViewId(name, savedViews);
    patch({ compareViews: [...savedViews, { id, name, config: snapshot }] });
    setActiveView(id);
    enqueueSnackbar(`View “${name}” saved`, { variant: "success" });
  };

  const updateView = (id) => {
    patch({
      compareViews: savedViews.map((v) =>
        v.id === id ? { ...v, config: snapshot } : v,
      ),
    });
    enqueueSnackbar("View updated", { variant: "success" });
  };

  const renameView = (id, name) =>
    patch({
      compareViews: savedViews.map((v) => (v.id === id ? { ...v, name } : v)),
    });

  const deleteView = (id) => {
    patch({ compareViews: savedViews.filter((v) => v.id !== id) });
    if (id === activeViewId) setActiveView(null);
  };

  const env =
    getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);
  /* Each run named the way the runs list names it — "agent v3 × env v7". */
  const identityOf = (r) => {
    if (!r?._env) return runIdentityOf(r, envState, env);
    /* A run from another environment is named by where it ran. */
    const id = runIdentityOf(r, r._envState, r._env);
    return {
      ...id,
      env: `${id.env || ""} · ${r.envName}`.replace(/^ · /, ""),
      envName: `${id.envName || ""} · ${r.envName}`.replace(/^ · /, ""),
      label: `${id.label} · ${r.envName}`,
    };
  };
  const { check: checkRunFit, dialog: runFitDialog } = useRunFitCheck(
    env,
    envState,
    patch,
  );
  const runIds = useMemo(
    () => (params.get("runs") || "").split(",").filter(Boolean),
    [params],
  );
  /* Environments this one is related to by a fork — their runs compare on
     carried ids (cross-environment); any other environment only side by side. */
  const envById = (id) =>
    getEnvironment(id) || state.myEnvironments.find((e) => e.id === id);
  const parentOf = (e) =>
    (typeof e?.forkedFrom === "string" ? e.forkedFrom : e?.forkedFrom?.envId) ||
    null;
  const foreign = useMemo(() => {
    const map = new Map();
    runIds
      .filter((id) => id.includes("::"))
      .forEach((key) => {
        const [otherId, runId] = key.split("::");
        const otherEnv = envById(otherId);
        const otherState = state.byEnv?.[otherId];
        const raw = (otherState?.runs || []).find((r) => r.id === runId);
        if (!otherEnv || !raw) return;
        const order = [...(otherState.runs || [])].sort(
          (a, b) =>
            Date.parse(a.finishedAt || 0) - Date.parse(b.finishedAt || 0),
        );
        const sum = runSummary(
          otherEnv,
          otherState,
          raw,
          Math.max(
            0,
            order.findIndex((r) => r.id === runId),
          ),
        );
        const related =
          parentOf(otherEnv) === envId || parentOf(env) === otherId;
        map.set(key, {
          ...sum,
          id: key,
          _env: otherEnv,
          _envState: otherState,
          envName: otherEnv.name,
          letter: `${related ? "F" : "X"}${sum.ordinal}`,
          // eslint-disable-next-line no-restricted-syntax -- ds: data colour
          color: "#525252",
        });
      });
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runIds, state.byEnv, state.myEnvironments, envId]);
  const [numbersFor, setNumbersFor] = useState(null);
  const [chartOpen, setChartOpen] = useState(false);
  const comparisonRaw = useMemo(
    () =>
      env
        ? buildComparison(env, envState, runIds, { foreign })
        : { runs: [], rows: [], evals: [] },
    [env, envState, runIds, foreign],
  );
  /* Every row classed against the baseline, and the verdict over the pairs. */
  const { comparison, read } = useMemo(
    () =>
      env && comparisonRaw.runs.length >= 2
        ? withPairs(comparisonRaw, env, envState, {
            excludeRechecked,
            regrades: envState.compareRegrades || [],
            targetId: numbersFor,
          })
        : { comparison: comparisonRaw, read: null },
    [comparisonRaw, env, envState, excludeRechecked, numbersFor],
  );
  /* What each run pinned (this environment's runs only), and the two
     environment versions the verdict sits on. */
  const held = useMemo(() => {
    const own = comparisonRaw.runs.filter((r) => !r._env);
    return env && own.length >= 2 && own.length === comparisonRaw.runs.length
      ? heldFixedAcross(own, env, envState)
      : null;
  }, [comparisonRaw.runs, env, envState]);
  const envHeadline = useMemo(() => {
    const runs = comparisonRaw.runs;
    if (!env || !read || runs.length < 2 || runs.some((r) => r._env))
      return null;
    const a = snapshotOfRun(runs[0], env, envState);
    const b = snapshotOfRun(
      runs.find((r) => r.id === read.targetId) || runs[1],
      env,
      envState,
    );
    return compareEnvHeadline(a, b, read.counts, read.total);
  }, [comparisonRaw.runs, env, envState, read]);
  /* Every runnable row this environment has — manual runs plus every
     improvement-run trial — with the unified numeric-chip identity
     applied so the Add-run picker matches the runs list badges. */
  const others = useMemo(() => {
    if (!env) return [];
    const chips = chipIdentity(env, envState);
    const manual = runSummaries(env, envState);
    const trials = trialSummaries(env, envState);
    /* What would differ from the baseline's setup (world, tools, caller,
       evals…) — the agent is what's under test, so it doesn't count. An
       epoch's run reads its source run's setup. */
    const base = comparisonRaw.runs[0];
    const setupDiffOf = (r) => {
      if (!base) return [];
      const opt =
        r.kind === "trial"
          ? (envState.optimizations || []).find(
              (o) => o.id === r.selfImprovementId,
            )
          : null;
      const full =
        (envState.runs || []).find((x) => x.id === r.id) ||
        (opt && (envState.runs || []).find((x) => x.id === opt.fromRunId)) ||
        r;
      const h = heldFixedAcross([base, full], env, envState);
      return (h?.differs || [])
        .filter((d) => d.id !== "agent" && d.id !== "trials")
        .map((d) => d.label);
    };
    return [...manual, ...trials]
      .filter((r) => !runIds.includes(r.id))
      .map((r) => {
        const chip = chips.get(r.id);
        const withChip = chip
          ? { ...r, letter: chip.letter, color: chip.color }
          : r;
        return { ...withChip, setupDiff: setupDiffOf(r) };
      })
      /* Same setup first: those read fairly against the baseline. */
      .sort(
        (a, b) =>
          (a.setupDiff.length > 0) - (b.setupDiff.length > 0) ||
          new Date(b.finishedAt) - new Date(a.finishedAt),
      );
  }, [env, envState, runIds, comparisonRaw.runs]);

  /* Runs in other environments: a fork's (or its parent's) compare on
     carried ids; an unrelated one's latest run only side by side. */
  const crossOthers = useMemo(() => {
    if (!env) return [];
    const out = [];
    (state.myEnvironments || [])
      .filter((e) => e.id !== envId)
      .forEach((e) => {
        const related =
          parentOf(e) === envId ||
          parentOf(env) === e.id ||
          (!!parentOf(e) && parentOf(e) === parentOf(env));
        const done = (state.byEnv?.[e.id]?.runs || [])
          .filter((r) => r.finishedAt && !r.pending && r.status !== "running")
          .sort((a, b) => Date.parse(b.finishedAt) - Date.parse(a.finishedAt))
          .slice(0, related ? 5 : 1);
        done.forEach((r) => {
          const key = `${e.id}::${r.id}`;
          /* Named the way its own environment names it — "agent v3 × environment v5". */
          if (!runIds.includes(key))
            out.push({
              key,
              related,
              envName: e.name,
              run: r,
              label: runIdentityOf(r, state.byEnv?.[e.id], e).label,
            });
        });
      });
    return out.sort((a, b) =>
      a.related === b.related ? 0 : a.related ? -1 : 1,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [env, envId, state.myEnvironments, state.byEnv, runIds]);

  const evalId = comparison.evals.some((e) => e.id === pickedEval)
    ? pickedEval
    : comparison.evals[0]?.id || null;

  /* Search, then the panel's fields, then the quick filters, then order. */
  const rows = useMemo(() => {
    const found = filterRows(comparison.rows, {
      query,
      filters,
      evals: comparison.evals,
    });
    return sortRows(applyQuick(found, view.quick), view.sort);
  }, [
    comparison.rows,
    comparison.evals,
    query,
    filters,
    view.quick,
    view.sort,
  ]);
  /* Bands by pair class: exact regressions on top (regressions-first sort),
     the comparable rows grouped as chosen, every class outside the delta in
     its own band below. Sub-goal groups the way the single-run table can't. */
  const groups = useMemo(
    () =>
      bandRows(rows, {
        group: view.group,
        /* The table's run headers count and filter regressions per run, so
           it doesn't also pull one pair's regressions into a band on top. */
        regressionsFirst: view.sort === "movement" && view.view !== "table",
        groupFn: (rs) =>
          view.group === "subGoal" && env
            ? subGoalGroups(rs, comparison.runs, env, envState)
            : groupRows(rs, view.group),
      }),
    [rows, view.group, view.sort, view.view, comparison.runs, env, envState],
  );

  const back = () =>
    from?.to
      ? navigate(from.to, { state: from.state })
      : navigate(paths.dashboard.simulate.environmentStep(envId, "runs"));
  const setRuns = (ids) =>
    setParams({ runs: ids.join(",") }, { replace: true });

  const exportComparison = () => {
    const payload = {
      environment: env?.name,
      baseline: comparison.baseline?.id,
      verdict: read
        ? {
            comparedOn: read.sentence,
            delta: read.interval.delta,
            interval: [read.interval.lo, read.interval.hi],
            label: read.label.label,
          }
        : null,
      runs: comparison.runs.map((r) => ({
        id: r.id,
        agentVersion: r.agentVersion,
        passRate: r.passRate,
        tokens: r.tokens,
        /* What the run was charged, as its Cost tab — kept in the export — and
           the agent's own model, on the customer's key, in dollars. */
        credits: runCreditsOf(env, envState)[r.id] ?? null,
        modelUsd: Number((r.cost || 0).toFixed(4)),
        costUnverified: r.costUnverified || 0,
      })),
      scenarios: rows.map((row) => ({
        scenario: row.title,
        critical: !!row.critical,
        pair: row.pair?.cls || null,
        movement:
          row.pair?.flip?.text || (row.changed ? "changed" : "unchanged"),
        runs: row.cells.map((c) => ({
          run: c.runId,
          verdict: c.status,
          deciding: c.deciding.text,
          durationMs: c.durationMs,
          tokens: c.tokens,
        })),
      })),
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `${env?.id || "comparison"}-compare.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!env || comparison.runs.length < 2) {
    /* AC-14.7: say what is missing — never render a version that never ran. */
    const finishedRuns = (envState.runs || []).filter(
      (r) => r.finishedAt && !r.pending,
    ).length;
    return (
      <Box sx={{ p: 3 }}>
        <EmptyState
          icon="solar:transfer-horizontal-linear"
          title={finishedRuns < 2 ? "Not yet run" : "Nothing to compare"}
          body={
            finishedRuns < 2
              ? `Comparing needs two finished runs; this environment has ${finishedRuns}. Run it, then compare.`
              : "Pick two or more runs from the summary and press Compare."
          }
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={back}
            >
              Back to runs
            </Button>
          }
        />
      </Box>
    );
  }

  const changed = changedCount(comparison);
  /* Every run on the same environment and scenario version: said once in the
     subtitle, not on every chip. */
  const sharedEnv =
    new Set(comparison.runs.map((r) => identityOf(r).envName)).size === 1;

  /* n is part of the measurement. Runs sampled a different number of times are
     not reporting the same quantity, and the header says so rather than
     letting two differently-sampled rates sit side by side as equals. */
  const sampleNote =
    comparison.coverage.repeats.length === 1
      ? ` · ${comparison.coverage.repeats[0]} trial${comparison.coverage.repeats[0] === 1 ? "" : "s"} each`
      : ` · trials per scenario differ (${comparison.coverage.repeats.join(", ")})`;

  /*
    Re-running is the loop closing. It starts a real run of exactly the rows
    someone ticked, against whatever the agent is now — which is the reason
    they were reading a comparison in the first place.
  */
  /*
    Optimize reads failures against the run they happened in, so the handover
    names a run: the latest one compared, with the ticked scenarios in tow.
  */
  const optimizeSelected = () => {
    const target = comparison.runs[comparison.runs.length - 1];
    navigate(
      `${paths.dashboard.simulate.simulationRun(env.id, target.id)}?tab=omega&only=${selected.join(",")}`,
    );
  };

  /*
    A bridge, backfill or top-up is a new run, started only on a click
    (AC-16.11): the older agent version on the scenarios that need it, at the
    trial count the comparison reads.
  */
  const startFix = (fix) => {
    const q = new URLSearchParams();
    if (fix.agent) q.set("agent", fix.agent);
    if (fix.scenarioIds?.length) q.set("only", fix.scenarioIds.join(","));
    q.set("trials", String(fix.repeats || 3));
    navigate(
      `${paths.dashboard.simulate.simulationRun(env.id, protoRunId(env.id, Date.now().toString(36)))}?${q.toString()}`,
    );
  };
  /* The offer on a band outside the delta: bridge the changed, backfill the new. */
  const bandAction = (group) => {
    /* Across environment versions there is no delta to bring them into. */
    if (
      !read ||
      !["changed", "new"].includes(group.cls) ||
      read.relation !== "same" ||
      read.label.id === "side"
    )
      return null;
    const agent = read.keys.a.agent;
    const bridge = bridgeOf(env, envState, agent);
    if (!bridge.can)
      return (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {bridge.why}
        </Typography>
      );
    const ids = group.rows.map((r) => r.id);
    const k = read.keys.b.repeats;
    return (
      <Tooltip
        arrow
        title={`Run ${agent} on these ${ids.length} — it ${bridge.how}. They then pair as exact.`}
      >
        <Button
          size="small"
          variant="outlined"
          onClick={(e) => {
            e.stopPropagation();
            startFix({ agent, scenarioIds: ids, repeats: k });
          }}
          startIcon={<Iconify icon="solar:play-linear" width={14} />}
          sx={{
            flexShrink: 0,
            color: "text.primary",
            borderColor: "divider",
            "&:hover": {
              borderColor: "text.disabled",
              bgcolor: "action.hover",
            },
          }}
        >
          {`Run ${agent} on these · ${priceLine(priceOf(env, envState, ids.length, k))}`}
        </Button>
      </Tooltip>
    );
  };

  const rerunSelected = () =>
    checkRunFit(() => {
      const only = selected.join(",");
      const trials = comparison.runs[comparison.runs.length - 1]?.repeats || 3;
      navigate(
        `${paths.dashboard.simulate.simulationRun(env.id, protoRunId(env.id, Date.now().toString(36)))}?only=${only}&trials=${trials}`,
      );
    });
  const bands = evalId ? distributionFor(comparison, evalId) : [];
  const peak = Math.max(1, ...bands.flatMap((b) => b.counts));
  const rowPad = ROW_HEIGHTS.find((h) => h.id === view.rowHeight)?.py ?? 2;

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      {runFitDialog}
      {/* ── header ──
          Two rows on purpose. Everything was on one line — title, subtitle,
          five chips, four controls and two icons — and a row that dense reads
          as a toolbar someone kept appending to. The top line says what you
          are looking at; the second says what you can do to it. */}
      <Box
        sx={{
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 2, pt: 1.5, pb: 1 }}
        >
          {/* Back is a labelled button rather than a bare arrow — the previous
              icon-only affordance was easy to miss, and this screen is a
              detour off the Runs list. */}
          <Button
            variant="outlined"
            size="small"
            onClick={back}
            startIcon={
              <Iconify icon="solar:alt-arrow-left-linear" width={16} />
            }
            sx={{ flexShrink: 0, minWidth: 0 }}
          >
            {from?.label ? `Back to ${from.label}` : "Back to runs"}
          </Button>
          <Box
            sx={{ height: 20, width: "1px", bgcolor: "divider", flexShrink: 0 }}
          />
          <Box minWidth={0}>
            <Typography noWrap sx={{ typography: "m2", fontWeight: 600 }}>
              {env.name}
            </Typography>
            {/* The claim this screen rests on, stated only as far as it is
                true. "Identical in every run" was printed unconditionally, and
                the moment someone compared a full sweep against a re-run of
                four blockers it was a lie in the subtitle of the screen that
                decides releases. */}
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {(() => {
                /* An improvement's run is its source version plus a change —
                   a different agent config from the version itself. */
                const changed = comparison.runs.some((r) => r.kind === "trial");
                const labels = [
                  ...new Set(
                    comparison.runs.map((r) =>
                      r.kind === "trial"
                        ? `${r.agentVersion} + change`
                        : r.agentVersion,
                    ),
                  ),
                ];
                const n = labels.length;
                /* A credentials-only version behaves as its base — said, so
                   v5 against v5.1 doesn't read as two agents. */
                const known = agentVersions(envState);
                const same =
                  n > 1 &&
                  !changed &&
                  new Set(labels.map((l) => behaviourLabelOf(known, l)))
                    .size === 1;
                /* What every run shares is said once here, so the run
                   chips below only carry what differs. */
                const envs = [
                  ...new Set(comparison.runs.map((r) => identityOf(r).envName)),
                ];
                return `${envs.length === 1 && envs[0] ? `${envs[0]} · ` : ""}${same ? "same behaviour, only credentials differ · " : ""}`;
              })()}
              {comparison.coverage.shared} scenarios
              {comparison.coverage.partial > 0 &&
                ` · ${comparison.coverage.partial} in only some`}
              {sampleNote}
            </Typography>
          </Box>
          <Box flex={1} />
        </Stack>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 2, pb: 1.5, flexWrap: "wrap", rowGap: 1 }}
        >
          {comparison.runs.map((r, i) => (
            <Stack
              key={r.id}
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{
                pl: 1,
                pr: comparison.runs.length > 2 ? 0.25 : 1,
                py: 0.5,
                borderRadius: "6px",
                border: "1px solid",
                borderColor: alpha(r.color, 0.35),
                bgcolor: (t) =>
                  alpha(r.color, t.palette.mode === "dark" ? 0.12 : 0.06),
              }}
            >
              <CompareLetter letter={r.letter} color={r.color} />
              {/*
                Both halves of the pairing on the pill — a comparison is
                `env × agent`, and reading only "agent v2" hides "which
                world did that number come from". Env part is muted so
                the label stays scannable when both are the same.
              */}
              <Tooltip
                arrow
                title={`${runLabelOf(r)} · ${identityOf(r).agentLabel}${identityOf(r).env ? ` × ${identityOf(r).envName}` : ""} · ${scenarioPassText(r, { env, envState })}`}
              >
                <Typography
                  noWrap
                  sx={{ typography: "s3", fontWeight: 600, maxWidth: 220 }}
                >
                  {identityOf(r).agentLabel}
                  {!sharedEnv && identityOf(r).env && (
                    <Box
                      component="span"
                      sx={{ color: "text.tertiary", fontWeight: 500 }}
                    >
                      {` × ${identityOf(r).envName}`}
                    </Box>
                  )}
                  <Box
                    component="span"
                    sx={{ color: "text.secondary", fontWeight: 500 }}
                  >
                    {comparison.coverage.notInAll > 0 &&
                    comparison.coverage.common > 0
                      ? ` · ${r.sharedPassRate ?? "—"}% on ${comparison.coverage.common} shared`
                      : ` · ${String(scenarioPassText(r, { env, envState })).split(" ")[0]}`}
                  </Box>
                </Typography>
              </Tooltip>
              {i === 0 && (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  baseline
                </Typography>
              )}
              {comparison.runs.length > 2 && (
                <Tooltip arrow title="Remove from this comparison">
                  <IconButton
                    size="small"
                    onClick={() => setRuns(runIds.filter((id) => id !== r.id))}
                  >
                    <Iconify
                      icon="solar:close-linear"
                      width={12}
                      sx={{ color: "text.tertiary" }}
                    />
                  </IconButton>
                </Tooltip>
              )}
            </Stack>
          ))}
          {(others.length > 0 || crossOthers.length > 0) && (
            <Button
              variant="outlined"
              size="small"
              onClick={(e) => openAddMenu(e.currentTarget)}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              sx={{
                border: "1px dashed",
                borderColor: "divider",
              }}
            >
              Add run
            </Button>
          )}

          <Box flex={1} />

          <SavedViews
            views={savedViews}
            activeId={activeViewId}
            dirty={dirty}
            onApply={applyView}
            onSave={saveView}
            onUpdate={updateView}
            onRename={renameView}
            onDelete={deleteView}
          />

          <CompareActions
            filters={filters}
            onFilters={setFilters}
            evals={comparison.evals}
            view={view}
            onView={setViewOverride}
            onSaveDefault={() => {
              patch({ compareView: view });
              enqueueSnackbar(
                "Saved as the default view for this environment",
                { variant: "success" },
              );
            }}
            onResetView={() => {
              setViewOverride(defaultView());
              patch({ compareView: null });
            }}
            selectedCount={selected.length}
            onExport={exportComparison}
            onCopyLink={() => {
              navigator.clipboard?.writeText(window.location.href);
              enqueueSnackbar("Link copied", { variant: "success" });
            }}
            onRerun={rerunSelected}
            onRegrade={() =>
              enqueueSnackbar(
                `Re-grading ${selected.length} scenario${selected.length === 1 ? "" : "s"} from recorded evidence — no calls are made`,
                { variant: "info" },
              )
            }
            onOptimize={optimizeSelected}
          />
        </Stack>
      </Box>

      <RuleChangeDialog
        open={!!answering}
        onClose={() => setAnswering(null)}
        envs={[env]}
        version={answering}
        origin={
          answering
            ? versionOriginOf(answering, agentSourceOf(envState.agent))
            : undefined
        }
      />

      <Menu
        open={!!addAnchor}
        anchorEl={addAnchor}
        onClose={closeAddMenu}
        slotProps={{ paper: { sx: { maxHeight: 480, minWidth: 340 } } }}
      >
        {others.map((r) => {
          const picked = addStaging.includes(r.id);
          return (
            <MenuItem
              key={r.id}
              sx={{ typography: "s2", gap: 0.5, py: 0.75 }}
              onClick={() => toggleAddStaging(r.id)}
              selected={picked}
            >
              <Checkbox
                size="small"
                checked={picked}
                tabIndex={-1}
                sx={{
                  p: 0.5,
                  mr: 0.5,
                  pointerEvents: "none",
                  ...neutralCheckboxSx,
                }}
              />
              <CompareLetter letter={r.letter} color={r.color} />
              <Box component="span" sx={{ flex: 1, minWidth: 0, ml: 1 }}>
                {r.kind === "trial" ? runLabelOf(r) : `Run ${r.ordinal}`}{" "}
                · {identityOf(r).agentLabel}
                {identityOf(r).env && (
                  <Box component="span" sx={{ color: "text.tertiary" }}>
                    {" "}
                    × {identityOf(r).envName}
                  </Box>
                )}
                {r.setupDiff?.length > 0 && (
                  <Tooltip
                    arrow
                    title={`Not the baseline's setup: ${r.setupDiff.join(", ")}. Differences may come from that, not the agent.`}
                  >
                    <Box
                      component="span"
                      sx={{ color: "status.warning.fg", ml: 0.75 }}
                    >
                      · different setup
                    </Box>
                  </Tooltip>
                )}
              </Box>
              {/* Scenario pass with its fraction, from the same helper as the
                  Runs table's Scenario pass cell (runStatsOf). An epoch's
                  run that scored only its target scenarios says so — "0% 0/1"
                  is a real result on one scenario, not an empty run. */}
              <Tooltip
                arrow
                title={
                  r.kind === "trial" && r.partial
                    ? "Target scenarios only — held-back scenarios are checked on the change it picks."
                    : ""
                }
              >
                <Box
                  component="span"
                  sx={{
                    color: "text.tertiary",
                    fontVariantNumeric: "tabular-nums",
                    whiteSpace: "nowrap",
                  }}
                >
                  {scenarioPassText(r, { env, envState })}
                </Box>
              </Tooltip>
            </MenuItem>
          );
        })}
        {crossOthers.map((c, i) => {
          const picked = addStaging.includes(c.key);
          /* Every other environment reads side by side — one heading. */
          const first = i === 0;
          return [
            first && (
              <Typography
                key={`h-${c.key}`}
                sx={{
                  px: 2,
                  pt: 1,
                  pb: 0.5,
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.tertiary",
                  textTransform: "none",
                  letterSpacing: 0.4,
                }}
              >
                Other environments · side by side
              </Typography>
            ),
            <MenuItem
              key={c.key}
              sx={{ typography: "s2", gap: 0.5, py: 0.75 }}
              onClick={() => toggleAddStaging(c.key)}
              selected={picked}
            >
              <Checkbox
                size="small"
                checked={picked}
                tabIndex={-1}
                sx={{
                  p: 0.5,
                  mr: 0.5,
                  pointerEvents: "none",
                  ...neutralCheckboxSx,
                }}
              />
              <Box component="span" sx={{ flex: 1, minWidth: 0, ml: 0.5 }}>
                {`Run ${c.run.ordinal || ""} · ${c.label}`.replace(
                  "Run  ",
                  "Run ",
                )}
                <Box
                  component="span"
                  sx={{ color: "text.tertiary" }}
                >{` · ${c.envName}`}</Box>
              </Box>
            </MenuItem>,
          ];
        })}
        {/* Footer: staging count + commit action. Sticky so it stays put
            while the list scrolls. */}
        <Box
          sx={{
            position: "sticky",
            bottom: 0,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            px: 1.5,
            py: 1,
            mt: 0.5,
            display: "flex",
            alignItems: "center",
            gap: 1,
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
          >
            {addStaging.length
              ? `${addStaging.length} selected`
              : "Pick runs to add"}
          </Typography>
          <Button
            size="small"
            onClick={closeAddMenu}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!addStaging.length}
            onClick={commitAdd}
          >
            Add {addStaging.length || ""}
          </Button>
        </Box>
      </Menu>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        {/* The verdict over the comparable pairs — or "side by side" when
            the runs sit on different environment versions. */}
        {(() => {
          /* Must-pass regressions first; promoting the last run is its own reviewed step. */
          const mustPass = mustPassRegressionsOf(comparison.rows);
          const first = comparison.runs[0];
          const last =
            comparison.runs.find((r) => r.id === read?.targetId) ||
            comparison.runs[1];
          const candidate = last?.agentVersion || null;
          const candidateLabel = (last && identityOf(last).agent) || candidate;
          const baseline = first?.agentVersion || null;
          const baselineLabel = (first && identityOf(first).agent) || baseline;
          const verdict =
            verdictWithMustPass(read?.label, mustPass)?.label || "No verdict";
          return (
            <>
              <PromoteDialog
                open={promoting}
                candidate={candidate}
                baseline={baseline}
                verdict={verdict}
                regressions={mustPass}
                onClose={() => setPromoting(false)}
                onPromote={({ note, reviewed }) => {
                  setPromoting(false);
                  patch(
                    promotePatch(envState, {
                      version: candidate,
                      runId: last?.id,
                      baseline,
                      verdict,
                      reviewed,
                      note,
                    }),
                  );
                  enqueueSnackbar(
                    `Agent ${candidate} promoted — ${baseline} stays available to roll back to`,
                    { variant: "success" },
                  );
                }}
              />
              <CompareVerdict
                read={read}
                held={held}
                mustPass={mustPass}
                onShowMustPass={() =>
                  setViewOverride({ ...view, quick: "critical" })
                }
                promote={
                  candidate && baseline && candidateLabel !== baselineLabel
                    ? {
                        candidate,
                        label: candidateLabel,
                        baseline,
                        onClick: () => setPromoting(true),
                        disabled: !gates.canEdit
                          ? gates.editReason
                          : candidate === baseline
                            ? `Both runs are on agent ${candidate} — there's no other version to promote.`
                            : null,
                      }
                    : null
                }
                runs={comparison.runs}
                identityOf={identityOf}
                envHeadline={envHeadline}
                onExcludeRechecked={setExcludeRechecked}
                onFix={(fix) => startFix(fix)}
                onImprove={() => {
                  const latest = comparison.runs[comparison.runs.length - 1];
                  if (latest?.id)
                    navigate(
                      `${paths.dashboard.simulate.simulationRun(env.id, latest.id)}?improveNew=1`,
                    );
                }}
                onWarning={(w) => {
                  if (w.kind === "regrade" && read) {
                    /* Re-grading reads stored traces — recorded on the environment
                 so the comparison reads both runs under one version. */
                    patch({
                      compareRegrades: [
                        ...(envState.compareRegrades || []),
                        { a: read.baselineId, b: read.targetId, to: w.to },
                      ],
                    });
                    enqueueSnackbar(
                      "Both runs re-graded from their stored traces — no calls were made",
                      { variant: "success" },
                    );
                  } else if (w.kind === "bridge" && w.scenarioIds?.length) {
                    startFix({
                      agent: w.agent,
                      scenarioIds: w.scenarioIds,
                      repeats: w.repeats,
                    });
                  }
                }}
                questions={[]}
                onAnswer={(v) => setAnswering(v)}
              />
            </>
          );
        })()}

        {/* One card for both. The distribution is a summary of the rows
            underneath it, so a second border and a gap between them spends
            space on a boundary that is not real. */}
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            /* clip, not hidden: hidden makes this box the scroll container,
               and the table header could no longer stay fixed. */
            overflow: "clip",
          }}
        >
          {view.showChart && (
            <SectionCard
              title="Score distribution"
              sx={{
                border: "none",
                borderRadius: 0,
                bgcolor: "transparent",
                /* Closed, the header's own bottom line would sit on the
                   next section's top line and read as one thick stroke. */
                /* A slim header: the chart is one click away, not a band. */
                "& > :first-of-type": {
                  py: 0.75,
                  ...(!chartOpen && { borderBottom: "none" }),
                },
              }}
              action={
                <Stack direction="row" alignItems="center" spacing={2}>
                  {/* Legend and eval picker only steer the open chart. */}
                  {chartOpen && (
                  <Stack
                    direction="row"
                    spacing={1.5}
                    sx={{ display: { xs: "none", md: "flex" } }}
                  >
                    {comparison.runs.map((r) => (
                      <Stack
                        key={r.id}
                        direction="row"
                        alignItems="center"
                        spacing={0.625}
                      >
                        <Box
                          sx={{
                            width: 8,
                            height: 8,
                            borderRadius: "50%",
                            bgcolor: r.color,
                          }}
                        />
                        <Typography
                          sx={{ typography: "s3", color: "text.secondary" }}
                        >
                          <Box
                            component="span"
                            sx={{ fontWeight: 600, color: "text.primary" }}
                          >
                            {runLabelOf(r)}
                          </Box>{" "}
                          {identityOf(r).agentLabel}
                          {!sharedEnv && identityOf(r).env && (
                            <Box
                              component="span"
                              sx={{ color: "text.tertiary" }}
                            >
                              {" "}
                              × {identityOf(r).envName}
                            </Box>
                          )}
                        </Typography>
                      </Stack>
                    ))}
                  </Stack>
                  )}
                  {chartOpen && (
                  <TextField
                    select
                    size="small"
                    value={evalId || ""}
                    onChange={(e) => setPickedEval(e.target.value)}
                    sx={{
                      minWidth: 170,
                      "& .MuiInputBase-input": { typography: "s3", py: 0.5 },
                    }}
                  >
                    {comparison.evals.map((e) => (
                      <MenuItem
                        key={e.id}
                        value={e.id}
                        sx={{ typography: "s2" }}
                      >
                        {e.name}
                      </MenuItem>
                    ))}
                  </TextField>
                  )}
                  <IconButton
                    size="small"
                    aria-label={
                      chartOpen ? "Hide score distribution" : "Show score distribution"
                    }
                    aria-expanded={chartOpen}
                    onClick={() => setChartOpen((o) => !o)}
                  >
                    <Iconify
                      icon={
                        chartOpen
                          ? "solar:alt-arrow-up-linear"
                          : "solar:alt-arrow-down-linear"
                      }
                      width={16}
                      sx={{ color: "text.tertiary" }}
                    />
                  </IconButton>
                </Stack>
              }
            >
              {chartOpen && (
              <Box sx={{ px: 1.5, pt: 0.5, pb: 0 }}>
                <ReactApexChart
                  type="bar"
                  height={140}
                  series={comparison.runs.map((r, i) => ({
                    name: `${r.letter} · ${identityOf(r).label}`,
                    data: bands.map((b) => b.counts[i]),
                  }))}
                  options={{
                    chart: {
                      toolbar: { show: false },
                      fontFamily: theme.typography.fontFamily,
                      background: "transparent",
                    },
                    theme: { mode: theme.palette.mode },
                    colors: comparison.runs.map((r) => r.color),
                    plotOptions: {
                      bar: { columnWidth: "48%", borderRadius: 2 },
                    },
                    dataLabels: { enabled: false },
                    legend: { show: false },
                    grid: {
                      borderColor: theme.palette.divider,
                      strokeDashArray: 4,
                      xaxis: { lines: { show: false } },
                    },
                    xaxis: {
                      categories: bands.map((b) => b.label),
                      axisBorder: { show: false },
                      axisTicks: { show: false },
                      labels: {
                        style: {
                          colors: theme.palette.text.secondary,
                          fontSize: "12px",
                        },
                      },
                    },
                    yaxis: {
                      min: 0,
                      max: peak,
                      tickAmount: Math.min(peak, 3),
                      labels: {
                        style: {
                          colors: theme.palette.text.secondary,
                          fontSize: "12px",
                        },
                        formatter: (v) => `${Math.round(v)}`,
                      },
                    },
                    tooltip: { y: { formatter: (v) => `${v} scenarios` } },
                  }}
                />
              </Box>
              )}
            </SectionCard>
          )}

          {/* Title, count, search and the diff switch on one line — they were
            three stacked bands saying one thing. */}
          <Stack
            direction="row"
            alignItems="center"
            spacing={2}
            sx={{
              px: 2.5,
              py: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            {/* One fact the rows below don't already say. */}
            <Typography
              noWrap
              sx={{
                flex: 1,
                minWidth: 0,
                typography: "s2",
                color: "text.secondary",
              }}
            >
              {view.view !== "table" &&
                `${comparedWithText(comparison.runs)} · `}
              {changed} of {comparison.rows.length} changed
              {rows.length !== comparison.rows.length &&
                ` · showing ${rows.length}`}
              {/* The table's number columns read one run against the
                  baseline; which one is picked here. */}
              {view.view === "table" && comparison.runs.length > 1 && (
                <>
                  {" · Numbers for "}
                  {comparison.runs.length > 2 ? (
                    <Select
                      variant="standard"
                      disableUnderline
                      value={
                        numbersFor &&
                        comparison.runs.some((r) => r.id === numbersFor)
                          ? numbersFor
                          : comparison.runs[1].id
                      }
                      onChange={(e) => setNumbersFor(e.target.value)}
                      sx={{
                        typography: "s2",
                        fontWeight: 600,
                        color: "text.primary",
                        "& .MuiSelect-select": { py: 0, pr: "18px !important", minWidth: 0 },
                        "& .MuiSelect-icon": { right: 0, width: 16, height: 16, top: "calc(50% - 8px)" },
                      }}
                    >
                      {comparison.runs.slice(1).map((r) => (
                        <MenuItem key={r.id} value={r.id} sx={{ typography: "s2" }}>
                          {runLabelOf(r)}
                        </MenuItem>
                      ))}
                    </Select>
                  ) : (
                    <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>
                      {runLabelOf(comparison.runs[1])}
                    </Box>
                  )}
                  {` vs ${runLabelOf(comparison.runs[0])}`}
                </>
              )}
            </Typography>
            <CompareSearchBar
              query={query}
              onQuery={setQuery}
              view={view}
              onView={setViewOverride}
            />
          </Stack>

          {rows.length === 0 ? (
            <EmptyState
              icon="solar:filter-linear"
              title="No scenarios match"
              body="Loosen the filters, or clear the search."
            />
          ) : view.view === "summary" ? (
            <CompareSummary
              comparison={comparison}
              evals={comparison.evals}
              env={env}
              envState={envState}
            />
          ) : view.view === "grid" ? (
            <CompareGrid
              comparison={comparison}
              groups={groups}
              evals={comparison.evals}
              env={env}
              envState={envState}
              view={view}
              onOpen={setOpenRow}
              bandAction={bandAction}
            />
          ) : (
            <CompareRunColumns
              compareId={numbersFor}
              allRows={comparison.rows}
              groups={groups}
              bandAction={bandAction}
              view={view}
              rowPad={rowPad}
              evals={comparison.evals}
              runs={comparison.runs}
              onOpen={setOpenRow}
            />
          )}
        </Box>
      </Box>

      {/* Opening a row is not a peek at a cell — it is the call, in every run,
          with the same Show Diff the table is already reading under. */}
      <CallCompare
        open={!!openRow}
        row={openRow}
        rows={rows}
        runs={comparison.runs}
        env={env}
        envState={envState}
        diff={view.diff}
        onDiff={(on) => setViewOverride({ ...view, diff: on })}
        onOpenRow={setOpenRow}
        onClose={() => setOpenRow(null)}
      />
    </Stack>
  );
}
