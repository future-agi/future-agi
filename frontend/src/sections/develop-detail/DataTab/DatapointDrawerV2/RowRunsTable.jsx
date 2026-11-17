import React, { Fragment, useId, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import PropTypes from "prop-types";
import MediaValue from "src/sections/common/Media/MediaValue";
import { mediaCellKind } from "src/sections/common/Media/mediaModel";
import {
  Box,
  Checkbox,
  IconButton,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import { copyText } from "src/sections/common/ux/CopyButton";
import { notify } from "src/sections/common/ux/toast";
import {
  fmtScore,
  fmtTimeAbsolute,
  fmtTimeRelative,
} from "src/sections/common/format/fmt";
import RunChip from "src/sections/common/Runs/RunChip";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import EvalChip from "./EvalChip";
import RowRunDetail from "./RowRunDetail";
import RunLinks from "./RunLinks";
import {
  SIDE_BY_SIDE_UNAVAILABLE,
  formatCost,
  formatLatency,
  runEvalVerdict,
  runResultHref,
} from "./runsModel";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import TraceChip from "src/sections/common/Trace/TraceChip";
import { cellTraceChipProps } from "src/sections/common/Trace/traceChipModel";
import { traceChipMenu } from "src/sections/common/Trace/traceChipMenu";
import { runKindOf } from "src/sections/common/Runs/runKind";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import {
  flattenTrace,
  stepTotals,
  traceRoot,
} from "src/sections/experiment-detail/results/drawer/alignSteps";

/**
 * A run's trace on this row (R2-SPEC §2.1): the run's `trace_summary` when
 * the row read carries one, else the totals of its trace. Click opens the
 * result drawer on `dtab=trajectory` in the run's experiment.
 */
const RowRunTraceChip = ({ run, rowId }) => {
  const navigate = useNavigate();
  const [full, setFull] = useState(false);
  const traceId =
    run.trace_id ?? run.trials_detail?.find((t) => t.trace_id)?.trace_id;
  const needsRead = !run.trace_summary && Boolean(traceId);
  const q = useGetTraceDetail(needsRead ? traceId : null);
  const summary = useMemo(() => {
    if (run.trace_summary) return run.trace_summary;
    if (!traceId || !q.data) return [];
    const t = stepTotals(flattenTrace(q.data), traceRoot(q.data));
    return [
      {
        status: run.status === "error" ? "error" : "ok",
        steps: t.steps,
        tool_calls: t.toolCalls,
        llm_calls: t.llmCalls,
        tool_modes: [],
        ...(run.status === "error"
          ? { error: { step: t.steps, text: run.error ?? "" } }
          : {}),
      },
    ];
  }, [run, traceId, q.data]);
  if (needsRead && q.isLoading)
    return <TraceChip state="running" label="Reading trace" compact />;
  const chip = cellTraceChipProps({ trace_summary: summary }, 1, {
    sourceKind: runKindOf(run),
  });
  const openTrajectory = () =>
    navigate(runResultHref(run, rowId, { trajectory: true }));
  return (
    <Box onClick={(e) => e.stopPropagation()} sx={{ mt: 0.5 }}>
      <TraceChip
        {...chip}
        compact
        onOpen={run.experiment_id && rowId ? openTrajectory : undefined}
        menu={[
          ...(run.experiment_id && rowId
            ? [
                {
                  label: "Open trajectory",
                  icon: "mdi:transit-connection-variant",
                  onClick: openTrajectory,
                },
              ]
            : []),
          ...(traceId
            ? [
                {
                  label: "Open full trace ↗",
                  icon: "mdi:open-in-new",
                  onClick: () => setFull(true),
                },
              ]
            : []),
          ...traceChipMenu({ traceId }),
        ]}
      />
      {full && traceId ? (
        <TraceDetailDrawerV2
          traceId={traceId}
          open={full}
          onClose={() => setFull(false)}
        />
      ) : null}
    </Box>
  );
};
RowRunTraceChip.propTypes = {
  run: PropTypes.object.isRequired,
  rowId: PropTypes.string,
};

const HEAD_SX = {
  typography: "s3",
  fontWeight: 600,
  color: "text.secondary",
  whiteSpace: "nowrap",
  py: 0.75,
  px: 1,
  bgcolor: "background.paper",
};
const STICKY_EDGE = (t) => `inset -1px 0 0 ${t.palette.divider}`;
const CELL_SX = { py: 0.75, px: 1, verticalAlign: "top", typography: "s2" };
const DOT = { width: 6, height: 6, borderRadius: "50%", bgcolor: "amber.500" };

const Dot = ({ title }) => (
  <CustomTooltip show size="small" placement="top" arrow title={title}>
    <Box sx={{ ...DOT, flexShrink: 0 }} />
  </CustomTooltip>
);

Dot.propTypes = { title: PropTypes.string };

const ranText = (r) => (r.ran_at ? fmtTimeRelative(r.ran_at) : null);
// A run's dataset version has a number only where versions are numbered;
// without one the cell shows no `v`.
const hasNumber = (r) => Number.isFinite(r.dataset_version?.number);

// Sticky first two columns (Advanced, RV#19): checkbox 40 px, then Run.
const STICKY_BOX = 40;
const stickySx = (left, head) => ({
  position: "sticky",
  left,
  zIndex: head ? 3 : 1,
  bgcolor: "background.paper",
});

/**
 * The per-run ⋯ (MUST 1): Side by side with the baseline, open the result
 * or trajectory, copy the run id. `sideBySideState(runId)`: `open`,
 * `single` (no second run on the row), `unavailable` (its runs are not
 * all in one experiment, their set-ups differ, or that is still being
 * checked: the item stays, off, and says why with `sideBySideNote(runId)`)
 * or `retry` (the check failed: the same, and a Try again item that calls
 * `onSideBySideRetry(runId)`).
 */
const RunRowMenu = ({
  run,
  rowId,
  datasetId,
  isBaseline,
  hasBaseline,
  onSideBySide,
  onSideBySideRetry,
  sideBySideState = () => "open",
  sideBySideNote = () => SIDE_BY_SIDE_UNAVAILABLE,
  compareOffered = true,
  onExternalHandoff,
  externalHandoffDisabled = false,
}) => {
  const navigate = useNavigate();
  const [anchor, setAnchor] = useState(null);
  const noteId = useId();
  const resultHref = runResultHref(run, rowId, { datasetId });
  const close = () => setAnchor(null);
  const compareLabel = isBaseline
    ? "Side by side with the runs that disagree"
    : hasBaseline
      ? "Side by side with baseline"
      : "Side by side with the newest run";
  const compareState = compareOffered ? sideBySideState(run.run_id) : "open";
  const item = (icon, label, onClick, extra = {}) => (
    <MenuItem
      dense
      onClick={() => {
        close();
        onClick();
      }}
      {...extra}
    >
      <ListItemIcon>
        <Iconify icon={icon} width={18} />
      </ListItemIcon>
      <ListItemText primaryTypographyProps={{ typography: "s1" }}>
        {label}
      </ListItemText>
    </MenuItem>
  );
  return (
    <>
      <CustomTooltip
        show
        size="small"
        placement="bottom"
        arrow
        disableInteractive
        title={`More for ${run.label}`}
      >
        <IconButton
          size="small"
          aria-label={`More for ${run.label}`}
          onClick={(e) => {
            e.stopPropagation();
            setAnchor(e.currentTarget);
          }}
          data-testid={`row-run-menu-${run.label}`}
        >
          <Iconify icon="mdi:dots-horizontal" width={18} />
        </IconButton>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {/* Keep the comparison visible when its runs cannot open. */}
        {compareOffered &&
          (compareState === "unavailable" || compareState === "retry" ? (
            <MenuItem
              dense
              disabled
              aria-label={compareLabel}
              aria-describedby={noteId}
              sx={{
                alignItems: "flex-start",
                whiteSpace: "normal",
                maxWidth: 300,
                "&.Mui-disabled": { opacity: 1 },
              }}
            >
              <ListItemIcon sx={{ mt: 0.25, opacity: 0.6 }}>
                <Iconify icon="mdi:compare-horizontal" width={18} />
              </ListItemIcon>
              <ListItemText
                sx={{ my: 0 }}
                primary={compareLabel}
                secondary={
                  sideBySideNote(run.run_id) ?? SIDE_BY_SIDE_UNAVAILABLE
                }
                primaryTypographyProps={{
                  typography: "s1",
                  color: "text.secondary",
                }}
                secondaryTypographyProps={{
                  id: noteId,
                  typography: "s3",
                  color: "text.secondary",
                }}
              />
            </MenuItem>
          ) : (
            item(
              "mdi:compare-horizontal",
              compareLabel,
              () => onSideBySide(run.run_id),
              { disabled: compareState !== "open" },
            )
          ))}
        {/* Reads the check again; the menu stays open to show the answer. */}
        {compareOffered && compareState === "retry" && onSideBySideRetry && (
          <MenuItem dense onClick={() => onSideBySideRetry(run.run_id)}>
            <ListItemIcon>
              <Iconify icon="mdi:refresh" width={18} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s1" }}>
              Try again
            </ListItemText>
          </MenuItem>
        )}
        {resultHref &&
          ["agent", "graph"].includes(run.target_type) &&
          item("mdi:transit-connection-variant", "Open the trajectory", () =>
            navigate(
              runResultHref(run, rowId, { trajectory: true, datasetId }),
            ),
          )}
        {resultHref &&
          item(
            "mdi:open-in-new",
            run.experiment_id
              ? "Open this result in the experiment"
              : "Open this result",
            () => navigate(resultHref),
          )}
        {item("mdi:identifier", "Copy run id", async () => {
          if (await copyText(run.run_id))
            notify(`Copied run id of ${run.label}`, { key: "run-id-copy" });
        })}
        {run.target_type === "external" &&
          onExternalHandoff &&
          item(
            "mdi:code-braces",
            "Run again in your code…",
            () => {
              if (!externalHandoffDisabled && run.example_id)
                onExternalHandoff(run);
            },
            { disabled: externalHandoffDisabled || !run.example_id },
          )}
      </Menu>
    </>
  );
};

RunRowMenu.propTypes = {
  run: PropTypes.object.isRequired,
  rowId: PropTypes.string,
  datasetId: PropTypes.string,
  isBaseline: PropTypes.bool,
  hasBaseline: PropTypes.bool,
  onSideBySide: PropTypes.func.isRequired,
  onSideBySideRetry: PropTypes.func,
  sideBySideState: PropTypes.func,
  sideBySideNote: PropTypes.func,
  compareOffered: PropTypes.bool,
  onExternalHandoff: PropTypes.func,
  externalHandoffDisabled: PropTypes.bool,
};

/**
 * The run table of `Runs across experiments`. Wide (≥ 760 px): Run ·
 * Version · Output · evals · Latency/Cost/Ran · links. Narrow (the 640 px
 * panel): Version and the experiment go under the run chip; latency, cost,
 * age and the links move into the expanded row.
 */
const RowRunsTable = ({
  runs,
  metrics,
  allMetrics,
  narrow,
  selectable = true,
  stickyRun = false,
  selected,
  maxSelected = Infinity,
  onToggleSelect,
  open,
  onToggleOpen,
  rowId,
  datasetId,
  expected,
  lastChangedVersion,
  baselineId = null,
  onSideBySide,
  onSideBySideRetry,
  sideBySideState = () => "open",
  sideBySideNote,
  compareOffered = true,
  onExternalHandoff,
  externalHandoffDisabled = false,
}) => {
  const hidden = allMetrics.length - metrics.length;
  const colSpan =
    (narrow ? 3 : 6) +
    metrics.length +
    (selectable ? 0 : -1) +
    (narrow ? 1 : 0);
  const runLeft = selectable ? STICKY_BOX : 0;
  const staleTitle = `Row changed after this run (v${lastChangedVersion ?? "latest"})`;
  return (
    <Table size="small" stickyHeader>
      <TableHead>
        <TableRow>
          {selectable && (
            <TableCell
              padding="checkbox"
              sx={{ ...HEAD_SX, ...(stickyRun && stickySx(0, true)) }}
            />
          )}
          <TableCell
            sx={{
              ...HEAD_SX,
              ...(stickyRun && stickySx(runLeft, true)),
              ...(stickyRun && { boxShadow: STICKY_EDGE }),
            }}
          >
            Run
          </TableCell>
          {!narrow && <TableCell sx={HEAD_SX}>Version</TableCell>}
          <TableCell sx={{ ...HEAD_SX, minWidth: 140 }}>Output</TableCell>
          {metrics.map((m, i) => (
            <TableCell key={m.key} sx={HEAD_SX}>
              {m.name}
              {narrow && hidden > 0 && i === metrics.length - 1 && (
                <CustomTooltip
                  show
                  size="small"
                  placement="top"
                  arrow
                  title={`${allMetrics
                    .slice(metrics.length)
                    .map((x) => x.name)
                    .join(", ")} · expand a run or widen the panel`}
                >
                  <Box
                    component="span"
                    sx={{ color: "primary.main", ml: 0.75 }}
                  >
                    +{hidden}
                  </Box>
                </CustomTooltip>
              )}
            </TableCell>
          ))}
          {!narrow && (
            <TableCell sx={{ ...HEAD_SX, textAlign: "right" }}>
              {hidden > 0 && (
                <CustomTooltip
                  show
                  size="small"
                  placement="top"
                  arrow
                  title={`${allMetrics
                    .slice(metrics.length)
                    .map((x) => x.name)
                    .join(", ")} · expand a run`}
                >
                  <Box component="span" sx={{ color: "primary.main", mr: 1 }}>
                    +{hidden} evals
                  </Box>
                </CustomTooltip>
              )}
              Latency
            </TableCell>
          )}
          <TableCell sx={HEAD_SX} />
        </TableRow>
      </TableHead>
      <TableBody>
        {runs.map((r) => {
          const isOpen = open === r.run_id;
          const stale = Boolean(r.row_changed_after);
          return (
            <Fragment key={r.run_id}>
              <TableRow
                hover
                selected={isOpen}
                data-run-row={r.run_id}
                onClick={() => onToggleOpen(r.run_id)}
                sx={{
                  cursor: "pointer",
                  "& td": { borderBottomColor: "divider" },
                }}
                data-testid={`row-run-${r.label}`}
              >
                {selectable && (
                  <TableCell
                    padding="checkbox"
                    sx={{
                      verticalAlign: "top",
                      ...(stickyRun && stickySx(0)),
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <CustomTooltip
                      show={
                        !selected.includes(r.run_id) &&
                        selected.length >= maxSelected
                      }
                      size="small"
                      placement="right"
                      arrow
                      disableInteractive
                      title={`Side by side shows up to ${maxSelected} runs · untick one first`}
                    >
                      <span>
                        <Checkbox
                          size="small"
                          checked={selected.includes(r.run_id)}
                          disabled={
                            !selected.includes(r.run_id) &&
                            selected.length >= maxSelected
                          }
                          onChange={() => onToggleSelect(r.run_id)}
                          inputProps={{
                            "aria-label": `Select ${r.label} for side by side`,
                          }}
                        />
                      </span>
                    </CustomTooltip>
                  </TableCell>
                )}
                <TableCell
                  sx={{
                    ...CELL_SX,
                    minWidth: 140,
                    maxWidth: 210,
                    ...(stickyRun && stickySx(runLeft)),
                    ...(stickyRun && { boxShadow: STICKY_EDGE }),
                    ...(stickyRun && isOpen && { bgcolor: "action.selected" }),
                  }}
                >
                  <RunChip
                    run={r}
                    isBaseline={r.is_dataset_baseline}
                    isPinned={r.is_dataset_baseline}
                    showStatus={false}
                  />
                  <Stack
                    direction="row"
                    alignItems="center"
                    gap={0.5}
                    sx={{ mt: 0.375, minWidth: 0 }}
                  >
                    {narrow && hasNumber(r) && (
                      <VersionNumber number={r.dataset_version.number} />
                    )}
                    {narrow && stale && <Dot title={staleTitle} />}
                    <Typography
                      typography="s3"
                      color="text.disabled"
                      noWrap
                      title={r.experiment_name}
                    >
                      {r.experiment_name}
                    </Typography>
                  </Stack>
                </TableCell>
                {!narrow && (
                  <TableCell sx={CELL_SX}>
                    <Stack direction="row" alignItems="center" gap={0.5}>
                      {hasNumber(r) && (
                        <VersionNumber number={r.dataset_version.number} />
                      )}
                      {stale && <Dot title={staleTitle} />}
                    </Stack>
                  </TableCell>
                )}
                <TableCell sx={CELL_SX}>
                  {r.status !== "error" && mediaCellKind(r.output) ? (
                    // Call / media outputs as their chip.
                    <Box sx={{ height: 28, display: "flex", minWidth: 0 }}>
                      <MediaValue value={r.output} size="cell" />
                    </Box>
                  ) : (
                    <Typography
                      typography="s2"
                      color={
                        r.status === "error" ? "error.main" : "text.primary"
                      }
                      sx={{
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                      }}
                    >
                      {r.status === "error"
                        ? r.error ?? "Errored"
                        : typeof r.output === "string" || r.output == null
                          ? r.output
                          : JSON.stringify(r.output)}
                    </Typography>
                  )}
                  <RowRunTraceChip run={r} rowId={rowId} />
                </TableCell>
                {metrics.map((m) => {
                  const ev = r.evals?.[m.key];
                  if (!ev)
                    return (
                      <TableCell key={m.key} sx={CELL_SX}>
                        <Typography typography="s3" color="text.disabled">
                          Not scored
                        </Typography>
                      </TableCell>
                    );
                  return (
                    <TableCell key={m.key} sx={CELL_SX}>
                      <CustomTooltip
                        show={r.trials > 1 || ev.flaky}
                        size="small"
                        placement="bottom"
                        arrow
                        disableInteractive
                        title={`${r.trials} trials · σ ${fmtScore(ev.std ?? 0)}${ev.flaky ? " · trials disagree" : ""}`}
                      >
                        <Stack direction="row" alignItems="center" gap={0.5}>
                          <EvalChip verdict={runEvalVerdict(ev, m, r.trials)} />
                          {ev.flaky && <Box sx={DOT} />}
                        </Stack>
                      </CustomTooltip>
                    </TableCell>
                  );
                })}
                {!narrow && (
                  <TableCell
                    sx={{
                      ...CELL_SX,
                      textAlign: "right",
                      whiteSpace: "nowrap",
                    }}
                  >
                    <Typography
                      typography="s2"
                      sx={{ fontVariantNumeric: "tabular-nums" }}
                    >
                      {formatLatency(r.latency_ms)}
                    </Typography>
                    <Typography
                      typography="s3"
                      color="text.disabled"
                      sx={{ fontVariantNumeric: "tabular-nums" }}
                      title={
                        r.ran_at
                          ? `Ran ${fmtTimeAbsolute(r.ran_at)}`
                          : undefined
                      }
                    >
                      {formatCost(r.cost)}
                      {ranText(r) ? ` · ${ranText(r)}` : ""}
                    </Typography>
                  </TableCell>
                )}
                <TableCell
                  sx={{ ...CELL_SX, whiteSpace: "nowrap", px: 0.5 }}
                  onClick={(e) => e.stopPropagation()}
                >
                  {!narrow && (
                    <RunLinks run={r} rowId={rowId} datasetId={datasetId} />
                  )}
                  {onSideBySide && (
                    <RunRowMenu
                      run={r}
                      rowId={rowId}
                      datasetId={datasetId}
                      isBaseline={r.run_id === baselineId}
                      hasBaseline={Boolean(baselineId)}
                      onSideBySide={onSideBySide}
                      onSideBySideRetry={onSideBySideRetry}
                      sideBySideState={sideBySideState}
                      sideBySideNote={sideBySideNote}
                      compareOffered={compareOffered}
                      onExternalHandoff={onExternalHandoff}
                      externalHandoffDisabled={externalHandoffDisabled}
                    />
                  )}
                </TableCell>
              </TableRow>
              {isOpen && (
                <TableRow>
                  <TableCell colSpan={colSpan} sx={{ p: 0 }}>
                    <RowRunDetail
                      run={r}
                      rowId={rowId}
                      datasetId={datasetId}
                      metrics={allMetrics}
                      expected={expected}
                    />
                  </TableCell>
                </TableRow>
              )}
            </Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
};

RowRunsTable.propTypes = {
  runs: PropTypes.array.isRequired,
  metrics: PropTypes.array.isRequired,
  allMetrics: PropTypes.array.isRequired,
  narrow: PropTypes.bool,
  selectable: PropTypes.bool,
  stickyRun: PropTypes.bool,
  selected: PropTypes.array.isRequired,
  maxSelected: PropTypes.number,
  onToggleSelect: PropTypes.func.isRequired,
  baselineId: PropTypes.string,
  onSideBySide: PropTypes.func,
  onSideBySideRetry: PropTypes.func,
  sideBySideState: PropTypes.func,
  sideBySideNote: PropTypes.func,
  compareOffered: PropTypes.bool,
  onExternalHandoff: PropTypes.func,
  externalHandoffDisabled: PropTypes.bool,
  open: PropTypes.string,
  onToggleOpen: PropTypes.func.isRequired,
  rowId: PropTypes.string.isRequired,
  datasetId: PropTypes.string,
  expected: PropTypes.object,
  lastChangedVersion: PropTypes.number,
};

export default RowRunsTable;
