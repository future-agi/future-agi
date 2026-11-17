import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Button,
  Link,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  formatDelta,
  formatValue,
} from "src/sections/common/Metrics/metricFormat";
import AnalysisCard from "src/sections/common/Analysis/AnalysisCard";
import EmptyState from "src/sections/common/ux/EmptyState";
import Truncate from "src/sections/common/ux/Truncate";
import CopyButton from "src/sections/common/ux/CopyButton";
import {
  EMPTY,
  fmtCount,
  fmtPts,
  NUM_SX,
} from "src/sections/common/format/fmt";
import { VOCAB } from "src/sections/common/vocab";
import { aggOf } from "src/sections/common/Analysis/MetricCard";
import {
  CustomTab,
  CustomTabs,
  TabWrapper,
} from "src/sections/develop-detail/SummaryTab/SummaryStyle";
import CandidatePicker from "./CandidatePicker";

const tabular = NUM_SX;
const PAGE = 10;

/** A row's change: `Pass → Fail` reads as a red ↓, scores as a signed delta. */
function DeltaText({ metric, delta }) {
  const hib = metric.higher_is_better !== false;
  const better = hib ? delta > 0 : delta < 0;
  const color =
    Math.abs(delta) < 1e-9
      ? "text.secondary"
      : better
        ? "green.600"
        : "red.600";
  const flat = Math.abs(delta) < 1e-9;
  const text =
    metric.output_type === "pass_fail"
      ? flat
        ? "same"
        : fmtPts(delta, { fraction: true })
      : formatDelta(metric, delta);
  return (
    <Box component="span" sx={{ color }}>
      {flat ? "" : delta > 0 ? "▲ " : "▼ "}
      {text}
    </Box>
  );
}
DeltaText.propTypes = { metric: PropTypes.object, delta: PropTypes.number };
/** What each tab counts (so its number reconciles with the verdict). */
const TAB_HELP = {
  worse: (c, b) =>
    `Tasks where ${c} passes fewer ${VOCAB.TRIALS} than ${b}: the verdict's count, biggest drop first.`,
  regressed: (c, b) =>
    `Tasks where an eval score of ${c} moved down vs ${b} beyond noise (any ${VOCAB.TRIAL}). Not the same as Got worse, which counts ${VOCAB.TRIALS} passed.`,
  improved: (c, b) =>
    `Tasks where an eval score of ${c} moved up vs ${b} beyond noise.`,
  changed: () => "Tasks that got better or worse on any eval.",
  failing: (c) => `Tasks where ${c} fails an eval.`,
  flaky: (c) =>
    `Tasks where ${c}'s ${VOCAB.TRIALS} disagree: some passed, some failed.`,
};

// IT-R1 G2 P1: two named counts. `Got worse` = the verdict's `worse`
// (Task success dropped); `Any eval worse` = any eval moved down (was
// `Regressed`, which read as the same number).
const TAB_LABEL = {
  worse: "Got worse",
  regressed: "Any eval worse",
  improved: "Any eval better",
  changed: "Changed",
  failing: "Failing",
  flaky: "Flaky",
};

/**
 * L2 G2 R4 (consistency #6): `Any eval worse` means one thing — any eval
 * moved (the Data tab's `on any eval N`). When the list reads ONE metric
 * (`Metric: Task success`), its tabs name it: `Task success worse (2)`.
 */
export function tabLabelOf(k, metricName) {
  if (metricName) {
    if (k === "regressed") return `${metricName} worse`;
    if (k === "improved") return `${metricName} better`;
    if (k === "changed") return `Changed on ${metricName}`;
  }
  return TAB_LABEL[k] ?? k;
}

/** `2/3 trials` (VOCAB) for `{pass, n}`; `—` without tries. */
function triesText(t) {
  if (!t || !(t.n > 0)) return EMPTY;
  return `${t.pass}/${t.n} ${t.n === 1 ? VOCAB.TRIAL : VOCAB.TRIALS}`;
}

