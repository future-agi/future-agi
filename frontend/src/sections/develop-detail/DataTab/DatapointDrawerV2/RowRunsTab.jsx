import React, { useEffect, useId, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Link,
  Menu,
  MenuItem,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useRowRuns } from "src/api/develop/row-view";
import { useDatasetRuns, useExperimentDetail } from "src/api/develop/runs";
import { surfaceOffered } from "src/sections/common/Collab/collabOffer";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import { useExternalRunHandoff } from "src/sections/common/Runs/useExternalRunHandoff";
import EmptyState from "src/sections/common/ux/EmptyState";
import ErrorState from "src/sections/common/ux/ErrorState";
import Term from "src/sections/common/ux/Term";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import { fmtCount } from "src/sections/common/format/fmt";
import { Pill } from "./RowHeaderPills";
import RowRunsTable from "./RowRunsTable";
import {
  DATASET_RUNS_CATALOG,
  SIDE_BY_SIDE_CHECK_FAILED,
  SIDE_BY_SIDE_MAX,
  defaultSideBySideRuns,
  filterRunsByVerdict,
  orderForSideBySide,
  runsFlags,
  sideBySideBlocked,
  sideBySideCatalogKey,
  sideBySideResultsHref,
  unrecordedExperimentsEmptyText,
  unrecordedExperimentsText,
  verdictSegments,
} from "./runsModel";

const SORT = [
  { value: "newest", label: "Newest" },
  { value: "worst", label: "Worst first" },
  { value: "best", label: "Best first" },
];
const MAX_EVALS = 4;
// Below 760 px the table folds Version, Latency and the links into the Run
// cell and the expanded row; eval columns fill what is left.
const NARROW_PX = 760;
const EVAL_PX = 92;
// URL keys of this tab (F5): the open run, Show (verdict or scope), Sort.
const VIEW_DEFAULTS = { run: "", rshow: "all", rsort: "newest" };
const SCOPES = ["current_version", "baseline_latest"];

/** `[refCallback, width]` of an element that may mount later. */
function useWidth() {
  const [el, setEl] = useState(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((e) => setW(e[0]?.contentRect?.width ?? 0));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w];
}

