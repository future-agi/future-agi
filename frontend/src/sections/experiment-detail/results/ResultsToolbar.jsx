import React, { useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Menu,
  MenuItem,
  ListItemText,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useMediaQuery,
} from "@mui/material";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import CustomTooltip from "src/components/tooltip";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { fmtCount } from "src/sections/common/format/fmt";
import FilterMenu from "./FilterMenu";
import Iconify from "src/components/iconify";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { resultsShowOffered } from "src/api/develop/results-shows";
import { noBaselineLine } from "./showModel";

// Screen-reader only (the word stays for assistive tech and tests).
const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};
import SortMenu from "./SortMenu";
// HO results R2 / R1: the Hide-empty switch and the Add-a-column host.
import HideEmptySwitch from "src/sections/common/filters/HideEmptySwitch";
import CustomColumnHost from "src/sections/common/CustomColumns/CustomColumnHost";
import CustomSortChip from "src/sections/common/CustomColumns/CustomSortChip";

const SHOW_TIPS = {
  all: "Every row in view",
  regressed: "Worse: rows where any eval got worse for any run vs the baseline",
  improved:
    "Improved: rows where any eval got better for any run vs the baseline",
  changed: "Changed: rows where any eval moved for any run vs the baseline",
};

/**
 * The scope the Show counts are about (R3 analysis fix): they count every
 * other run against the baseline, so the label names the pair, e.g.
 * `Any run vs #2B` (or `#2D vs #2B` with one candidate), never a bare count
 * that reads as the verdict's run.
 */
export function showScopeLabel(runs, baselineId) {
  const base = (runs ?? []).find((r) => r.run_id === baselineId);
  if (!base?.label) return null;
  const others = (runs ?? []).filter((r) => r.run_id !== baselineId);
  if (others.length === 1 && others[0].label)
    return `${others[0].label} vs ${base.label}`;
  return `Any run vs ${base.label}`;
}

/**
 * `Show  All · Regressed 23 · Improved 31 · Changed 48` (≥ 2 runs). One view
 * (A3-10): the label is `Show`; the pair the counts are about (`Any run vs
 * #2B`) is in its tooltip and in each count's.
 */
/**
 * iterate r2 G3 (P8): `Worse than #2B · any eval 5` — one regression word
 * on the page (the verdict says `got worse` / `went pass → fail`). `short`
 * (below 1536 px, where the toolbar shares one row with Diff · Review ·
 * View · More): `Worse than #2B 5`; the tooltip keeps `any eval …`.
 */
export const showLabel = (k) =>
  // IT-R4 G3 (P4): `All · Worse (n) · Better (n) · Changed (n)`; who is
  // compared with whom (`worse than #2B on any eval`) is on hover.
  k === "regressed" ? "Worse" : k === "improved" ? "Better" : "Changed";

/**
 * iterate r3 G3 (P2): the tooltip of `Any eval worse (5)` —
 * `Task success worse on 2 (T13, T28); other evals on 3`.
 */
export function anyWorseTip({
  metricName,
  worse,
  names,
  total,
  oneCandidate,
  leadLabel,
}) {
  if (!metricName || !Number.isFinite(worse) || !Number.isFinite(total))
    return null;
  // L7c g6 (grid:show): with several candidates the two numbers have two
  // scopes; each one names its own (`14 rows worse on any eval for any run
  // · 4 on Correctness for #2B`).
  // IT-R5 G3 (P2): Worse means one thing — `9 rows worse on any eval · 3
  // on Correctness`, the same two numbers as the verdict.
  const listed = names?.length
    ? ` (${names.slice(0, 4).join(", ")}${names.length > 4 ? ` +${names.length - 4}` : ""})`
    : "";
  if (oneCandidate)
    return `${fmtCount(total, "row")} worse on any eval · ${worse} on ${metricName}${listed}`;
  return `${fmtCount(total, "row")} worse on any eval for any run · ${worse} on ${metricName} for ${leadLabel ?? "the lead run"}${listed}`;
}

