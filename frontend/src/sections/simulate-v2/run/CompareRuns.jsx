import PropTypes from "prop-types";
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
  isExactRegression,
  behaviourDiff,
  ROW_HEIGHTS,
  viewSnapshot,
  snapshotsEqual,
  newViewId,
} from "../_mock/compareView";
import { withPairs, bridgeOf, priceOf, priceLine } from "../_mock/pairs";
import { useEnvState, useSimStore } from "../store";
import { mustPassRegressionsOf, promotePatch } from "../_mock/promote";
import { useRoleGates } from "../_mock/previewRole";
import PromoteDialog from "./PromoteDialog";
import { runCreditsOf } from "../_mock/usageLedger";
import {
  SectionCard,
  EmptyState,
  PersonaBadge,
  Verdict,
  DomainChip,
} from "../components/primitives";
import CallCompare from "./CallCompare";
import CompareActions, {
  CompareSearchBar,
  SavedViews,
} from "./CompareControls";
import CompareGrid from "./CompareGrid";
import CompareSummary from "./CompareSummary";
import CompareVerdict from "./CompareVerdict";
import OutcomeChip from "./CompareOutcome";
import { csatOf } from "./traceTableHelpers";
import RuleChangeDialog from "../components/RuleChangeDialog";
import { agentSourceOf } from "../_mock/agentRegistry";
import { agentVersions, behaviourLabelOf } from "../_mock/versions";
import { textTone } from "../components/textTone";
import { versionOriginOf } from "../_mock/specChange";
import { subGoalGroups, heldFixedAcross } from "../_mock/compareHeld";
import { snapshotOfRun, compareEnvHeadline } from "../_mock/envSnapshot";
import { clickable, focusRing } from "../components/clickable";
import { runStatsOf } from "../_mock/runCompare";

/* "62% 67/108" — scenario pass and its fraction, from runStatsOf like the Runs
   table's Scenario pass cell. An improvement's run is read from its own
   per-scenario outcomes (trialTasks), even once Compare has hydrated it with
   the source run's calls, so the menu, the pill and the table agree. */