const MenuSelect = ({ prefix, value, options, onChange }) => {
  const [anchor, setAnchor] = useState(null);
  const current = options.find((o) => o.value === value) ?? options[0];
  return (
    <>
      <Button
        size="small"
        color="inherit"
        onClick={(e) => setAnchor(e.currentTarget)}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        sx={{ typography: "s2", color: "text.secondary", px: 1 }}
      >
        {prefix}:&nbsp;
        <Box component="span" sx={{ color: "text.primary", fontWeight: 500 }}>
          {current.label}
        </Box>
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        {options.map((o) => (
          <MenuItem
            key={o.value}
            dense
            selected={o.value === value}
            onClick={() => {
              setAnchor(null);
              onChange(o.value);
            }}
          >
            {o.label}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
};

MenuSelect.propTypes = {
  prefix: PropTypes.string,
  value: PropTypes.string,
  options: PropTypes.array,
  onChange: PropTypes.func,
};

const linkSx = {
  typography: "inherit",
  fontWeight: "inherit",
  verticalAlign: "baseline",
  color: "primary.main",
  cursor: "pointer",
  textDecoration: "underline",
  textDecorationStyle: "dotted",
  textUnderlineOffset: 3,
  "&:hover": { textDecorationStyle: "solid" },
};

/**
 * RV#21: `Ran in 9 runs · 7 experiments · Passes Correctness in 6/9 · 3
 * failing` — `6/9` shows the passing runs, `3 failing` the failing ones.
 */
const RunsSummary = ({ summary, onShow }) => {
  const fails = summary.primary_scored - summary.primary_pass;
  return (
    <Typography
      typography="s1"
      fontWeight="fontWeightMedium"
      data-testid="row-runs-summary"
    >
      Ran in {fmtCount(summary.runs)}{" "}
      <Term k="run">{summary.runs === 1 ? "run" : "runs"}</Term> ·{" "}
      {fmtCount(summary.experiments)}{" "}
      <Term k="experiment">
        {summary.experiments === 1 ? "experiment" : "experiments"}
      </Term>
      {summary.primary_metric_name && summary.primary_scored ? (
        <>
          {" "}
          · Passes {summary.primary_metric_name} in{" "}
          <CustomTooltip
            show
            size="small"
            placement="bottom"
            arrow
            disableInteractive
            title={`Show the ${summary.primary_pass} runs that pass ${summary.primary_metric_name}`}
          >
            <Link
              component="button"
              onClick={() => onShow("pass")}
              sx={linkSx}
              data-testid="row-runs-pass-link"
            >
              {summary.primary_pass}/{summary.primary_scored}
            </Link>
          </CustomTooltip>
          {fails > 0 && (
            <>
              {" · "}
              <CustomTooltip
                show
                size="small"
                placement="bottom"
                arrow
                disableInteractive
                title={`Show the ${fails} runs that fail ${summary.primary_metric_name}`}
              >
                <Link
                  component="button"
                  onClick={() => onShow("fail")}
                  sx={{ ...linkSx, color: "error.main" }}
                  data-testid="row-runs-fail-link"
                >
                  {fails} failing
                </Link>
              </CustomTooltip>
            </>
          )}
        </>
      ) : null}
    </Typography>
  );
};

RunsSummary.propTypes = {
  summary: PropTypes.object.isRequired,
  onShow: PropTypes.func.isRequired,
};

/** One segment per scored run (pass green, fail red); click opens it. */
const VerdictBar = ({ segments, metricName, openId, onOpen }) => (
  <Stack
    direction="row"
    gap={0.375}
    sx={{ mt: 1, maxWidth: 360 }}
    role="list"
    aria-label={`${metricName} per run`}
    data-testid="row-runs-bar"
  >
    {segments.map((s) => (
      <CustomTooltip
        key={s.runId}
        show
        size="small"
        placement="bottom"
        arrow
        disableInteractive
        title={`${s.label} · ${s.status === "pass" ? "Passes" : "Fails"} ${metricName} · click to open`}
      >
        <ButtonBase
          role="listitem"
          aria-label={`${s.label} ${s.status}`}
          onClick={() => onOpen(s.runId)}
          sx={{
            flex: 1,
            height: 8,
            minWidth: 10,
            borderRadius: 0.5,
            bgcolor: s.status === "pass" ? "green.600" : "red.600",
            opacity: openId && openId !== s.runId ? 0.45 : 1,
            outline: openId === s.runId ? "2px solid" : "none",
            outlineColor: "text.primary",
            outlineOffset: 1,
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
            },
          }}
        />
      </CustomTooltip>
    ))}
  </Stack>
);

VerdictBar.propTypes = {
  segments: PropTypes.array.isRequired,
  metricName: PropTypes.string,
  openId: PropTypes.string,
  onOpen: PropTypes.func.isRequired,
};

/**
 * `Runs across experiments` (PRODUCT-SPEC §3.4, N7): this row in every
 * run on the dataset. Simple: the summary with clickable counts, a pass /
 * fail bar and `Compare these runs side by side`; the table folds to the
 * Run, Output and eval columns. Advanced: tick up to 4 runs for side by
 * side, the full table (sticky Run column), Sort. URL: `run`, `rshow`,
 * `rsort`.
 */
const RowRunsTab = ({
  datasetId,
  rowId,
  expected,
  lastChangedVersion,
  onNewExperiment,
}) => {
  const navigate = useNavigate();
  const compareNoticeId = useId();
  const [view, setView] = useViewUrlState(VIEW_DEFAULTS);
  const scope = SCOPES.includes(view.rshow) ? view.rshow : "all";
  const verdict = ["pass", "fail"].includes(view.rshow) ? view.rshow : "all";
  const sort = SORT.some((s) => s.value === view.rsort) ? view.rsort : "newest";
  const [selected, setSelected] = useState([]);
  const access = useDatasetAccess(datasetId);
  const externalHandoffDisabled =
    access.can.run === false || access.can.download === false;
  const externalHandoff = useExternalRunHandoff({
    datasetId,
    disabled: externalHandoffDisabled,
  });
  const open = view.run || null;
  // Comparison opens the selected runs in their own experiment's Results,
  // or, when they are not all in one experiment, in the dataset's Compare
  // Data.
  const compareOffered = surfaceOffered("exp.compare");
  const q = useRowRuns(datasetId, rowId, { scope, sort });
  const data = q.data;
  const [boxRef, width] = useWidth();
  // A1-25: the panel's width decides (never a mode).
  const narrow = width > 0 && width < NARROW_PX;
  // Fixed columns take ~480 px narrow, ~660 px wide (+40 px for the
  // checkboxes, the ⋯ column included); evals ~92 px each.
  const fixedPx = (narrow ? 480 : 660) + 40;
  const fit = width
    ? Math.max(1, Math.min(MAX_EVALS, Math.floor((width - fixedPx) / EVAL_PX)))
    : MAX_EVALS;
  const metrics = useMemo(
    () => (data?.metrics ?? []).slice(0, fit),
    [data, fit],
  );
  const primary = data?.metrics?.[0] ?? null;
  const allRuns = useMemo(() => data?.runs ?? [], [data]);
  const runs = useMemo(
    () => filterRunsByVerdict(allRuns, primary, verdict),
    [allRuns, primary, verdict],
  );
  useEffect(() => setSelected([]), [rowId]);
  // Runs of one experiment open together only when they share a set-up,
  // which only the experiment's run catalog says (each run's comparison
  // group). Read it only for an experiment a comparison here could open
  // in: the one a run's ⋯ pairs with (the baseline's, else the newest
  // run's) and the ticked runs' own. Until it answers, or when it fails,
  // the doors stay off and say so.
  const anchorRun = allRuns.find((r) => r.is_dataset_baseline) ?? allRuns[0];
  const anchorExperiment = allRuns.some(
    (r) => r !== anchorRun && r.experiment_id === anchorRun.experiment_id,
  )
    ? anchorRun.experiment_id
    : null;
  const tickedRuns = allRuns.filter((r) => selected.includes(r.run_id));
  const tickedExperiment =
    tickedRuns.length > 1 &&
    tickedRuns.every((r) => r.experiment_id === tickedRuns[0].experiment_id)
      ? tickedRuns[0].experiment_id
      : null;
  const catalogRead = { enabled: compareOffered, meta: { errorHandled: true } };
  const anchorCatalog = useExperimentDetail(anchorExperiment, catalogRead);
  const tickedCatalog = useExperimentDetail(tickedExperiment, catalogRead);
  // Runs of other experiments, or of none, open together only when the
  // dataset's run catalog puts them in one comparison group. Read it once
  // this row has runs that are not all in one experiment.
  const acrossExperiments =
    allRuns.length > 1 &&
    allRuns.some(
      (r) => !r.experiment_id || r.experiment_id !== allRuns[0].experiment_id,
    );
  const datasetCatalog = useDatasetRuns(datasetId, {
    ...catalogRead,
    enabled: compareOffered && acrossExperiments,
  });
  const catalogReads = [
    [anchorExperiment, anchorCatalog, "runs"],
    [tickedExperiment, tickedCatalog, "runs"],
    [acrossExperiments && DATASET_RUNS_CATALOG, datasetCatalog, "results"],
  ].filter(([key]) => key);
  // Each catalog read: its runs once answered, else null.
  const catalogs = Object.fromEntries(
    catalogReads.map(([key, read, field]) => [
      key,
      {
        runs: read.data ? read.data[field] ?? [] : null,
        failed: read.isError,
        fetching: read.isFetching,
      },
    ]),
  );
  // Try again: read the catalog that checks these runs once more.
  const retryCatalog = (ids) => {
    const key = sideBySideCatalogKey(ids, allRuns);
    catalogReads.find(([id]) => id === key)?.[1].refetch();
  };

  // RV#20: a `run=` link scrolls its row into view once the table renders.
  useEffect(() => {
    if (!open || !allRuns.length) return;
    const t = setTimeout(() => {
      document
        .querySelector(`[data-run-row="${open}"]`)
        ?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    }, 60);
    return () => clearTimeout(t);
  }, [open, allRuns.length]);

  if (q.isError)
    return (
      <Box sx={{ p: 2 }}>
        <ErrorState
          compact
          thing="the runs of this row"
          error={q.error}
          onRetry={() => q.refetch()}
        />
      </Box>
    );

  if (q.isLoading)
    return (
      <Stack gap={1} sx={{ p: 2 }}>
        <Skeleton width="60%" />
        <Skeleton width={240} height={10} />
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} variant="rounded" height={44} />
        ))}
      </Stack>
    );

  // Experiments with no recorded runs can't be traced to this row: the
  // empty state says they exist, never that the row did not run.
  const unrecordedEmpty = unrecordedExperimentsEmptyText(data?.summary);
  if (!data?.summary?.runs)
    return (
      <EmptyState
        icon="material-symbols:science-outline"
        title={
          unrecordedEmpty
            ? "No recorded run includes this row."
            : "This row hasn't been run in any experiment yet"
        }
        body={
          unrecordedEmpty ??
          "Run an experiment on the dataset to see how each agent answers this row."
        }
        actions={
          onNewExperiment
            ? [
                {
                  label: "New experiment on this row",
                  onClick: onNewExperiment,
                },
              ]
            : []
        }
      />
    );

  const toggle = (id) =>
    setSelected((s) =>
      s.includes(id)
        ? s.filter((x) => x !== id)
        : s.length < SIDE_BY_SIDE_MAX
          ? [...s, id]
          : s,
    );
  const flags = runsFlags(data.summary);
  const unrecorded = unrecordedExperimentsText(data.summary);
  const segments = verdictSegments(allRuns, primary);
  const baselineRun = allRuns.find((r) => r.is_dataset_baseline) ?? null;

  // The set the button opens: the ticks (the baseline joins a single
  // pick), else the baseline + the runs that disagree with it.
  const picks =
    selected.length === 0
      ? defaultSideBySideRuns(allRuns, primary)
      : selected.length === 1 &&
          baselineRun &&
          baselineRun.run_id !== selected[0]
        ? [baselineRun.run_id, selected[0]]
        : selected;
  const ordered = orderForSideBySide(picks, allRuns);
  // Why the picks can't open (other experiments, set-ups that differ, or a
  // catalog that is still being read or could not be read).
  const compareNotice =
    ordered.length > 0 ? sideBySideBlocked(picks, allRuns, catalogs) : null;
  const compareUnavailable = Boolean(compareNotice);
  const menuPicks = (id) =>
    baselineRun && baselineRun.run_id !== id
      ? [baselineRun.run_id, id]
      : [id, ...defaultSideBySideRuns(allRuns, primary, 2)].filter(
          (x, i, a) => a.indexOf(x) === i,
        );
  // A run's ⋯ Side by side: opens, stays off on a row with one run, or says
  // why its runs cannot open together (other experiments or set-ups, or a
  // catalog still being read); `retry` when the catalog could not be read.
  const menuSideBySide = (id) => {
    const ids = menuPicks(id);
    if (ids.length < 2) return "single";
    const note = sideBySideBlocked(ids, allRuns, catalogs);
    if (!note) return "open";
    return note === SIDE_BY_SIDE_CHECK_FAILED ? "retry" : "unavailable";
  };
  const menuSideBySideNote = (id) =>
    sideBySideBlocked(menuPicks(id), allRuns, catalogs);
  const openSideBySide = (ids) => {
    const href = sideBySideResultsHref(ids, allRuns, catalogs, {
      datasetId,
      rowId,
    });
    if (ids.length >= 2 && href) navigate(href);
  };
  const label = (id) => allRuns.find((r) => r.run_id === id)?.label ?? "";
  const compareWhere =
    sideBySideCatalogKey(ordered, allRuns) === DATASET_RUNS_CATALOG
      ? "the dataset's Compare Data"
      : "their experiment's Results";
  const compareTip =
    ordered.length < 2
      ? "Side by side needs at least 2 runs on this row"
      : `Opens ${ordered.map(label).join(", ")} in ${compareWhere}, with ${label(ordered[0])} as the baseline. Tick up to 4 runs to choose.`;
  const showOptions = [
    { value: "all", label: "All runs" },
    ...(primary
      ? [
          { value: "pass", label: `Passing ${primary.name}` },
          { value: "fail", label: `Failing ${primary.name}` },
        ]
      : []),
    { value: "current_version", label: "Runs on current version" },
    { value: "baseline_latest", label: "Baseline + latest" },
  ];

  return (
    <Box ref={boxRef}>
      <Box sx={{ px: 2, pt: 1.5, pb: 1 }}>
        <RunsSummary
          summary={data.summary}
          onShow={(v) => setView({ rshow: v === view.rshow ? "all" : v })}
        />
        {unrecorded && (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ mt: 0.5 }}
            data-testid="row-runs-unrecorded"
          >
            {unrecorded}
          </Typography>
        )}
        {segments.length > 1 && (
          <VerdictBar
            segments={segments}
            metricName={primary?.name}
            openId={open}
            onOpen={(id) => {
              if (verdict !== "all") setView({ rshow: "all", run: id });
              else setView({ run: open === id ? "" : id });
            }}
          />
        )}
        {flags.length > 0 && (
          <Stack direction="row" gap={0.75} sx={{ mt: 1, flexWrap: "wrap" }}>
            {flags.map((f) => (
              <Pill key={f.key} tone={f.tone} tooltip={f.tooltip}>
                {f.label}
              </Pill>
            ))}
          </Stack>
        )}
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ mt: 1, ml: -1, flexWrap: "wrap" }}
        >
          <MenuSelect
            prefix="Show"
            value={view.rshow}
            options={showOptions}
            onChange={(v) => setView({ rshow: v })}
          />
          <MenuSelect
            prefix="Sort"
            value={sort}
            options={SORT}
            onChange={(v) => setView({ rsort: v })}
          />
          <Box sx={{ flex: 1 }} />
          {compareOffered && (
            <CustomTooltip
              show={!compareUnavailable}
              size="small"
              placement="bottom"
              arrow
              disableInteractive
              title={compareTip}
            >
              <span>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={ordered.length < 2 || compareUnavailable}
                  aria-describedby={
                    compareUnavailable ? compareNoticeId : undefined
                  }
                  onClick={() => openSideBySide(picks)}
                  startIcon={
                    <Iconify icon="mdi:compare-horizontal" width={16} />
                  }
                  data-testid="row-runs-side-by-side"
                  sx={{ whiteSpace: "nowrap" }}
                >
                  {`Compare side by side${
                    selected.length ? ` (${selected.length})` : ""
                  }`}
                </Button>
              </span>
            </CustomTooltip>
          )}
        </Stack>
        {compareOffered && compareUnavailable && (
          <Stack
            direction="row"
            alignItems="baseline"
            gap={1}
            sx={{ mt: 0.5, flexWrap: "wrap" }}
          >
            <Typography
              id={compareNoticeId}
              typography="s3"
              color="text.secondary"
            >
              {compareNotice}
            </Typography>
            {compareNotice === SIDE_BY_SIDE_CHECK_FAILED && (
              <Link
                component="button"
                onClick={() => retryCatalog(picks)}
                sx={{ ...linkSx, typography: "s3" }}
              >
                Try again
              </Link>
            )}
          </Stack>
        )}
        {compareOffered && selected.length >= SIDE_BY_SIDE_MAX && (
          <Typography typography="s3" color="text.secondary" sx={{ mt: 0.5 }}>
            Side by side shows up to 4 runs. Untick one to pick another.
          </Typography>
        )}
      </Box>
      <Box
        sx={{
          overflowX: "auto",
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <RowRunsTable
          runs={runs}
          metrics={metrics}
          allMetrics={data.metrics ?? []}
          narrow={narrow}
          selectable={compareOffered}
          stickyRun
          selected={selected}
          maxSelected={SIDE_BY_SIDE_MAX}
          onToggleSelect={toggle}
          open={open}
          onToggleOpen={(id) => setView({ run: open === id ? "" : id })}
          rowId={rowId}
          datasetId={datasetId}
          expected={expected}
          lastChangedVersion={lastChangedVersion}
          baselineId={baselineRun?.run_id ?? null}
          compareOffered={compareOffered}
          onSideBySide={(id) => openSideBySide(menuPicks(id))}
          onSideBySideRetry={(id) => retryCatalog(menuPicks(id))}
          sideBySideState={menuSideBySide}
          sideBySideNote={menuSideBySideNote}
          externalHandoffDisabled={externalHandoffDisabled}
          onExternalHandoff={(run) =>
            externalHandoff.request(
              // The row endpoint exposes the canonical task kind as target_type.
              { ...run, task_kind: run.target_type },
              "selected",
              [run.example_id],
            )
          }
        />
        {runs.length === 0 && (
          <EmptyState
            compact
            title="No runs match this view"
            filterChips={[
              {
                label: showOptions.find((o) => o.value === view.rshow)?.label,
                onDelete: () => setView({ rshow: "all" }),
              },
            ]}
            onClearFilters={() => setView({ rshow: "all" })}
          />
        )}
      </Box>
      {externalHandoff.dialog}
    </Box>
  );
};

RowRunsTab.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  expected: PropTypes.object,
  lastChangedVersion: PropTypes.number,
  onNewExperiment: PropTypes.func,
};

export default RowRunsTab;