/**
 * `Task success worse (2)`: the verdict's number, one click (rows pill of
 * exactly those rows; a second click clears it).
 */
const TaskWorseChip = ({ chip }) => (
  <CustomTooltip show size="small" placement="top" arrow title={chip.tooltip}>
    <span>
      <ToggleButton
        value="task-worse"
        size="small"
        selected={chip.active}
        disabled={!chip.count && !chip.active}
        onChange={chip.onClick}
        data-testid="results-task-worse"
        aria-label={`${chip.label} (${chip.count})`}
        sx={{
          height: 32,
          px: 1,
          typography: "s2",
          fontWeight: 500,
          textTransform: "none",
          whiteSpace: "nowrap",
          color: chip.count ? "error.dark" : "text.secondary",
          borderColor: "divider",
          "&.Mui-selected": {
            color: "primary.main",
            bgcolor: (t) => `${t.palette.primary.main}14`,
          },
        }}
      >
        {chip.label}{" "}
        <span
          style={{
            fontVariantNumeric: "tabular-nums",
            opacity: 0.85,
            marginLeft: 4,
          }}
        >
          ({fmtCount(chip.count)})
        </span>
      </ToggleButton>
    </span>
  </CustomTooltip>
);

TaskWorseChip.propTypes = { chip: PropTypes.object.isRequired };

/**
 * Why a show is turned off, or null when it is offered: where this build's
 * results read does not answer it, the shared line; for Worse, Better and
 * Changed while the view has no baseline to compare with, the line that
 * says so (`noBaseline`). All is always offered.
 */
const offLineOf = (k, noBaseline) => {
  if (!k || k === "all") return null;
  if (!resultsShowOffered(k)) return NOT_IN_RELEASE;
  return noBaseline ?? null;
};

/** A count the read gave, or null (not counted, or not loaded yet). */
const countOf = (counts, k) =>
  Number.isFinite(counts?.[k]) ? counts[k] : null;

// Worse, Better and Changed where this build's results read does not answer
// them, or with no baseline to compare with: each stays in the row, turned
// off, with the line that says why in its tooltip and as its accessible
// description, and is never asked for.
const ShowControl = ({
  value,
  counts,
  onChange,
  scope,
  baseLabel,
  short,
  anyEval,
  oneCandidate,
  regressedTip,
  noBaseline,
}) => {
  const offId = useId();
  const on = (k) => !offLineOf(k, noBaseline);
  return (
    <Stack direction="row" alignItems="center" gap={0.75}>
      {/* P2: with the `Task success worse (n)` chip in front, the group
          reads on its own (`All · Any run worse (14) · …`); the scope stays
          on hover. */}
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={
          scope
            ? `${scope}: these counts compare every other run in view with the baseline, eval by eval. The verdict above is about one run.`
            : "Which rows to show"
        }
      >
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          data-testid="results-show-scope"
          data-scope={scope ?? undefined}
          sx={visuallyHidden}
        >
          Show
        </Typography>
      </CustomTooltip>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={on(value) ? value : "all"}
        onChange={(_, v) => v && on(v) && onChange(v)}
        aria-label="Show rows"
        sx={{
          height: 32,
          p: "2px",
          alignItems: "center",
          "& .MuiToggleButton-root": {
            px: 1.25,
            py: 0,
            height: 26,
            typography: "s2",
            fontWeight: 500,
            textTransform: "none",
            color: "text.secondary",
            gap: 0.5,
          },
          "& .Mui-selected": {
            color: "primary.main !important",
            bgcolor: (t) => `${t.palette.primary.main}14 !important`,
          },
        }}
      >
        <ToggleButton value="all" aria-label="Show all rows">
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title={SHOW_TIPS.all}
          >
            <span>All</span>
          </CustomTooltip>
        </ToggleButton>
        {["regressed", "improved", "changed"].map((k) => {
          const offLine = offLineOf(k, noBaseline);
          const off = Boolean(offLine);
          const name = `Show ${k === "regressed" ? "rows worse than the baseline on any eval" : k}`;
          // A turned-off show was never counted, so it carries no count;
          // nor does one the read did not count (or has not yet).
          const count = off ? null : countOf(counts, k);
          return (
            <ToggleButton
              key={k}
              value={k}
              disabled={off}
              aria-describedby={off ? `${offId}-${k}` : undefined}
              aria-label={
                count == null ? name : `${name}, ${fmtCount(count, "row")}`
              }
            >
              {/* A turned-off button takes no pointer; its label does, so
                  the tooltip still says the line on hover. */}
              <CustomTooltip
                show
                size="small"
                placement="top"
                arrow
                title={
                  off
                    ? offLine
                    : k === "regressed"
                      ? regressedTip ??
                        `Worse: rows where any eval got worse for any run vs ${baseLabel ?? "the baseline"}${scope ? ` (${scope})` : ""}`
                      : scope
                        ? `${SHOW_TIPS[k]} (${scope})`
                        : SHOW_TIPS[k]
                }
              >
                <span
                  data-testid={`results-show-${k}`}
                  style={off ? { pointerEvents: "auto" } : undefined}
                >
                  {showLabel(k)}
                  {count == null ? null : (
                    <>
                      {" "}
                      <span
                        style={{
                          fontVariantNumeric: "tabular-nums",
                          opacity: 0.8,
                        }}
                      >
                        {`(${fmtCount(count)})`}
                      </span>
                    </>
                  )}
                </span>
              </CustomTooltip>
              {off && (
                <span id={`${offId}-${k}`} style={visuallyHidden}>
                  {offLine}
                </span>
              )}
            </ToggleButton>
          );
        })}
      </ToggleButtonGroup>
    </Stack>
  );
};