const scenarioPassText = (r) => {
  const st = runStatsOf(
    r?.kind === "trial" && r.trialTasks?.length
      ? { ...r, tasks: r.trialTasks }
      : r,
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
  const [selected, setSelected] = useState([]);
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
          })
        : { comparison: comparisonRaw, read: null },
    [comparisonRaw, env, envState, excludeRechecked],
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
    const b = snapshotOfRun(runs[runs.length - 1], env, envState);
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
    return [...manual, ...trials]
      .filter((r) => !runIds.includes(r.id))
      .sort((a, b) => new Date(b.finishedAt) - new Date(a.finishedAt))
      .map((r) => {
        const chip = chips.get(r.id);
        return chip ? { ...r, letter: chip.letter, color: chip.color } : r;
      });
  }, [env, envState, runIds]);

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
        regressionsFirst: view.sort === "movement",
        groupFn: (rs) =>
          view.group === "subGoal" && env
            ? subGoalGroups(rs, comparison.runs, env, envState)
            : groupRows(rs, view.group),
      }),
    [rows, view.group, view.sort, comparison.runs, env, envState],
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

  /* n is part of the measurement. Runs sampled a different number of times are
     not reporting the same quantity, and the header says so rather than
     letting two differently-sampled rates sit side by side as equals. */
  const sampleNote =
    comparison.coverage.repeats.length === 1
      ? ` · ${comparison.coverage.repeats[0]} repeat${comparison.coverage.repeats[0] === 1 ? "" : "s"} each`
      : ` · repeats per scenario differ (${comparison.coverage.repeats.join(", ")})`;

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
            size="small"
            onClick={back}
            startIcon={
              <Iconify icon="solar:alt-arrow-left-linear" width={16} />
            }
            sx={{ color: "text.secondary", flexShrink: 0, minWidth: 0 }}
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
                /* The sample agent (Reference) is another agent at any label. */
                const labels = [
                  ...new Set(
                    comparison.runs.map((r) =>
                      r.reference ? `sample ${r.agentVersion}` : r.agentVersion,
                    ),
                  ),
                ];
                const n = labels.length;
                /* A credentials-only version behaves as its base — said, so
                   v5 against v5.1 doesn't read as two agents. */
                const known = agentVersions(envState);
                const same =
                  n > 1 &&
                  new Set(labels.map((l) => behaviourLabelOf(known, l)))
                    .size === 1;
                return `${comparison.runs.length} runs · ${n} agent version${n === 1 ? "" : "s"}${same ? ", same behaviour — only credentials differ" : ""}`;
              })()}{" "}
              · {comparison.coverage.shared} scenarios in every run
              {comparison.coverage.partial > 0 &&
                ` · ${comparison.coverage.partial} in only some`}
              {comparison.coverage.unmeasured > 0 &&
                ` · ${comparison.coverage.unmeasured} not measured`}
              {sampleNote}
            </Typography>
          </Box>
          <Box flex={1} />
          <Tooltip arrow title="Export comparison">
            <IconButton size="small" onClick={exportComparison}>
              <Iconify
                icon="solar:download-minimalistic-linear"
                width={16}
                sx={{ color: "text.tertiary" }}
              />
            </IconButton>
          </Tooltip>
          <Tooltip arrow title="Copy link">
            <IconButton
              size="small"
              onClick={() => {
                navigator.clipboard?.writeText(window.location.href);
                enqueueSnackbar("Link copied", { variant: "success" });
              }}
            >
              <Iconify
                icon="solar:share-linear"
                width={16}
                sx={{ color: "text.tertiary" }}
              />
            </IconButton>
          </Tooltip>
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
              <Letter letter={r.letter} color={r.color} />
              {/*
                Both halves of the pairing on the pill — a comparison is
                `env × agent`, and reading only "agent v2" hides "which
                world did that number come from". Env part is muted so
                the label stays scannable when both are the same.
              */}
              <Typography
                noWrap
                sx={{ typography: "s3", fontWeight: 600, maxWidth: 280 }}
              >
                {r.reference ? "Reference · sample " : ""}
                {identityOf(r).agentLabel}
                {identityOf(r).env && (
                  <Box
                    component="span"
                    sx={{ color: "text.tertiary", fontWeight: 500 }}
                  >
                    {" "}
                    × environment {identityOf(r).env}
                  </Box>
                )}
                {/* When the runs covered different scenarios, the pill reads the
                    same shared set the summary does, and says so. */}
                {comparison.coverage.notInAll > 0 &&
                comparison.coverage.common > 0
                  ? ` · ${r.sharedPassRate ?? "—"}% on ${comparison.coverage.common} shared`
                  : ` · ${scenarioPassText(r)}`}
              </Typography>
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
              size="small"
              onClick={(e) => openAddMenu(e.currentTarget)}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              sx={{
                color: "text.secondary",
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
              <Letter letter={r.letter} color={r.color} />
              <Box component="span" sx={{ flex: 1, minWidth: 0, ml: 1 }}>
                {r.kind === "trial"
                  ? `Run ${r.trialN}${r.selfImprovementName ? ` · ${r.selfImprovementName}` : ""}`
                  : `Run ${r.ordinal}`}{" "}
                · {identityOf(r).agentLabel}
                {identityOf(r).env && (
                  <Box component="span" sx={{ color: "text.tertiary" }}>
                    {" "}
                    × environment {identityOf(r).env}
                  </Box>
                )}
              </Box>
              {/* Scenario pass with its fraction, from the same helper as the
                  Runs table's Scenario pass cell (runStatsOf). An improvement's
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
                  {scenarioPassText(r)}
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
          const last = comparison.runs[comparison.runs.length - 1];
          const candidate = last?.agentVersion || null;
          const baseline = first?.agentVersion || null;
          const verdict = read?.label?.label || "No verdict";
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
                  candidate && baseline
                    ? {
                        candidate,
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
            overflow: "hidden",
          }}
        >
          {view.showChart && (
            <SectionCard
              title="Score distribution"
              sx={{ border: "none", borderRadius: 0, bgcolor: "transparent" }}
              action={
                <Stack direction="row" alignItems="center" spacing={2}>
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
                          {identityOf(r).agentLabel}
                          {identityOf(r).env && (
                            <Box
                              component="span"
                              sx={{ color: "text.tertiary" }}
                            >
                              {" "}
                              × environment {identityOf(r).env}
                            </Box>
                          )}
                        </Typography>
                      </Stack>
                    ))}
                  </Stack>
                  <TextField
                    select
                    size="small"
                    value={evalId || ""}
                    onChange={(e) => setPickedEval(e.target.value)}
                    sx={{
                      minWidth: 190,
                      "& .MuiInputBase-input": { typography: "s2", py: 0.75 },
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
                </Stack>
              }
            >
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
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                Scenario by scenario
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {changed} of {comparison.rows.length} ended differently
                {(() => {
                  const n = comparison.rows.filter(isExactRegression).length;
                  return n ? ` · ${n} exact passed → failed` : "";
                })()}
                {` · showing ${rows.length}`}
              </Typography>
            </Box>
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
            <TableView
              groups={groups}
              bandAction={bandAction}
              view={view}
              rowPad={rowPad}
              evals={comparison.evals}
              selected={selected}
              onToggle={(id) =>
                setSelected((prev) =>
                  prev.includes(id)
                    ? prev.filter((x) => x !== id)
                    : [...prev, id],
                )
              }
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

/* ── table view ──────────────────────────────────────────────────────────── */

/* Per-task synthesised metrics — same derivations the traces table uses,
   so a value read on a single-run screen equals the value read here. */
const hashStr = (s) => {
  let h = 0;
  for (let i = 0; i < String(s || "").length; i += 1)
    h = (h * 31 + String(s).charCodeAt(i)) >>> 0;
  return h;
};
const turnsOf = (t) => (t ? t.steps?.length ?? 0 : null);
const latencyOf = (t) =>
  t ? t.latencyMs ?? 280 + (hashStr(t.id) % 320) : null;

function TableView({
  groups,
  view,
  rowPad,
  selected,
  onToggle,
  onOpen,
  evals,
  bandAction,
}) {
  /* System-metric strip on the right of the row — same set the traces
     table shows: CSAT, Turns, Latency, Duration, Tokens, Cost. Widens
     with the number of active columns so nothing crushes. */
  const metricBits = [
    view.columns.csat && "CSAT",
    view.columns.turns && "Turns",
    view.columns.latency && "Latency",
    view.columns.duration && "Duration",
    view.columns.tokens && "Tokens",
    view.columns.cost && "Model cost",
  ].filter(Boolean);
  const metricColWidth = metricBits.length
    ? metricBits.length * 76 + (metricBits.length - 1) * 10
    : 0;
  const showScorers = view.columns.scorers && (evals?.length || 0) > 0;
  const scorersColWidth = showScorers
    ? evals.length * 92 + (evals.length - 1) * 10
    : 0;
  /* System metrics before evals — matches the single-run traces table,
     where evals live at the far right of the row. */
  /* No checkbox column — this screen is for reading a comparison, not for
     picking scenarios. Bulk actions live on the runs list. */
  const gridTemplate =
    `260px 1fr ${metricColWidth ? `${metricColWidth}px ` : ""}${scorersColWidth ? `${scorersColWidth}px` : ""}`.trim();

  return (
    <>
      <Box
        sx={{
          display: { xs: "none", lg: "grid" },
          gridTemplateColumns: gridTemplate,
          columnGap: 2,
          px: 2.5,
          py: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <ColHead>Scenario</ColHead>
        <ColHead>
          {view.diff
            ? "What changed against the baseline"
            : "What each run did with it"}
        </ColHead>
        {metricBits.length > 0 && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: `repeat(${metricBits.length}, minmax(56px, 1fr))`,
              columnGap: 1.25,
            }}
          >
            {metricBits.map((label) => (
              <ColHead key={label} sx={{ textAlign: "right" }}>
                {label}
              </ColHead>
            ))}
          </Box>
        )}
        {showScorers && (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: `repeat(${evals.length}, minmax(72px, 1fr))`,
              columnGap: 1.25,
            }}
          >
            {evals.map((e) => (
              <ColHead key={e.id} sx={{ textAlign: "right" }}>
                {e.name}
              </ColHead>
            ))}
          </Box>
        )}
      </Box>

      {groups.map((group) => (
        <Box key={group.id}>
          {group.label && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 2.5,
                py: 1,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                noWrap
                sx={{
                  fontSize: 13,
                  fontWeight: 600,
                  minWidth: 0,
                  flexShrink: group.note ? 0 : 1,
                  color:
                    group.tone === "bad"
                      ? "status.danger.fg"
                      : "text.secondary",
                }}
              >
                {group.label}
              </Typography>
              {group.mark === "critical" && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  critical
                </Typography>
              )}
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
              >
                {group.rows.length}
              </Typography>
              {group.note && (
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
                >
                  · {group.note}
                </Typography>
              )}
              {!group.rates && bandAction?.(group) && (
                <>
                  <Box flex={1} />
                  {bandAction(group)}
                </>
              )}
              {group.rates && (
                <>
                  <Box flex={1} />
                  {group.rates.map((r) => (
                    <Tooltip
                      key={r.runId}
                      arrow
                      title={
                        r.n
                          ? `Passed in run ${r.letter} on ${r.n} shared scenario${r.n === 1 ? "" : "s"} that check it`
                          : "No shared scenario measured in every run"
                      }
                    >
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={0.5}
                        sx={{ flexShrink: 0 }}
                      >
                        <Letter letter={r.letter} color={r.color} />
                        <Typography
                          sx={{
                            typography: "s3",
                            fontWeight: 600,
                            fontVariantNumeric: "tabular-nums",
                            minWidth: 30,
                          }}
                        >
                          {r.rate == null ? "—" : `${r.rate}%`}
                        </Typography>
                      </Stack>
                    </Tooltip>
                  ))}
                </>
              )}
            </Stack>
          )}

          <Stack
            divider={
              <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
            }
          >
            {group.rows.map((row) => (
              <Box
                key={row.id}
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", lg: gridTemplate },
                  columnGap: 2,
                  px: 2.5,
                  py: rowPad,
                  cursor: "pointer",
                  "&:hover": {
                    bgcolor: "action.hover",
                    "& .row-open": { opacity: 1 },
                  },
                  ...focusRing,
                }}
                {...clickable(() => onOpen(row))}
              >
                <Box minWidth={0}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <OutcomeChip pair={row.pair} />
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {row.title}
                    </Typography>
                  </Stack>
                  {view.rowHeight !== "compact" && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        mt: 0.25,
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                      }}
                    >
                      {row.task}
                    </Typography>
                  )}
                  {view.rowHeight === "large" && (
                    <Box sx={{ mt: 0.75 }}>
                      <PersonaBadge persona={row.persona} compact />
                    </Box>
                  )}
                </Box>

                <Stack spacing={1.25} sx={{ minWidth: 0 }}>
                  {row.cells.map((c, i) => (
                    <Stack
                      key={c.runId}
                      direction="row"
                      spacing={1.25}
                      alignItems="flex-start"
                    >
                      <Letter letter={c.letter} color={c.color} />
                      <Box sx={{ width: 78, flexShrink: 0 }}>
                        <Verdict
                          status={c.status}
                          passes={c.passes}
                          repeats={c.repeats}
                        />
                      </Box>
                      {/* Who the failure belongs to, on the row that reports
                          it — an unmeasured row must never read as the agent's. */}
                      {c.domain && !c.domain.measured && (
                        <DomainChip domain={c.domain} dense />
                      )}
                      <Typography
                        sx={{
                          flex: 1,
                          minWidth: 0,
                          typography: "s2",
                          color: "text.secondary",
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}
                      >
                        {view.diff && i > 0 ? (
                          <DiffText
                            diff={behaviourDiff(row.cells[0].task, c.task)}
                          />
                        ) : (
                          <>
                            {c.deciding.kind === "claim" && (
                              <Box
                                component="span"
                                sx={{
                                  color: "status.danger.fg",
                                  fontWeight: 600,
                                }}
                              >
                                Said, not done —{" "}
                              </Box>
                            )}
                            {c.deciding.text}
                          </>
                        )}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>

                {/* System metrics column — sits before evals, matching the
                    header order and the single-run traces table. */}
                <Stack
                  spacing={1.25}
                  sx={{
                    display: { xs: "none", lg: "flex" },
                    position: "relative",
                  }}
                >
                  {/* The row opens a side-by-side of the transcripts; without a
                      mark, nothing on it says that. */}
                  <Iconify
                    icon="solar:alt-arrow-right-linear"
                    width={16}
                    className="row-open"
                    sx={{
                      position: "absolute",
                      right: -14,
                      top: 2,
                      opacity: 0,
                      transition: "opacity .12s",
                      color: "text.tertiary",
                      pointerEvents: "none",
                    }}
                  />
                  {(() => {
                    const columns = [
                      view.columns.csat && {
                        key: "csat",
                        get: (c) => {
                          const v = csatOf(c.task);
                          return v != null ? String(v) : "—";
                        },
                      },
                      view.columns.turns && {
                        key: "turns",
                        get: (c) => {
                          const v = turnsOf(c.task);
                          return v != null ? String(v) : "—";
                        },
                      },
                      view.columns.latency && {
                        key: "latency",
                        get: (c) => {
                          const v = latencyOf(c.task);
                          return v != null ? `${v}ms` : "—";
                        },
                      },
                      view.columns.duration && {
                        key: "duration",
                        get: (c) =>
                          c.task ? `${(c.durationMs / 1000).toFixed(1)}s` : "—",
                      },
                      view.columns.tokens && {
                        key: "tokens",
                        get: (c) => (c.task ? c.tokens.toLocaleString() : "—"),
                      },
                      view.columns.cost && {
                        key: "cost",
                        get: (c) =>
                          !c.task
                            ? "—"
                            : c.costUnverified
                              ? "unverified"
                              : `≈ $${(c.cost || 0).toFixed(2)}`,
                      },
                    ].filter(Boolean);
                    return row.cells.map((c) => (
                      <Box
                        key={c.runId}
                        sx={{
                          display: "grid",
                          gridTemplateColumns: `repeat(${columns.length}, minmax(56px, 1fr))`,
                          columnGap: 1.25,
                          alignItems: "baseline",
                        }}
                      >
                        {columns.map((col) => (
                          <Typography
                            key={col.key}
                            noWrap
                            sx={{
                              textAlign: "right",
                              typography: "s2",
                              color: "text.primary",
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            {col.get(c)}
                          </Typography>
                        ))}
                      </Box>
                    ));
                  })()}
                </Stack>

                {/* Eval scores column — sits after the system metrics so
                    the run's numbers cluster together and evals close the
                    row on the right, mirroring the traces table. */}
                {showScorers && (
                  <Stack
                    spacing={1.25}
                    sx={{ display: { xs: "none", lg: "flex" } }}
                  >
                    {row.cells.map((c) => (
                      <Box
                        key={c.runId}
                        sx={{
                          display: "grid",
                          gridTemplateColumns: `repeat(${evals.length}, minmax(72px, 1fr))`,
                          columnGap: 1.25,
                          alignItems: "baseline",
                        }}
                      >
                        {evals.map((e) => {
                          const r = (c.task?.evalResults || []).find(
                            (x) => x.id === e.id,
                          );
                          if (!r) {
                            return (
                              <Typography
                                key={e.id}
                                sx={{
                                  textAlign: "right",
                                  typography: "s2",
                                  color: "text.disabled",
                                  fontVariantNumeric: "tabular-nums",
                                }}
                              >
                                —
                              </Typography>
                            );
                          }
                          return (
                            <Typography
                              key={e.id}
                              sx={{
                                textAlign: "right",
                                typography: "s2",
                                fontWeight: 600,
                                color: r.passed
                                  ? "text.primary"
                                  : "status.danger.fg",
                                fontVariantNumeric: "tabular-nums",
                              }}
                            >
                              {Math.round(r.score * 100)}%
                            </Typography>
                          );
                        })}
                      </Box>
                    ))}
                  </Stack>
                )}
              </Box>
            ))}
          </Stack>
        </Box>
      ))}
    </>
  );
}
TableView.propTypes = {
  groups: PropTypes.array,
  view: PropTypes.object,
  rowPad: PropTypes.number,
  evals: PropTypes.array,
  selected: PropTypes.array,
  onToggle: PropTypes.func,
  onOpen: PropTypes.func,
  bandAction: PropTypes.func,
};