/**
 * WorklistTable — the rows to look at next (PRODUCT-SPEC §3.13 M8.4): tabs
 * Regressed / Improved / Changed / Flaky (single run: Failing / Flaky) with
 * counts; in the header the candidate (`#2D ▾`, the run the page is about)
 * and `Metric: All evals ▾`; 10 rows, then `Show more (n)` · `See all n in
 * the grid ↗`; footer `Open all n in grid` · `Review these n` · `Add n rows
 * to dataset…`.
 */
const WorklistTable = ({
  analysis,
  colors,
  metricKey,
  onMetric,
  links,
  onReview,
  tab: tabProp,
  onTab,
  sideBySideHref,
  candidate,
}) => {
  const worklist = analysis.worklist;
  const tabKeys = Object.keys(worklist?.tabs ?? {});
  // IT-R3 G6 P4: open on the first tab with rows (never an empty
  // `Got worse (0)` while `Flaky (6)` has content).
  const firstFull =
    tabKeys.find((k) => (worklist.tabs[k]?.total ?? 0) > 0) ?? tabKeys[0];
  const [tabState, setTabState] = useState(firstFull ?? "regressed");
  // A tab the user picked (URL `wtab`) wins, even when empty.
  const tab = tabProp ?? tabState;
  const setTab = (v) => (onTab ? onTab(v) : setTabState(v));
  const [limitState, setLimit] = useState(null);
  const limit = limitState ?? PAGE;
  const current = worklist?.tabs?.[tab] ?? worklist?.tabs?.[tabKeys[0]];
  const activeTab = worklist?.tabs?.[tab] ? tab : tabKeys[0];
  const metricsByKey = useMemo(
    () => Object.fromEntries(analysis.metrics.map((m) => [m.key, m])),
    [analysis.metrics],
  );
  const evalMetrics = analysis.metrics.filter((m) => m.family === "eval");
  const runById = Object.fromEntries(analysis.runs.map((r) => [r.run_id, r]));
  const cand = runById[worklist?.run_id];
  const base = runById[analysis.baseline_run_id];
  const metric = metricsByKey[metricKey];
  // The metric the list's movement tabs count (null = every eval).
  const worklistMetricName = worklist?.metric
    ? metricsByKey[worklist.metric]?.name ?? null
    : null;
  if (!worklist || !cand) return null;

  const items = current?.items ?? [];
  const total = current?.total ?? items.length;
  const multiCandidate = (candidate?.items?.length ?? 0) > 1;
  // `vs #2B · 14 rows` (no "top N": the list shows 10, then Show more)
  const countText = `${base ? `vs ${base.label} · ` : "· "}${fmtCount(total, "row")}`;
  const gridHref = links.tab(current, activeTab);
  const ids = items.map((i) => i.example_id);

  // Pass/Fail over k trials reads `2/3 trials` (not `66.7%` next to `Pass`).
  const tries = Math.max(cand?.trials ?? 1, base?.trials ?? 1);
  const valueText = (k, v) => {
    if (v == null) return EMPTY;
    const def = metricsByKey[k];
    if (def?.output_type === "pass_fail" && tries > 1)
      return `${Math.round(v * tries)}/${tries} ${VOCAB.TRIALS}`;
    return formatValue(def, v, { agg: "value" });
  };
  // Got worse is counted on trials passed (the Matrix rule), so its rows
  // show those trials, never an eval that reads `same`.
  const isWorseTab = activeTab === "worse";
  const showValues = isWorseTab || Boolean(metric);

  const changeList = (item) => {
    const moved =
      activeTab === "improved"
        ? item.improved
        : activeTab === "failing"
          ? item.failing
          : item.regressed;
    const list =
      activeTab === "changed" ? [...item.regressed, ...item.improved] : moved;
    return list ?? [];
  };

  return (
    <AnalysisCard
      id="analysis-worklist"
      icon="mdi:format-list-checks"
      title="Tasks to review"
      titleAdornment={
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ minWidth: 0 }}
        >
          {multiCandidate ? (
            <CandidatePicker
              compact
              items={candidate.items}
              value={cand.run_id}
              colors={colors}
              onChange={(id) => {
                setLimit(null);
                candidate.onChange?.(id);
              }}
            />
          ) : null}
          <Typography typography="s2" color="text.secondary" noWrap>
            {multiCandidate ? countText : `${cand.label} ${countText}`}
          </Typography>
        </Stack>
      }
      actions={
        <Select
          size="small"
          value={metricKey ?? "all"}
          onChange={(e) => {
            setLimit(null);
            onMetric(e.target.value);
          }}
          inputProps={{ "aria-label": "Metric" }}
          renderValue={(v) =>
            `Metric: ${v === "all" ? "All evals" : metricsByKey[v]?.name ?? v}`
          }
          sx={{
            height: 28,
            typography: "s2",
            color: "text.secondary",
            maxWidth: 260,
            "& .MuiSelect-select": { py: 0.25, pl: 1 },
          }}
        >
          <MenuItem value="all" sx={{ typography: "s2" }}>
            All evals
          </MenuItem>
          {evalMetrics.map((m) => (
            <MenuItem key={m.key} value={m.key} sx={{ typography: "s2" }}>
              {m.name}
            </MenuItem>
          ))}
        </Select>
      }
      dense
      footer={
        total > 0 ? (
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
            <Button
              size="small"
              variant="outlined"
              component={RouterLink}
              to={gridHref}
              startIcon={<Iconify icon="mdi:table" width={16} />}
            >
              {total === 1
                ? "Open it in the grid"
                : `Open all ${total} in grid`}
            </Button>
            {base && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => onReview(ids)}
                startIcon={
                  <Iconify icon="mdi:account-check-outline" width={16} />
                }
              >
                {total === 1 ? "Review this task" : `Review these ${total}`}
              </Button>
            )}
            <Button
              size="small"
              variant="text"
              component={RouterLink}
              to={links.addToDataset(current, activeTab)}
              startIcon={
                <Iconify icon="mdi:database-plus-outline" width={16} />
              }
            >
              Add {fmtCount(total, "row")} to dataset…
            </Button>
          </Stack>
        ) : null
      }
    >
      <Box sx={{ px: 1.5, pb: 1 }}>
        <TabWrapper sx={{ mb: 0 }}>
          <CustomTabs
            value={activeTab}
            onChange={(e, v) => {
              setTab(v);
              setLimit(null);
            }}
            TabIndicatorProps={{ style: { display: "none" } }}
          >
            {tabKeys.map((k) => (
              <CustomTab
                key={k}
                value={k}
                label={
                  <CustomTooltip
                    show
                    size="small"
                    placement="top"
                    title={
                      TAB_HELP[k]?.(
                        cand.label,
                        base?.label ?? "the baseline",
                      ) ?? ""
                    }
                  >
                    <span>{`${tabLabelOf(k, worklistMetricName)} (${worklist.tabs[k].total})`}</span>
                  </CustomTooltip>
                }
              />
            ))}
          </CustomTabs>
        </TabWrapper>
        {TAB_HELP[activeTab] ? (
          <Typography
            typography="s3"
            color="text.secondary"
            component="p"
            sx={{ mt: 0.75 }}
            data-testid="worklist-tab-help"
          >
            {TAB_HELP[activeTab](cand.label, base?.label ?? "the baseline")}
          </Typography>
        ) : null}
      </Box>
      {items.length === 0 ? (
        <EmptyState
          compact
          icon={activeTab === "flaky" ? "mdi:sine-wave" : "mdi:check-all"}
          title={
            activeTab === "flaky"
              ? `No flaky tasks on ${cand.label}`
              : `No ${TAB_LABEL[activeTab]?.toLowerCase()} tasks${metric ? ` on ${metric.name}` : ""}`
          }
          body={
            activeTab === "flaky"
              ? (cand.trials ?? 1) > 1
                ? "Every task gave the same result on every try."
                : "This run tried each task once. Run 3 trials per task to see which results are unstable."
              : activeTab === "regressed"
                ? `Nothing got worse than on ${base?.label ?? "the baseline"}.`
                : "Pick another tab or metric."
          }
          sx={{ pb: 1.5 }}
        />
      ) : (
        <Table
          size="small"
          sx={{
            "& td, & th": {
              typography: "s2",
              borderColor: "divider",
              py: 0.75,
            },
            "& th": {
              fontWeight: 600,
              color: "text.secondary",
              bgcolor: "background.neutral",
            },
          }}
        >
          <TableHead>
            <TableRow>
              <TableCell sx={{ pl: 1.5, width: 56 }}>Task</TableCell>
              <TableCell>Input</TableCell>
              <TableCell>Split</TableCell>
              <TableCell>{isWorseTab ? "Why it is here" : "Metrics"}</TableCell>
              {showValues && base && (
                <TableCell align="right">{base.label}</TableCell>
              )}
              {showValues && <TableCell align="right">{cand.label}</TableCell>}
              {showValues && base && <TableCell align="right">Δ</TableCell>}
              <TableCell align="center">
                <CustomTooltip
                  show
                  size="small"
                  placement="top"
                  title={`A dot marks a task whose ${VOCAB.TRIALS} disagree: some passed, some failed.`}
                >
                  <span>Flaky</span>
                </CustomTooltip>
              </TableCell>
              <TableCell align="right" sx={{ pr: 1.5 }}>
                Actions
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {items.slice(0, limit).map((item) => {
              const v = metric ? item.values?.[metric.key] : null;
              const changes = changeList(item);
              return (
                <TableRow
                  key={item.example_id}
                  hover
                  sx={{
                    "& .row-hover": { opacity: 0 },
                    "&:hover .row-hover, &:focus-within .row-hover": {
                      opacity: 1,
                    },
                  }}
                >
                  <TableCell
                    sx={{ pl: 1.5, ...tabular, color: "text.secondary" }}
                  >
                    {item.row_order}
                  </TableCell>
                  <TableCell sx={{ maxWidth: 360 }}>
                    <Truncate
                      text={item.input_preview || "Empty input"}
                      lines={2}
                    />
                  </TableCell>
                  <TableCell
                    sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
                  >
                    {item.splits.length ? item.splits.join(", ") : "No split"}
                  </TableCell>
                  <TableCell>
                    <Typography typography="s2" color="text.secondary">
                      {item.status === "error"
                        ? `Errored: ${item.error ?? "every trial failed"}`
                        : isWorseTab && item.tries
                          ? `Fewer ${VOCAB.TRIALS} passed`
                          : changes.length
                            ? changes
                                .map((c) => metricsByKey[c.metric]?.name)
                                .join(", ")
                            : EMPTY}
                    </Typography>
                  </TableCell>
                  {isWorseTab && base && (
                    <TableCell align="right" sx={tabular}>
                      {triesText(item.tries?.baseline)}
                    </TableCell>
                  )}
                  {isWorseTab && (
                    <TableCell align="right" sx={tabular}>
                      {triesText(item.tries?.candidate)}
                    </TableCell>
                  )}
                  {isWorseTab && base && (
                    <TableCell
                      align="right"
                      sx={{ ...tabular, fontWeight: 600, color: "red.600" }}
                    >
                      {item.tries?.baseline?.n && item.tries?.candidate?.n
                        ? `▼ ${fmtPts(
                            item.tries.candidate.pass / item.tries.candidate.n -
                              item.tries.baseline.pass / item.tries.baseline.n,
                            { fraction: true },
                          )}`
                        : EMPTY}
                    </TableCell>
                  )}
                  {!isWorseTab && metric && base && (
                    <TableCell align="right" sx={tabular}>
                      {valueText(metric.key, v?.baseline)}
                    </TableCell>
                  )}
                  {!isWorseTab && metric && (
                    <TableCell align="right" sx={tabular}>
                      {valueText(metric.key, v?.candidate)}
                    </TableCell>
                  )}
                  {!isWorseTab && metric && base && (
                    <TableCell
                      align="right"
                      sx={{ ...tabular, fontWeight: 600 }}
                    >
                      {v?.baseline != null && v?.candidate != null ? (
                        <DeltaText
                          metric={metric}
                          delta={v.candidate - v.baseline}
                        />
                      ) : (
                        EMPTY
                      )}
                    </TableCell>
                  )}
                  <TableCell align="center">
                    {item.flaky ? (
                      <CustomTooltip
                        show
                        size="small"
                        placement="top"
                        arrow
                        title={`Flaky on ${cand.label}: some ${VOCAB.TRIALS} passed, some failed`}
                      >
                        <Box
                          component="span"
                          // L2 G2 (a11y #9a): the tooltip's label needs a role.
                          role="img"
                          sx={{
                            display: "inline-block",
                            width: 8,
                            height: 8,
                            borderRadius: "50%",
                            bgcolor: "warning.main",
                          }}
                        />
                      </CustomTooltip>
                    ) : null}
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{ pr: 1.5, whiteSpace: "nowrap" }}
                  >
                    <Stack
                      direction="row"
                      gap={1}
                      alignItems="center"
                      justifyContent="flex-end"
                    >
                      <Box className="row-hover">
                        <CopyButton
                          value={item.example_id}
                          tooltip={`Copy row id ${item.example_id}`}
                          iconSize={14}
                        />
                      </Box>
                      {sideBySideHref && base && (
                        <CustomTooltip
                          show
                          size="small"
                          placement="top"
                          title={`Open task ${item.row_order} with ${base.label} and ${cand.label} side by side`}
                        >
                          <Link
                            component={RouterLink}
                            to={sideBySideHref(item.example_id, cand.run_id)}
                            underline="hover"
                            sx={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 0.25,
                            }}
                          >
                            <Iconify icon="mdi:compare-horizontal" width={14} />
                            Side by side
                          </Link>
                        </CustomTooltip>
                      )}
                      <Link
                        component={RouterLink}
                        to={links.row(item.example_id, current)}
                        underline="hover"
                        color="text.secondary"
                      >
                        Open
                      </Link>
                    </Stack>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      {items.length > limit && (
        <Stack
          direction="row"
          alignItems="center"
          gap={1.5}
          sx={{ px: 1.5, py: 0.75 }}
        >
          <Button
            size="small"
            onClick={() => setLimit((l) => (l ?? PAGE) + PAGE * 2)}
          >
            Show more ({items.length - limit})
          </Button>
          <Link
            component={RouterLink}
            to={gridHref}
            underline="hover"
            typography="s2"
            sx={{ display: "inline-flex", alignItems: "center", gap: 0.25 }}
          >
            See all {total} in the grid
            <Iconify icon="mdi:arrow-top-right" width={14} />
          </Link>
        </Stack>
      )}
    </AnalysisCard>
  );
};

WorklistTable.propTypes = {
  analysis: PropTypes.object.isRequired,
  colors: PropTypes.object,
  metricKey: PropTypes.string,
  onMetric: PropTypes.func.isRequired,
  links: PropTypes.object.isRequired,
  onReview: PropTypes.func.isRequired,
  // R3: URL-held tab (`wtab=`) and the side-by-side door per row
  tab: PropTypes.string,
  onTab: PropTypes.func,
  sideBySideHref: PropTypes.func,
  // the page's candidate: `{items: summaryVerdicts items, onChange(runId)}`
  candidate: PropTypes.shape({
    items: PropTypes.array,
    onChange: PropTypes.func,
  }),
};

export default WorklistTable;
