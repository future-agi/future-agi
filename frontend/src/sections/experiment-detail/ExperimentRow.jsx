import {
  Box,
  Button,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { experimentDescription } from "./experimentDescription";
import React, { useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import axios, { endpoints } from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import AuditDrawer from "src/sections/common/Audit/AuditDrawer";
import { useDatasetRuns, useExperimentDetail } from "src/api/develop/runs";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import {
  pollIntervalFor,
  getRunStatus,
  runProgressOf,
} from "src/sections/common/Runs/runStatus";
import { resumeRuns, stopRuns } from "src/api/develop/experiment-run-actions";
import {
  canResumeRun,
  resumeOutcomeOf,
} from "src/sections/common/Runs/runResume";
import HoverCard from "src/sections/common/ux/oneview/HoverCard";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import notify from "src/sections/common/ux/toast";
import { fmtCount, fmtTimeRelative } from "src/sections/common/format/fmt";
import { StatusChip } from "src/sections/common/Runs/RunStatusChip";
import RunPickerPopover from "src/sections/common/Runs/RunPickerPopover";
import VersionChip from "src/sections/common/Datasets/VersionChip";
import SplitChips from "src/sections/common/Datasets/SplitChips";
import { useMatrix, useRunConfigs } from "src/api/develop/r2";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { openRerunWithChanges } from "src/sections/common/RunSources/rerunBus";
import BackButton from "../develop-detail/Common/BackButton";
import { useLiveCounts, withSharedCounts } from "./results/liveCountShare";
import IterateButton, { iterateDoorHref } from "./iterate/IterateButton";
import { iterableFocusOf } from "./iterate/iterateModel";
import {
  headerShipTone,
  headerStatusText,
  nothingRanYet,
} from "./matrix/headerVerdict";
import {
  changedConfigItem,
  failedItemText,
  rerunPlan,
} from "./ExperimentBarRightSection/rerunPlan";
import {
  allItemLabel,
  rerunHereOf,
} from "./ExperimentBarRightSection/rerunHere";
import { configDiff } from "src/sections/common/Runs/configDiff";
import { estimateText } from "src/sections/common/Runs/rerunEstimate";
import DevelopExperimentSelect, {
  experimentStatusOf,
} from "./DevelopExperimentSelect";
import { canRerunHere } from "src/sections/common/Runs/runKind";
import { experimentUrl } from "src/sections/develop-detail/ExperimentTab/leaderboardModel";
import { visibleRuns } from "src/sections/develop-detail/ExperimentTab/attemptsModel";
import {
  RERUN_TOOLTIP,
  rerunAllBody,
  rerunChangedBody,
  rerunFailedBody,
  rerunSomeBody,
  startedRunsOf,
  startedToast,
} from "src/sections/common/Runs/rerunCopyModel";
import { backoffLine } from "src/sections/common/Runs/errorCause";

/** Compare-page URL from an experiment page (PRODUCT-SPEC §3.10.2). */
export function compareUrl({ datasetId, runIds, baselineId, tab, from }) {
  const params = new URLSearchParams({
    runs: runIds.join(","),
    baseline: baselineId,
  });
  if (from) params.set("from", from);
  return `/dashboard/develop/${datasetId}/compare/${tab}?${params.toString()}`;
}

/**
 * Experiment page header (PRODUCT-SPEC §3.10.2): Back · experiment switcher
 * · status · dataset version + splits; right: `Compare with dataset
 * baseline #1A` (when the pinned baseline is in another experiment) ·
 * `Compare with…` · `Rerun ▾` · `Stop` while running.
 */
const ExperimentRow = () => {
  const navigate = useNavigate();
  // One view (A3-02/03), by width only: below 1440 px the version, split
  // and dataset-baseline chips fold into a HoverCard on the experiment name
  // and into ⋯.
  const narrow = useMediaQuery("(max-width:1439.95px)");
  const [moreAnchor, setMoreAnchor] = useState(null);
  const [confirm, setConfirm] = useState(null); // {kind:"stop"|"rerun"}
  const { pathname, search } = useLocation();
  const { experimentId } = useParams();
  const queryClient = useQueryClient();
  const { role } = useAuthContext();
  const roleEdit = !!RolePermission.DATASETS[PERMISSIONS.UPDATE][role];
  // the interface contract (§9 the interface contract transfer, M6): the experiment's audit log.
  const [auditOpen, setAuditOpen] = useState(false);
  // IT-R3 G6 P2: the Rerun menu anchors to a wrapper that never remounts.
  // (The tooltip around the button unmounts it while the menu is open, so a
  // ref on the button pointed at a detached node and the menu opened at the
  // window's top-left corner.)
  const rerunAnchorRef = useRef(null);
  const [rerunOpen, setRerunOpen] = useState(false);
  // IT-R5 G6 P1: `Compare ▾` (to baseline / with…) and the picker it opens.
  const [compareAnchor, setCompareAnchor] = useState(null);
  const compareRef = useRef(null);
  const [pickerSignal, setPickerSignal] = useState(0);
  const tab = pathname.endsWith("/summary")
    ? "summary"
    : pathname.endsWith("/matrix")
      ? "matrix"
      : "data";

  const { data: experiment } = useExperimentDetail(experimentId, {
    refetchInterval: (query) =>
      pollIntervalFor(query.state.data?.data?.result?.runs ?? []),
  });
  const runs = useMemo(() => experiment?.runs ?? [], [experiment]);
  const runIds = runs.map((r) => r.run_id);
  const datasetId = experiment?.dataset_id;
  // the interface contract §9 (M6): Stop, Resume and Rerun need run access on the dataset.
  const access = useDatasetAccess(datasetId);
  const canEdit = roleEdit && access.can.run;
  const { data: datasetRuns } = useDatasetRuns(datasetId);
  const pinnedId = datasetRuns?.pinned_run_id ?? null;
  const pinned = (datasetRuns?.results ?? []).find(
    (r) => r.run_id === pinnedId,
  );
  const showBaselineChip = pinned && !runIds.includes(pinned.run_id);
  const { data: datasets } = useDevelopDatasetList();
  const datasetName =
    (datasets ?? []).find((d) => d.datasetId === datasetId)?.name ?? null;
  const here = `${pathname}${search}`;
  const { kind: datasetKind } = useDatasetKind(datasetId);
  const rowNoun = datasetKind === "task_suite" ? "task" : "row";
  // IT-R3 G6 P2: the menu's numbers come from the runs' own cells (rows that
  // failed a check vs errored, cost, wall time) and their configs (where).
  const { data: matrix } = useMatrix(
    { datasetId, runIds },
    { enabled: Boolean(datasetId) && runIds.length > 0 && canEdit },
  );
  const { data: r1 } = useRunConfigs(datasetId, {
    enabled: Boolean(datasetId) && canEdit,
  });
  // IT-R5 G6 P1 (rule 38): ONE contained purple primary. The page's ship
  // line (same candidate and compare as the Matrix / Summary) decides it:
  // `Don't ship …` → `Test a fix`; otherwise `Rerun`.
  const { data: headlineRuns } = useDatasetRuns(datasetId, {
    include: "metrics,config",
    refetchOnWindowFocus: false,
  });
  const urlBaseline = new URLSearchParams(search).get("baseline");
  const pageBaselineId = runIds.includes(urlBaseline)
    ? urlBaseline
    : pinnedId && runIds.includes(pinnedId)
      ? pinnedId
      : runIds[0] ?? null;
  const shipTone = useMemo(
    () =>
      headerShipTone({
        matrix,
        runIds,
        baselineId: pageBaselineId,
        cand: new URLSearchParams(search).get("cand"),
        runs: headlineRuns?.results ?? [],
        noun: datasetKind === "task_suite" ? "task" : "row",
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      matrix,
      runIds.join(","),
      pageBaselineId,
      search,
      headlineRuns,
      datasetKind,
    ],
  );
  const fixFirst = shipTone === "worse";
  // dataset integration FE-0: Test a fix targets only a run it applies to (a prompt or
  // agent run); TS #3's code and harness runs get no door here either.
  const fixTarget = iterableFocusOf({
    runIds,
    runs: headlineRuns?.results ?? [],
    baselineId: pageBaselineId,
    cand: new URLSearchParams(search).get("cand"),
    urlBaseline,
  });
  const fixTargetLabel =
    runs.find((r) => r.run_id === fixTarget)?.label ??
    (headlineRuns?.results ?? []).find((r) => r.run_id === fixTarget)?.label ??
    null;
  const fixHref = fixTarget
    ? iterateDoorHref({ pathname, search, runId: fixTarget, experimentId })
    : null;
  // dataset integration FE-0 (owner Q12): the menu reruns only the runs Future AGI can
  // start again itself, read with their configs (rerunHere.js).
  const candParam = new URLSearchParams(search).get("cand");
  const rerunHere = useMemo(
    // the interface contract kept runs: a rerun starts from the newest run of each config.
    () =>
      rerunHereOf(
        visibleRuns(runs, ""),
        [r1?.results, headlineRuns?.results],
        candParam,
      ),
    [runs, r1, headlineRuns, candParam],
  );
  const plan = useMemo(
    () => rerunPlan(matrix, rerunHere.here),
    [matrix, rerunHere],
  );
  const failedItem = failedItemText(plan, rowNoun);
  // L2 G9: runs whose config differs from the run they came from.
  const changedItem = useMemo(() => {
    const withConfig = (r) => ({
      ...r,
      config:
        (r1?.results ?? []).find((x) => x.run_id === r.run_id)?.config ??
        r.config,
    });
    const all = (headlineRuns?.results ?? []).map(withConfig);
    // the interface contract: from the newest run of each config, as Re-run all.
    const mine = visibleRuns(runs, "").map((r) => {
      const listed = all.find((x) => x.run_id === r.run_id);
      return withConfig({
        ...r,
        parent_run_id: r.parent_run_id ?? listed?.parent_run_id ?? null,
        // L3 G9 (fix 8): carried rows and the config it last ran fully on.
        iteration: r.iteration ?? listed?.iteration ?? null,
        config: r.config ?? listed?.config,
      });
    });
    return changedConfigItem(mine.filter(canRerunHere), all, (a, b) =>
      configDiff(a, b),
    );
  }, [runs, r1, headlineRuns]);
  const changesRun = rerunHere.changesRun;
  const canRerunAny = rerunHere.here.length > 0;
  const fromParam = new URLSearchParams(search).get("from");
  const status = experimentStatusOf(runs);
  // options-truth L2 finalize: while the Data tab is open its grid's counts
  // win (one number on the page); status words stay this poll's.
  const gridCounts = useLiveCounts((s) =>
    experimentId ? s.byExperiment[experimentId] ?? null : null,
  );
  const countedRuns = useMemo(
    () => withSharedCounts(runs, gridCounts),
    [runs, gridCounts],
  );
  const activeRuns = countedRuns.filter((r) => getRunStatus(r).active);
  const stoppedRuns = countedRuns.filter(
    (r) => getRunStatus(r).key === "Cancelled",
  );
  // options-truth L7b G4: Resume only the stopped runs the ledger allows
  // (never a run its cost cap stopped).
  const resumableRuns = stoppedRuns.filter(canResumeRun);
  const failedRuns = rerunHere.here.filter(
    (r) => (r.progress?.errors ?? 0) > 0,
  );
  const progressOf = (list) =>
    list.reduce(
      (acc, r) => {
        const p = runProgressOf(r);
        return {
          done: acc.done + (p.done ?? 0),
          // L7c g5 (ledger:header-status-pill): rows scored, the count
          // every run chip's bar shows.
          finished: acc.finished + (p.finished ?? p.done ?? 0),
          total: acc.total + (p.total ?? 0),
          left: acc.left + (p.left ?? 0),
        };
      },
      { done: 0, finished: 0, total: 0, left: 0 },
    );
  const activeProgress = progressOf(activeRuns);
  // Rule 36 / S3: the pill reads the runs' own progress.
  const statusText = headerStatusText(status, activeProgress);
  const notRunYet = nothingRanYet(countedRuns, runProgressOf);
  const NOTHING_RAN = "Nothing ran yet · start it from the commands above";
  const stoppedProgress = progressOf(resumableRuns);
  const examples = runProgressOf(runs[0] ?? {}).total ?? 0;
  const costOf = (r) =>
    [r.total_cost, r.cost, r.metrics?.total_cost, r.cost_usd].find((v) =>
      Number.isFinite(v),
    ) ?? null;
  const runCosts = rerunHere.here.map(costOf).filter((v) => v != null);
  const rerunCost = runCosts.length
    ? runCosts.reduce((a, b) => a + b, 0)
    : null;
  // IT-R5 G6 P5: `5 runs × 30 tasks · 450 tries · ~$32.55 · ~11 min · …`
  // from the one estimator (the dialog reads the same per-try numbers).
  const allLine = [
    [
      fmtCount(rerunHere.here.length, "run"),
      examples ? fmtCount(examples, rowNoun) : null,
    ]
      .filter(Boolean)
      .join(" × "),
    estimateText(
      plan.all.estimate?.cost == null && rerunCost
        ? { ...plan.all.estimate, cost: rerunCost }
        : plan.all.estimate,
      rowNoun,
      { rows: false },
    ),
    plan.all.places,
  ]
    .filter(Boolean)
    .join(" · ");
  const stoppedAt = stoppedRuns
    .map((r) => r.stopped_at ?? r.updated_at)
    .filter(Boolean)
    .sort()
    .pop();
  const version = runs[0]?.dataset_version;
  const splits = runs[0]?.splits ?? [];
  const openVersion = () =>
    navigate(
      `/dashboard/develop/${datasetId}?tab=data&version=${version.number}`,
    );
  const openPinnedCompare = () =>
    navigate(
      compareUrl({
        datasetId,
        runIds: [pinned.run_id, ...runIds],
        baselineId: pinned.run_id,
        tab,
        from: here,
      }),
    );
  // What the chips say, for the folded (narrow) header.
  const foldedText = [
    version ? `v${version.number}` : null,
    splits.length
      ? `${splits.length === 1 ? "split" : "splits"} ${splits.map((sp) => sp.name).join(", ")}`
      : null,
    showBaselineChip
      ? `dataset baseline ${pinned.label}${pinned.experiment_ordinal ? ` (#${pinned.experiment_ordinal} ${pinned.experiment_name ?? ""})`.replace(" )", ")") : ""}`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
    queryClient.invalidateQueries({ queryKey: ["develop"] });
  };
  const call = useMutation({
    mutationFn: ({ url, body }) => axios.post(url, body),
    onSuccess: (res, { done, from }) => {
      invalidate();
      // the interface contract kept runs: the new runs the rerun started, with Open.
      const started = startedRunsOf(res);
      if (!started.labels.length)
        return enqueueSnackbar(done, { variant: "success" });
      notify(
        startedToast(
          started.labels,
          (from ?? []).map((r) => r.label),
        ),
        {
          variant: "success",
          key: "exp-rerun",
          action: () => (
            <Button
              size="small"
              color="inherit"
              onClick={() =>
                navigate(
                  experimentUrl(datasetId, experimentId, {
                    runId: started.runIds[0],
                  }),
                )
              }
            >
              Open
            </Button>
          ),
        },
      );
    },
    onError: (e) =>
      enqueueSnackbar(
        getRequestErrorMessage(e, "Could not update the experiment"),
        {
          variant: "error",
        },
      ),
  });

  // dataset integration FE-0: with a run Future AGI cannot start again in the
  // experiment, `All …` reruns exactly the runs it names (rerun-cells
  // `source_ids`, as the leaderboard does); the others keep their results.
  const allLabel = allItemLabel(rerunHere, rowNoun);
  const rerunAll = () =>
    rerunHere.allHere
      ? call.mutate({
          url: endpoints.develop.experiment.rerun,
          body: { experiment_ids: [experimentId] },
          done: `Re-running #${experiment?.ordinal}`,
          from: rerunHere.here,
        })
      : call.mutate({
          url: endpoints.develop.experiment.reRunExperimentCell(experimentId),
          body: { source_ids: rerunHere.here.map((r) => r.run_id) },
          done: `Re-running ${rerunHere.here.map((r) => r.label).join(", ")}`,
          from: rerunHere.here,
        });
  // IT-R3 G6 P2: `Failed rows only` re-runs the runs with rows that failed a
  // check or errored (not only errored runs).
  const failedTargets = plan.failed.runs.length
    ? plan.failed.runs.map((r) => r.run)
    : failedRuns;
  const rerunChanged = () =>
    call.mutate({
      url: endpoints.develop.experiment.reRunExperimentCell(experimentId),
      body: { source_ids: changedItem.runs.map((r) => r.run_id) },
      done: `Re-running ${changedItem.runs.map((r) => r.label).join(", ")} (config changed)`,
      from: changedItem.runs,
    });
  const rerunFailed = () =>
    call.mutate({
      url: endpoints.develop.experiment.reRunExperimentCell(experimentId),
      body: {
        source_ids: failedTargets.map((r) => r.run_id),
        failed_only: true,
      },
      done: `Re-running the failed ${rowNoun}s of ${failedTargets.map((r) => r.label).join(", ")}`,
      from: failedTargets,
    });
  // CR#37: Stop asks by name with counts, then toasts with Undo (= resume).
  const labels = (list) => list.map((r) => r.label).join(", ");
  const resume = async (list = resumableRuns, { quiet } = {}) => {
    try {
      const res = await resumeRuns(
        experimentId,
        list.map((r) => r.run_id),
      );
      invalidate();
      // options-truth L7b G4: the response says which runs resumed; a
      // refused run shows its own message, never `Resumed`.
      const { resumed, refused } = resumeOutcomeOf(res?.data?.result);
      if (refused.length)
        notify(refused.join(" "), { key: "exp-resume", variant: "error" });
      if (!quiet && resumed.length)
        notify(
          `Resumed ${labels(list.filter((r) => resumed.some((x) => x.run_id === r.run_id)))} · ${fmtCount(
            resumed.reduce((n, x) => n + (x.pending ?? 0), 0),
            "example",
          )} left`,
          { key: "exp-resume", variant: "success" },
        );
    } catch (e) {
      notify(getRequestErrorMessage(e, "Could not resume the runs"), {
        key: "exp-resume",
        variant: "error",
      });
    }
  };
  const stop = async () => {
    const list = activeRuns;
    const p = progressOf(list);
    try {
      const res = await stopRuns(
        experimentId,
        list.map((r) => r.run_id),
      );
      invalidate();
      // options-truth L7b G4: the toast reads what the server kept at the
      // moment it stopped (the stop response), not the last poll.
      const stopped = res?.data?.result?.runs ?? [];
      const kept = stopped.length
        ? stopped.reduce((n, r) => n + (r.done ?? 0), 0)
        : p.done;
      const of = stopped.length
        ? stopped.reduce((n, r) => n + (r.total ?? 0), 0) || p.total
        : p.total;
      notify(
        // L7c g5 (action:stop-resume): the ledger's noun (rows / tasks).
        `Stopped ${labels(list)} · ${kept} of ${fmtCount(of, rowNoun)} kept`,
        {
          key: "exp-stop",
          undo: { onUndo: () => resume(list, { quiet: false }) },
        },
      );
    } catch (e) {
      notify(getRequestErrorMessage(e, "Could not stop the runs"), {
        key: "exp-stop",
        variant: "error",
      });
    }
  };

  return (
    // IT-R2 G2 P9: one line that never wraps: a run starting, stopping or
    // a baseline chip never inserts a header row (the page below never
    // jumps). The left side gives way (the experiment name ellipsizes); the
    // actions keep their width.
    <Box
      sx={{
        paddingTop: 1.5,
        paddingX: 1,
        display: "flex",
        justifyContent: "space-between",
        gap: 2,
        alignItems: "center",
        flexWrap: "nowrap",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1.5}
        sx={{ minWidth: 0, flex: "1 1 auto", overflow: "hidden" }}
      >
        {/* IT-R3 G6 P2 (rule 25): Back goes to the parent (the page that
            opened this one, else the dataset's Experiments tab), never
            history -1. */}
        <BackButton
          onBack={() =>
            navigate(
              fromParam && fromParam.startsWith("/")
                ? fromParam
                : `/dashboard/develop/${datasetId}?tab=experiments`,
            )
          }
        />
        {/* L7c g6 (exp:description): the description the user typed shows
            under the experiment title (one line, full text on hover). */}
        <Stack sx={{ minWidth: 0 }}>
          {narrow && foldedText ? (
            <HoverCard
              component="div"
              trigger={
                <DevelopExperimentSelect
                  experiment={experiment}
                  maxWidth={240}
                />
              }
              // The select is the control: hover shows the card, a click opens
              // the select (never pins), Tab reaches the select itself.
              triggerProps={{
                onClick: undefined,
                onKeyDown: undefined,
                tabIndex: -1,
                role: undefined,
                "aria-expanded": undefined,
                "data-testid": "experiment-name-card",
              }}
              triggerSx={{ display: "inline-flex", minWidth: 0 }}
              title={experiment?.name ?? "Experiment"}
              actions={
                <Stack direction="row" gap={0.5} sx={{ flexWrap: "wrap" }}>
                  {version && (
                    <Button size="small" onClick={openVersion}>
                      {`Open dataset v${version.number}`}
                    </Button>
                  )}
                  {showBaselineChip && (
                    <Button size="small" onClick={openPinnedCompare}>
                      {`Compare with ${pinned.label}`}
                    </Button>
                  )}
                </Stack>
              }
            >
              {foldedText}
            </HoverCard>
          ) : (
            <DevelopExperimentSelect
              experiment={experiment}
              maxWidth={narrow ? 240 : 340}
            />
          )}
          {experimentDescription(experiment) ? (
            <Typography
              typography="s3"
              color="text.secondary"
              noWrap
              title={experimentDescription(experiment)}
              data-testid="experiment-description"
              sx={{ pl: 1, maxWidth: narrow ? 300 : 520 }}
            >
              {experimentDescription(experiment)}
            </Typography>
          ) : null}
        </Stack>
        {status && (
          <CustomTooltip
            show={stoppedRuns.length > 0 && !activeRuns.length}
            size="small"
            placement="bottom"
            arrow
            title={`Stopped${stoppedAt ? ` ${fmtTimeRelative(stoppedAt)}` : ""} · ${progressOf(stoppedRuns).done} of ${fmtCount(progressOf(stoppedRuns).total, rowNoun)} kept`}
          >
            <span>
              <StatusChip status={status.key} label={statusText ?? undefined} />
            </span>
          </CustomTooltip>
        )}
        {!narrow && version && (
          <VersionChip version={version} onClick={openVersion} />
        )}
        {!narrow && splits.length > 0 && <SplitChips splits={splits} max={2} />}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ ml: "auto", flexShrink: 0 }}
      >
        {/* IT-R5 G6 P1: one `Compare ▾` (to the dataset baseline, with…)
            instead of two buttons. */}
        {datasetId && (
          <>
            <Box
              component="span"
              ref={compareRef}
              sx={{ display: "inline-flex", flexShrink: 0 }}
            >
              <Button
                variant="outlined"
                color="inherit"
                size="medium"
                startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
                endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
                aria-haspopup="menu"
                aria-expanded={Boolean(compareAnchor) || undefined}
                onClick={(e) => setCompareAnchor(e.currentTarget)}
                data-testid="exp-compare-menu"
                sx={{ height: 32, whiteSpace: "nowrap", typography: "s1" }}
              >
                Compare
              </Button>
            </Box>
            <Menu
              anchorEl={compareAnchor}
              open={Boolean(compareAnchor)}
              onClose={() => setCompareAnchor(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "right" }}
              slotProps={{ paper: { sx: { minWidth: 260, mt: 0.5 } } }}
            >
              {showBaselineChip && (
                <MenuItem
                  onClick={() => {
                    setCompareAnchor(null);
                    openPinnedCompare();
                  }}
                  aria-label={`Compare with dataset baseline ${pinned.label}`}
                  data-testid="exp-compare-baseline"
                >
                  <ListItemIcon>
                    <Iconify icon="mdi:pin-outline" width={18} />
                  </ListItemIcon>
                  <ListItemText
                    primary={`to baseline ${pinned.label}`}
                    secondary={`The dataset baseline (#${pinned.experiment_ordinal} ${pinned.experiment_name ?? ""}), next to these runs`.replace(
                      " )",
                      ")",
                    )}
                    primaryTypographyProps={{ typography: "s1" }}
                    secondaryTypographyProps={{ typography: "s3" }}
                  />
                </MenuItem>
              )}
              <MenuItem
                onClick={() => {
                  setCompareAnchor(null);
                  setPickerSignal((n) => n + 1);
                }}
                data-testid="exp-compare-with"
              >
                <ListItemIcon>
                  <Iconify icon="mdi:plus" width={18} />
                </ListItemIcon>
                <ListItemText
                  primary="with…"
                  secondary="Pick runs from this or other experiments"
                  primaryTypographyProps={{ typography: "s1" }}
                  secondaryTypographyProps={{ typography: "s3" }}
                />
              </MenuItem>
            </Menu>
          </>
        )}
        {datasetId && (
          // IT-R2 G2 P1: `Runs in this experiment` first, then `Other
          // experiments on Support ops tasks` (#3B OpenAI Agents SDK, #3C
          // CrewAI…); the ticked runs open side by side in the Matrix.
          <RunPickerPopover
            datasetId={datasetId}
            excludeIds={[]}
            includeIds={runIds}
            datasetName={datasetName ?? undefined}
            label="Compare with…"
            openSignal={pickerSignal}
            anchorEl={compareRef.current}
            onPick={(picks) => {
              if (picks.length < 2) return;
              const baselineId =
                pinnedId && picks.includes(pinnedId)
                  ? pinnedId
                  : picks.find((id) => runIds.includes(id)) ?? picks[0];
              navigate(
                compareUrl({
                  datasetId,
                  runIds: [
                    baselineId,
                    ...picks.filter((id) => id !== baselineId),
                  ],
                  baselineId,
                  tab: "matrix",
                  from: here,
                }),
              );
            }}
          />
        )}
        {/* the interface contract (§9 transfer, M6): who ran, deleted and re-scored here. */}
        <CustomTooltip show size="small" arrow title="Audit log">
          <IconButton
            aria-label="Audit log"
            onClick={() => setAuditOpen(true)}
            data-testid="exp-audit-log"
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.75,
              width: 38,
              height: 38,
            }}
          >
            <Iconify icon="mdi:history" width={18} />
          </IconButton>
        </CustomTooltip>
        {auditOpen && (
          <AuditDrawer
            open
            onClose={() => setAuditOpen(false)}
            scope={`experiment:${experimentId}`}
            title={experiment?.name ?? undefined}
          />
        )}
        {fixFirst && (
          // Rule 38: the page says Don't ship → `Test a fix` is the one
          // contained purple primary (Rerun is outlined). IterateButton is
          // G3's (outlined); the look is set here until it takes a variant.
          <Box
            component="span"
            sx={{
              display: "inline-flex",
              flexShrink: 0,
              "& [data-testid=iterate-button]:not(.Mui-disabled)": {
                bgcolor: "primary.main",
                color: "primary.contrastText",
                borderColor: "primary.main",
                "&:hover": { bgcolor: "primary.dark" },
              },
            }}
            data-testid="exp-primary-fix"
          >
            <IterateButton
              datasetId={datasetId}
              experimentId={experimentId}
              runIds={runIds}
              baselineId={pageBaselineId}
            />
          </Box>
        )}
        {/* The primary action: Stop while running, Resume when stopped
            (CR#37, where Stop was), else Rerun ▾ (CR#38). */}
        {activeRuns.length > 0 && canEdit ? (
          <CustomTooltip
            show
            size="small"
            arrow
            title={`Stop ${labels(activeRuns)}. Every ${rowNoun} done when you stop is kept; resume later.`}
          >
            <Button
              variant="outlined"
              color="error"
              size="medium"
              startIcon={<Iconify icon="mdi:stop-circle-outline" width={18} />}
              onClick={() => {
                // options-truth L7b G4 (action:stop-resume): the confirm
                // reads the runs' progress now, not the last poll.
                queryClient.invalidateQueries({
                  queryKey: ["experiment", experimentId],
                });
                setConfirm({ kind: "stop" });
              }}
              data-testid="exp-stop"
            >
              Stop
            </Button>
          </CustomTooltip>
        ) : resumableRuns.length > 0 && canEdit ? (
          <CustomTooltip
            show
            size="small"
            arrow
            title={`Resume ${labels(resumableRuns)} where they stopped`}
          >
            <Button
              variant="contained"
              color="primary"
              size="medium"
              startIcon={<Iconify icon="mdi:play-circle-outline" width={18} />}
              onClick={() => resume()}
              data-testid="exp-resume"
            >
              {`Resume ${stoppedProgress.left} left`}
            </Button>
          </CustomTooltip>
        ) : null}
        {/* dataset integration FE-0: absent when no run here can be started again or
            fixed (e.g. TS #3: code and harness runs only). */}
        {canEdit && (fixHref || canRerunAny) && (
          <Box
            component="span"
            ref={rerunAnchorRef}
            sx={{ display: "inline-flex", flexShrink: 0 }}
            data-testid="exp-rerun-anchor"
          >
            <CustomTooltip
              show={!rerunOpen}
              title={notRunYet ? NOTHING_RAN : RERUN_TOOLTIP}
              size="small"
              arrow
            >
              {/* P9: while Stop / Resume holds the primary slot, Rerun is its
                icon (same menu), so the row keeps its width. */}
              {activeRuns.length || resumableRuns.length ? (
                <IconButton
                  aria-label="Rerun"
                  onClick={() => setRerunOpen(true)}
                  disabled={call.isPending}
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 0.75,
                    width: 38,
                    height: 38,
                    color: "primary.main",
                  }}
                >
                  <Iconify icon="mdi:replay" width={18} />
                </IconButton>
              ) : (
                <span style={{ display: "inline-flex" }}>
                  <Button
                    variant={fixFirst ? "outlined" : "contained"}
                    color="primary"
                    size="medium"
                    startIcon={<Iconify icon="mdi:replay" width={18} />}
                    endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
                    onClick={() => setRerunOpen(true)}
                    disabled={call.isPending || notRunYet}
                    aria-label={
                      notRunYet ? `Rerun · ${NOTHING_RAN}` : undefined
                    }
                    data-testid="exp-rerun"
                  >
                    Rerun
                  </Button>
                </span>
              )}
            </CustomTooltip>
          </Box>
        )}
        {narrow && foldedText && (
          <>
            <CustomTooltip show size="small" arrow title="More actions">
              <IconButton
                aria-label="More actions"
                onClick={(e) => setMoreAnchor(e.currentTarget)}
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 0.75,
                  width: 38,
                  height: 38,
                }}
              >
                <Iconify icon="mdi:dots-horizontal" width={18} />
              </IconButton>
            </CustomTooltip>
            <Menu
              anchorEl={moreAnchor}
              open={Boolean(moreAnchor)}
              onClose={() => setMoreAnchor(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "right" }}
              slotProps={{ paper: { sx: { minWidth: 260, mt: 0.5 } } }}
            >
              {showBaselineChip && (
                <MenuItem
                  onClick={() => {
                    setMoreAnchor(null);
                    openPinnedCompare();
                  }}
                >
                  <ListItemIcon>
                    <Iconify icon="mdi:pin-outline" width={18} />
                  </ListItemIcon>
                  <ListItemText
                    primary={`Compare with dataset baseline ${pinned.label}`}
                    primaryTypographyProps={{ typography: "s1" }}
                  />
                </MenuItem>
              )}
              {version && (
                <MenuItem
                  onClick={() => {
                    setMoreAnchor(null);
                    openVersion();
                  }}
                >
                  <ListItemIcon>
                    <Iconify icon="mdi:source-branch" width={18} />
                  </ListItemIcon>
                  <ListItemText
                    primary={`Open dataset v${version.number}`}
                    secondary={
                      splits.length
                        ? `Splits: ${splits.map((sp) => sp.name).join(", ")}`
                        : undefined
                    }
                    primaryTypographyProps={{ typography: "s1" }}
                    secondaryTypographyProps={{ typography: "s3" }}
                  />
                </MenuItem>
              )}
            </Menu>
          </>
        )}
        <Menu
          anchorEl={() => rerunAnchorRef.current}
          open={rerunOpen}
          onClose={() => setRerunOpen(false)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{
            paper: {
              // MenuItem is nowrap by default: with many runs the cost /
              // place line ran past the 420 px edge and was cut off.
              sx: {
                minWidth: 300,
                maxWidth: 420,
                mt: 0.5,
                "& .MuiMenuItem-root": { whiteSpace: "normal" },
              },
              "data-testid": "exp-rerun-menu",
            },
          }}
        >
          {/* dataset integration FE-0 (owner Q12): each item is absent (not disabled)
              when it would act on a run Future AGI cannot start again or
              change itself (code, harness, voice, upload). MUI Menu takes
              no Fragment, so every item carries its own condition. */}
          {fixHref && (
            <MenuItem
              onClick={() => {
                setRerunOpen(false);
                navigate(fixHref);
              }}
              data-testid="exp-rerun-test-fix"
            >
              <ListItemIcon>
                <Iconify icon="mdi:source-branch" width={18} />
              </ListItemIcon>
              <ListItemText
                primary={
                  fixTargetLabel
                    ? `Test a fix on ${fixTargetLabel}`
                    : "Test a fix…"
                }
                secondary="Change the model, settings or tools, rerun some rows, compare"
                primaryTypographyProps={{ typography: "s1" }}
                secondaryTypographyProps={{ typography: "s3" }}
              />
            </MenuItem>
          )}
          {fixHref && canRerunAny && <Divider />}
          {canRerunAny && (
            <MenuItem
              onClick={() => {
                setRerunOpen(false);
                setConfirm({ kind: "rerun" });
              }}
              data-testid="exp-rerun-all"
            >
              <ListItemIcon>
                <Iconify icon="mdi:replay" width={18} />
              </ListItemIcon>
              <ListItemText
                primary={allLabel}
                secondary={allLine}
                primaryTypographyProps={{ typography: "s1" }}
                secondaryTypographyProps={{ typography: "s3" }}
              />
            </MenuItem>
          )}
          {/* IT-R3 G6 P2: disabled items stay readable, greyed, with the
              reason (never struck through). */}
          {canRerunAny && (
            <MenuItem
              disabled={!failedItem.enabled}
              onClick={() => {
                setRerunOpen(false);
                setConfirm({ kind: "failed" });
              }}
              sx={{ "&.Mui-disabled": { opacity: 1 } }}
              data-testid="exp-rerun-failed"
            >
              <ListItemIcon sx={{ opacity: failedItem.enabled ? 1 : 0.5 }}>
                <Iconify icon="mdi:alert-circle-outline" width={18} />
              </ListItemIcon>
              <ListItemText
                primary={failedItem.primary}
                secondary={
                  failedItem.enabled
                    ? [failedItem.secondary, plan.failed.places]
                        .filter(Boolean)
                        .join(" · ")
                    : failedItem.secondary
                }
                primaryTypographyProps={{
                  typography: "s1",
                  color: failedItem.enabled ? "text.primary" : "text.disabled",
                }}
                secondaryTypographyProps={{
                  typography: "s3",
                  color: "text.secondary",
                }}
              />
            </MenuItem>
          )}
          {changesRun && (
            <MenuItem
              onClick={() => {
                setRerunOpen(false);
                openRerunWithChanges({ runId: changesRun.run_id });
              }}
              data-testid="exp-rerun-changes"
            >
              <ListItemIcon>
                <Iconify icon="mdi:restart" width={18} />
              </ListItemIcon>
              <ListItemText
                primary="Rerun with changes…"
                secondary={`Start from ${changesRun.label}${changesRun.name ? ` ${changesRun.name}` : ""}, change a setting, compare`}
                primaryTypographyProps={{ typography: "s1" }}
                secondaryTypographyProps={{ typography: "s3" }}
              />
            </MenuItem>
          )}
          {canRerunAny && <Divider />}
          {canRerunAny && (
            <MenuItem
              disabled={!changedItem.enabled}
              onClick={() => {
                setRerunOpen(false);
                setConfirm({ kind: "changed" });
              }}
              sx={{ "&.Mui-disabled": { opacity: 1 } }}
              data-testid="exp-rerun-changed-configs"
            >
              <ListItemIcon sx={{ opacity: changedItem.enabled ? 1 : 0.5 }}>
                <Iconify icon="mdi:tune-variant" width={18} />
              </ListItemIcon>
              <ListItemText
                primary={changedItem.primary}
                secondary={changedItem.secondary}
                primaryTypographyProps={{
                  typography: "s1",
                  color: changedItem.enabled ? "text.primary" : "text.disabled",
                }}
                secondaryTypographyProps={{
                  typography: "s3",
                  color: "text.secondary",
                }}
              />
            </MenuItem>
          )}
        </Menu>
      </Stack>
      <ConfirmDialog
        open={confirm?.kind === "stop"}
        title={`Stop ${labels(activeRuns)}?`}
        // L7c g5 (action:stop-resume): the count here can be a poll behind
        // the run, so it says `about`; the toast then reads the server's
        // count from the stop response. One noun with the ledger.
        body={`So far about ${activeProgress.done} of ${fmtCount(activeProgress.total, rowNoun)} are done; every ${rowNoun} done when you stop is kept. You can resume the others later.`}
        items={activeRuns.map((r) => `${r.label} ${r.name ?? ""}`.trim())}
        confirmLabel="Stop run"
        cancelLabel="Keep running"
        danger
        onConfirm={() => {
          setConfirm(null);
          stop();
        }}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm?.kind === "failed"}
        title={`Rerun ${fmtCount(plan.failed.rows + plan.failed.errored, `failed ${rowNoun}`)} across ${fmtCount(failedTargets.length, "run")}?`}
        body={[
          rerunFailedBody({ rowNoun, runs: failedTargets }),
          backoffLine(failedTargets),
        ]
          .filter(Boolean)
          .join(" ")}
        items={failedTargets.map((r) => `${r.label} ${r.name ?? ""}`.trim())}
        confirmLabel={`Rerun failed ${rowNoun}s`}
        cancelLabel="Keep results"
        danger={false}
        onConfirm={() => {
          setConfirm(null);
          rerunFailed();
        }}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm?.kind === "changed"}
        title={`Rerun ${fmtCount(changedItem.runs.length, "run")} whose config changed?`}
        body={rerunChangedBody({
          secondary: changedItem.secondary,
          rowNoun,
          runs: changedItem.runs,
        })}
        items={changedItem.runs.map((r) => `${r.label} ${r.name ?? ""}`.trim())}
        confirmLabel="Rerun"
        cancelLabel="Keep results"
        danger={false}
        onConfirm={() => {
          setConfirm(null);
          rerunChanged();
        }}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm?.kind === "rerun"}
        title={`Re-run experiment #${experiment?.ordinal ?? ""}?`}
        body={
          rerunHere.allHere
            ? rerunAllBody({ rowNoun, allLine, runs: rerunHere.here })
            : rerunSomeBody({ rowNoun, allLine, runs: rerunHere.here })
        }
        confirmLabel="Re-run all"
        cancelLabel="Keep results"
        danger={false}
        onConfirm={() => {
          setConfirm(null);
          rerunAll();
        }}
        onClose={() => setConfirm(null)}
      />
    </Box>
  );
};

export default ExperimentRow;