ShowControl.propTypes = {
  anyEval: PropTypes.bool,
  oneCandidate: PropTypes.bool,
  regressedTip: PropTypes.string,
  scope: PropTypes.string,
  baseLabel: PropTypes.string,
  short: PropTypes.bool,
  value: PropTypes.string,
  counts: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  noBaseline: PropTypes.string,
};

/**
 * IT-R5 G3 (P1, one-view budget): below 1440 px the four Show segments
 * fold into ONE menu button that names what is shown (`Worse (14) ▾`);
 * each item keeps its count and the same tooltip words.
 */
const ShowMenu = ({
  value: asked,
  counts,
  onChange,
  regressedTip,
  scope,
  noBaseline,
}) => {
  const [anchor, setAnchor] = useState(null);
  const on = (k) => !offLineOf(k, noBaseline);
  const value = on(asked) ? asked : "all";
  const label = (k) =>
    k === "all"
      ? "All rows"
      : on(k) && countOf(counts, k) != null
        ? `${showLabel(k)} (${fmtCount(countOf(counts, k))})`
        : showLabel(k);
  const tip = (k) =>
    k === "all"
      ? SHOW_TIPS.all
      : k === "regressed"
        ? regressedTip ?? SHOW_TIPS.regressed
        : `${SHOW_TIPS[k]}${scope ? ` (${scope})` : ""}`;
  return (
    <>
      <CustomTooltip show size="small" placement="top" arrow title={tip(value)}>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          aria-haspopup="menu"
          aria-label={`Show: ${label(value)}`}
          onClick={(e) => setAnchor(e.currentTarget)}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          data-testid="results-show-menu"
          sx={{
            height: 32,
            px: 1.25,
            typography: "s2",
            fontWeight: 500,
            whiteSpace: "nowrap",
            borderColor: "divider",
            color: value === "all" ? "text.primary" : "primary.main",
            "& .MuiButton-endIcon": { ml: 0.25 },
          }}
        >
          <span
            data-testid={
              value === "regressed" ? "results-show-regressed" : undefined
            }
          >
            {label(value)}
          </span>
        </Button>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        {["all", "regressed", "improved", "changed"].map((k) => (
          <MenuItem
            key={k}
            selected={k === value}
            disabled={!on(k)}
            data-testid={`results-show-item-${k}`}
            onClick={() => {
              setAnchor(null);
              if (on(k)) onChange(k);
            }}
          >
            <ListItemText
              primary={label(k)}
              secondary={on(k) ? tip(k) : offLineOf(k, noBaseline)}
              primaryTypographyProps={{ typography: "s1" }}
              secondaryTypographyProps={{
                typography: "s3",
                sx: { maxWidth: 300, whiteSpace: "normal" },
              }}
            />
          </MenuItem>
        ))}
      </Menu>
    </>
  );
};
ShowMenu.propTypes = {
  value: PropTypes.string,
  counts: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  regressedTip: PropTypes.string,
  scope: PropTypes.string,
  noBaseline: PropTypes.string,
};