/** The behaviour diff, in one line. */
function DiffText({ diff }) {
  if (!diff) return null;
  if (diff.identical) {
    return (
      <Box component="span" sx={{ color: "text.tertiary" }}>
        Same behaviour as the baseline.
      </Box>
    );
  }
  const parts = [];
  if (diff.missedNow.length)
    parts.push(`stopped calling ${diff.missedNow.join(", ")}`);
  else if (diff.dropped.length)
    parts.push(`dropped ${diff.dropped.join(", ")}`);
  if (diff.added.length) parts.push(`added ${diff.added.join(", ")}`);
  if (diff.turnDelta)
    parts.push(
      `${Math.abs(diff.turnDelta)} ${diff.turnDelta > 0 ? "more" : "fewer"} turns`,
    );
  if (!parts.length && diff.firstDivergence >= 0) {
    parts.push(
      `same tools, wording differs from turn ${diff.firstDivergence + 1}`,
    );
  }
  return (
    <>
      {diff.verdictChanged && (
        <Box
          component="span"
          sx={{ color: "status.danger.fg", fontWeight: 600 }}
        >
          Verdict changed —{" "}
        </Box>
      )}
      {parts.join(", ")}.
    </>
  );
}
DiffText.propTypes = { diff: PropTypes.object };

function ColHead({ children, sx }) {
  return (
    <Typography
      noWrap
      sx={{
        /* Reads like the product's DataTable header. */
        fontSize: 13,
        fontWeight: 500,
        color: "text.secondary",
        ...sx,
      }}
    >
      {children}
    </Typography>
  );
}
ColHead.propTypes = { children: PropTypes.node, sx: PropTypes.object };

function Letter({ letter, color }) {
  return (
    <Box
      sx={{
        width: 20,
        height: 20,
        borderRadius: "4px",
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        typography: "s3",
        fontWeight: 600,
        color: textTone(color),
        bgcolor: (t) => alpha(color, t.palette.mode === "dark" ? 0.22 : 0.14),
      }}
    >
      {letter}
    </Box>
  );
}
Letter.propTypes = { letter: PropTypes.string, color: PropTypes.string };

function Metric({ value, delta, lowerIsBetter }) {
  const good = delta == null ? null : lowerIsBetter ? delta < 0 : delta > 0;
  return (
    <Stack
      direction="row"
      alignItems="baseline"
      spacing={0.5}
      sx={{ minWidth: 78, justifyContent: "flex-end" }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </Typography>
      {delta != null && delta !== 0 && (
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: good ? "status.success.fg" : "status.danger.fg",
          }}
        >
          {delta > 0 ? "+" : ""}
          {delta}%
        </Typography>
      )}
    </Stack>
  );
}
Metric.propTypes = {
  value: PropTypes.string,
  delta: PropTypes.number,
  lowerIsBetter: PropTypes.bool,
};