/**
 * Left half of the results toolbar, one row (PRODUCT-SPEC §3.10.4, one view
 * A3-10): Search (`/`), Show (hidden with one run), Filter and Sort.
 * `compact` (below 1440 px) shortens Search and folds Filter to its icon.
 */
const ResultsToolbar = ({
  search,
  onSearch,
  show,
  showCounts,
  onShow,
  multiRun,
  filterView,
  onAddPill,
  sort,
  onSort,
  sortMetrics,
  runs,
  runIds,
  baselineId,
  hasTrials,
  total,
  compact = false,
  defaultSort,
  taskWorse,
  searchOnly = false,
  onHideEmptyLeftOut,
}) => {
  const [text, setText] = useState(search ?? "");
  // Worse, Better and Changed compare the other runs in view with the
  // baseline: with none to compare with they are turned off, saying why.
  const noBaseline = noBaselineLine({
    runIds: runIds ?? (runs ?? []).map((r) => r.run_id),
    baselineId,
  });
  // Below 1536 px the long P8 words get a short form (full on hover) so the
  // pinned actions on the right keep their room on this one row.
  const shortLabels = useMediaQuery("(max-width:1535.95px)");
  const timer = useRef(null);
  const inputRef = useRef(null);
  // HO R2: the switch measures the toolbar row this toolbar sits in.
  const rootRef = useRef(null);

  useEffect(() => setText(search ?? ""), [search]);
  useEffect(() => () => clearTimeout(timer.current), []);

  // `/` focuses search (KEYMAP "search", results surface); Esc in the
  // field clears it (RC#11).
  useShortcuts("results", {
    search: () => inputRef.current?.querySelector?.("input")?.focus(),
  });

  return (
    <Stack
      ref={rootRef}
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ flexWrap: "nowrap", flexShrink: 0 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        ref={inputRef}
        onKeyDown={(e) => {
          if (e.key === "Escape" && text) {
            e.stopPropagation();
            setText("");
            clearTimeout(timer.current);
            onSearch("");
          }
        }}
      >
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title="Search inputs, outputs and reasons. Press / to focus, Esc to clear."
        >
          <span>
            <FormSearchField
              size="small"
              placeholder="Search rows (/)"
              searchQuery={text}
              onChange={(e) => {
                const value = e.target.value;
                setText(value);
                clearTimeout(timer.current);
                timer.current = setTimeout(() => onSearch(value), 150);
              }}
              sx={{
                width: compact ? 156 : 184,
                "& .MuiInputBase-root": { height: 32 },
              }}
            />
          </span>
        </CustomTooltip>
        {search && Number.isFinite(total) && (
          <Typography
            typography="s2"
            color="text.secondary"
            noWrap
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {fmtCount(total, "row")}
          </Typography>
        )}
      </Stack>
      {/* IT-R4 G3 (P4): the verdict's `See the 3 rows that went pass →
          fail` is the one-click filter; the toolbar keeps the segments. */}
      {/* IT-R5 G3 (P5): the drawer is open → the toolbar is Search (and
          the Show set in effect, so the rows keep their meaning). */}
      {searchOnly ? (
        multiRun && show && show !== "all" ? (
          <ShowMenu
            value={show}
            counts={showCounts}
            onChange={onShow}
            scope={showScopeLabel(runs, baselineId)}
            regressedTip={taskWorse?.anyTip ?? undefined}
            noBaseline={noBaseline ?? undefined}
          />
        ) : null
      ) : null}
      {!searchOnly && multiRun && compact ? (
        <ShowMenu
          value={show}
          counts={showCounts}
          onChange={onShow}
          scope={showScopeLabel(runs, baselineId)}
          regressedTip={taskWorse?.anyTip ?? undefined}
          noBaseline={noBaseline ?? undefined}
        />
      ) : null}
      {!searchOnly && multiRun && !compact && (
        <ShowControl
          value={show}
          counts={showCounts}
          onChange={onShow}
          scope={showScopeLabel(runs, baselineId)}
          baseLabel={runs.find((r) => r.run_id === baselineId)?.label}
          short={shortLabels}
          anyEval={Boolean(taskWorse)}
          oneCandidate={
            runs.filter((r) => r.run_id !== baselineId && !r.not_found)
              .length === 1
          }
          regressedTip={taskWorse?.anyTip ?? undefined}
          noBaseline={noBaseline ?? undefined}
        />
      )}
      {searchOnly ? null : (
        <>
          <FilterMenu view={filterView} onAdd={onAddPill} compact={compact} />
          <SortMenu
            sort={sort}
            onChange={onSort}
            metrics={sortMetrics}
            runs={runs}
            baselineId={baselineId}
            hasTrials={hasTrials}
            compact={compact}
            shortLabel={shortLabels}
            defaultSort={defaultSort}
          />
          {/* HO R1: the compare toolbar (several runs, 1440 px and up)
              has no room left beside its pinned Review · View · More, so
              there the chip is its arrow and ✕ (tooltip: the sentence). */}
          <CustomSortChip
            datasetId={filterView.datasetId}
            sort={sort}
            runs={runs}
            onClear={() => onSort(defaultSort ?? "order")}
            compact={compact || shortLabels || multiRun}
            tight={multiRun && !compact}
          />
          {/* HO R2: shown with its words when the row has room for it. */}
          <HideEmptySwitch
            datasetId={filterView.datasetId}
            view={filterView.resultsView}
            rowOf={() => rootRef.current?.parentElement ?? null}
            onLeftOut={onHideEmptyLeftOut}
          />
        </>
      )}
      <CustomColumnHost
        datasetId={filterView.datasetId}
        view={filterView.resultsView}
        runs={runs}
      />
    </Stack>
  );
};

ResultsToolbar.propTypes = {
  search: PropTypes.string,
  onSearch: PropTypes.func.isRequired,
  show: PropTypes.string,
  showCounts: PropTypes.object,
  onShow: PropTypes.func.isRequired,
  multiRun: PropTypes.bool,
  filterView: PropTypes.object.isRequired,
  onAddPill: PropTypes.func.isRequired,
  sort: PropTypes.string,
  onSort: PropTypes.func.isRequired,
  sortMetrics: PropTypes.array.isRequired,
  runs: PropTypes.array.isRequired,
  /** The ids of the runs in view (else those of `runs`), for whether
   * Worse, Better and Changed have another run to compare with the
   * baseline. */
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  hasTrials: PropTypes.bool,
  total: PropTypes.number,
  compact: PropTypes.bool,
  searchOnly: PropTypes.bool,
  defaultSort: PropTypes.string,
  /** P2: `{label, count, tooltip, active, onClick, anyTip}` — the verdict's
   * `Task success worse (n)`, first in the row. */
  taskWorse: PropTypes.object,
  /** Told whether the row leaves the hide-empty switch out (no room). */
  onHideEmptyLeftOut: PropTypes.func,
};

export default ResultsToolbar;
