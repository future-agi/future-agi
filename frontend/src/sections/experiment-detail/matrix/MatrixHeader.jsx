import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { runHowItRan } from "src/sections/common/Runs/runHowItRan";
import { populationText } from "./pageShip";
import { runFingerprint } from "./runFingerprint";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Chip,
  Divider,
  IconButton,
  Menu,
  MenuItem,
  Popover,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import RunChip from "src/sections/common/Runs/RunChip";
import RunProvenance from "src/sections/common/Runs/RunProvenance";
import {
  runModelsText,
  runShortName,
  runSpokenName,
} from "src/sections/common/Runs/runLabel";
import Term from "src/sections/common/ux/Term";
import { fmtCount, fmtPct } from "src/sections/common/format/fmt";
import { alpha } from "@mui/material/styles";
import {
  REGRESSION_THRESHOLD,
  regressionBadgeText,
} from "src/sections/common/Analysis/voiceSummaryModel";
import ViewMenu from "src/sections/common/ux/oneview/ViewMenu";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import {
  GROUP_OPTIONS,
  SHOW_OPTIONS,
  SORT_OPTIONS,
  fmtMoney,
} from "./matrixModel";
import { CELL_WIDTH } from "./MatrixCell";

const menuButtonSx = {
  height: 30,
  px: 1,
  typography: "s2",
  color: "text.primary",
  borderColor: "divider",
  whiteSpace: "nowrap",
};

/** `Group: Category ▾` / `Sort: Most disagreement ▾` button + menu. */
function PickMenu({
  prefix,
  value,
  options,
  onChange,
  ariaLabel,
  icon,
  compact,
}) {
  const [anchor, setAnchor] = useState(null);
  const current = options.find((o) => o.value === value) ?? options[0];
  // 8c: below 1440 px the prefix shrinks to its icon (tooltip
  // keeps the words) so the toolbar stays on one line at 1280.
  const button = (
    <Button
      variant="outlined"
      size="small"
      aria-label={`${ariaLabel}: ${current?.label ?? ""}`}
      onClick={(e) => setAnchor(e.currentTarget)}
      endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
      sx={{ ...menuButtonSx, ...(compact ? { px: 0.75 } : null) }}
    >
      {/* IT-R3 G6 P5 (rule 23): the label keeps its verb at every width
          (`Group: None`, never a bare `None`). */}
      <Box component="span" sx={{ color: "text.secondary", mr: 0.5 }}>
        {prefix}
      </Box>
      {current?.label}
    </Button>
  );
  return (
    <>
      {compact && icon ? (
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={`${prefix} ${current?.label ?? ""}`}
        >
          {button}
        </CustomTooltip>
      ) : (
        button
      )}
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { minWidth: 200 } } }}
      >
        {options.map((o) =>
          o.divider ? (
            <Divider key={o.value} />
          ) : (
            <MenuItem
              key={o.value}
              selected={o.value === current?.value}
              onClick={() => {
                setAnchor(null);
                onChange(o.value);
              }}
              sx={{ typography: "s2" }}
            >
              {o.label}
            </MenuItem>
          ),
        )}
      </Menu>
    </>
  );
}

PickMenu.propTypes = {
  icon: PropTypes.string,
  compact: PropTypes.bool,
  prefix: PropTypes.string,
  value: PropTypes.string,
  options: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  ariaLabel: PropTypes.string,
};

const DEFINITIONS = [
  [
    "Pass/fail differs",
    "at least one run passes every trial and at least one run fails every trial.",
  ],
  [
    "Better / worse",
    "the candidate passes more / fewer trials of a task than the baseline (the verdict's counts, over tasks both ran).",
  ],
  ["Flaky", "at least one run passes some trials but not all."],
  ["No run reliable", "no run passes all its trials."],
  ["All reliable", "every run passes every trial."],
];

/** `How is this computed?`: the four row sets and the column numbers. */
function HowComputed({ anchor, onClose, k }) {
  return (
    <Popover
      open={Boolean(anchor)}
      anchorEl={anchor}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { p: 2, width: 380 } } }}
    >
      <Typography
        typography="s1"
        fontWeight="fontWeightSemiBold"
        sx={{ mb: 1 }}
      >
        How is this computed?
      </Typography>
      <Stack spacing={0.75}>
        {DEFINITIONS.map(([term, def]) => (
          <Typography key={term} typography="s2">
            <b>{term}</b>: {def}
          </Typography>
        ))}
        <Divider sx={{ my: 0.5 }} />
        <Typography typography="s2" color="text.secondary">
          Success rate (pass@1) = passed trials ÷ scored trials. Reliable on all{" "}
          {k} tries (pass^{k}) = tasks whose {k} trials all pass. Cost per task
          = mean cost of one try; Time per task = median time of one try (p50).
          Tries that didn&apos;t run (setup) are left out; errors count as
          fails. The chart&apos;s bars are 95% intervals from a task-level
          bootstrap (2,000 resamples, seeded).
        </Typography>
      </Stack>
    </Popover>
  );
}

HowComputed.propTypes = {
  anchor: PropTypes.any,
  onClose: PropTypes.func,
  k: PropTypes.number,
};

/**
 * The Matrix toolbar, one row (one view, A3-19): `30 rows · 3 runs · 3
 * trials each` (i: How is this computed?) · `Group: None ▾` · `Sort: Most
 * disagreement ▾` · `Show  All 30 · Differ 12 · Flaky 11 · No run reliable
 * 7 · All reliable 7` · spacer · `View` (Dense rows, Cost bars, More
 * metrics, the Frontier section, saved views, Reset view).
 */
// IT-R4 G6 P3: three chips in the row; the rest in `More ▾`.
const MAIN_SHOW = ["all", "disagree", "flaky"];
const chipGroupSx = {
  "& .MuiToggleButton-root": {
    height: 30,
    px: 1,
    typography: "s2",
    textTransform: "none",
    whiteSpace: "nowrap",
    color: "text.secondary",
    borderColor: "divider",
    "&.Mui-selected": {
      color: "text.primary",
      bgcolor: "action.selected",
      fontWeight: 500,
    },
  },
};

export function MatrixToolbar({
  headerText,
  running,
  finished,
  group,
  groupOptions,
  onGroup,
  sort,
  sortOptions,
  onSort,
  show,
  showCounts,
  onShow,
  dense,
  onDense,
  showCost,
  onShowCost,
  k,
  extra,
  onReview,
  moreMetrics,
  onMoreMetrics,
  scopeId,
  onResetView,
  heat,
  heatOptions,
  onHeat,
  heatLegend,
  viewActions,
  defaultGroup,
}) {
  const howRef = useRef(null);
  const [howAnchor, setHowAnchor] = useState(null);
  useRegisterCommands(
    "matrix-how",
    [
      {
        id: "matrix.how",
        label: "How is this computed?",
        keywords: ["definition", "pass@1", "pass^k", "differ", "flaky"],
        icon: "mdi:information-outline",
        run: () => setHowAnchor(howRef.current),
      },
    ],
    [],
  );
  const showOpt = SHOW_OPTIONS.find((o) => o.value === show);
  const compact = !useMediaQuery("(min-width:1440px)");
  const reviewCount = showCounts?.[show] ?? 0;
  const [moreShow, setMoreShow] = useState(null);
  const moreSelected = MAIN_SHOW.includes(show)
    ? null
    : SHOW_OPTIONS.find((o) => o.value === show) ?? null;
  // IT-R4 G6 P3 (S6): the View menu's radio groups.
  const groupList = groupOptions ?? GROUP_OPTIONS;
  const sortList = sortOptions ?? SORT_OPTIONS;
  const viewChoices = [
    ...(heatOptions?.length > 1 && onHeat
      ? [
          {
            id: "color",
            label: "Color",
            value: heat ?? "result",
            options: heatOptions,
            onChange: onHeat,
            defaultValue: "result",
          },
        ]
      : []),
    {
      id: "group",
      label: "Group",
      value: group ?? "none",
      options: groupList,
      onChange: onGroup,
      defaultValue: defaultGroup ?? "none",
    },
    {
      id: "sort",
      label: "Sort",
      value: sort ?? "disagreement",
      options: sortList,
      onChange: onSort,
      defaultValue: "disagreement",
    },
  ];
  const choiceChips = viewChoices
    .filter((c) => c.value != null && c.value !== c.defaultValue)
    .map((c) => ({
      id: c.id,
      label: c.label,
      text: `${c.label}: ${c.options.find((o) => o.value === c.value)?.label ?? c.value}`,
      reset: () => c.onChange?.(c.defaultValue),
    }));
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ px: 1, py: 0.25, flexWrap: "wrap", rowGap: 0.75 }}
      data-testid="matrix-toolbar"
    >
      <Stack direction="row" alignItems="center" gap={0.25}>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}
        >
          {/* 8c: `30 rows · 3 runs` below 1440 (tries per cell). */}
          {compact
            ? String(headerText ?? "")
                .split(" · ")
                .slice(0, 2)
                .join(" · ")
            : headerText}
        </Typography>
        {/* IT-R4 G6 P3: `How is this computed?` lives in View (and ⌘K). */}
        <Box ref={howRef} aria-hidden sx={{ width: 0, height: 20 }} />
      </Stack>
      {running && (
        <Typography
          typography="s2"
          color="primary.main"
          sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
        >
          {running}
        </Typography>
      )}
      {/* W28: a finished matrix says so (`Completed · 8 of 8 tries`). */}
      {!running && finished && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="matrix-finished"
          sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}
        >
          {finished}
        </Typography>
      )}
      {/* IT-R4 G6 P3: Color / Group / Sort / Row checks live in ONE View
          menu; a choice that is not the default shows as a chip here. */}
      {choiceChips.map((c) => (
        <Chip
          key={c.id}
          size="small"
          label={c.text}
          onDelete={c.reset}
          deleteIcon={
            <Iconify
              icon="mdi:close"
              width={14}
              aria-label={`Reset ${c.label}`}
            />
          }
          data-testid={`matrix-choice-chip-${c.id}`}
          sx={{ height: 24, typography: "s2" }}
        />
      ))}
      {heatLegend ? (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          data-testid="matrix-heat-legend"
          sx={{ display: "flex", alignItems: "center", gap: 0.5 }}
        >
          <Box
            component="span"
            aria-hidden
            sx={(t) => ({
              width: 40,
              height: 8,
              borderRadius: 0.5,
              background: `linear-gradient(90deg, ${alpha(t.palette.success.main, 0.18)}, ${alpha(t.palette.warning.main, 0.3)}, ${alpha(t.palette.error.main, 0.38)})`,
            })}
          />
          {heatLegend}
        </Typography>
      ) : null}
      {extra}
      <ToggleButtonGroup
        exclusive
        size="small"
        value={MAIN_SHOW.includes(show) ? show : null}
        onChange={(_, v) => v && onShow(v)}
        aria-label="Show rows"
        sx={chipGroupSx}
      >
        {SHOW_OPTIONS.filter((o) => MAIN_SHOW.includes(o.value)).map((o) => (
          <ToggleButton
            key={o.value}
            value={o.value}
            aria-label={`${o.label}, ${fmtCount(showCounts?.[o.value] ?? 0, "task")}`}
          >
            <CustomTooltip
              show
              size="small"
              placement="top"
              arrow
              title={o.tooltip}
            >
              <Box component="span">
                {o.label}
                <Box
                  component="span"
                  sx={{
                    ml: 0.5,
                    fontVariantNumeric: "tabular-nums",
                    opacity: 0.8,
                  }}
                >
                  {fmtCount(showCounts?.[o.value] ?? 0)}
                </Box>
              </Box>
            </CustomTooltip>
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      <Button
        size="small"
        color="inherit"
        aria-haspopup="menu"
        aria-expanded={Boolean(moreShow)}
        onClick={(e) => setMoreShow(e.currentTarget)}
        endIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        data-testid="matrix-show-more"
        sx={{
          height: 30,
          px: 1,
          typography: "s2",
          whiteSpace: "nowrap",
          color: moreSelected ? "text.primary" : "text.secondary",
          bgcolor: moreSelected ? "action.selected" : "transparent",
          fontWeight: moreSelected ? 500 : 400,
        }}
      >
        {moreSelected
          ? `${moreSelected.label} ${fmtCount(showCounts?.[moreSelected.value] ?? 0)}`
          : "More"}
      </Button>
      <Menu
        anchorEl={moreShow}
        open={Boolean(moreShow)}
        onClose={() => setMoreShow(null)}
      >
        {SHOW_OPTIONS.filter((o) => !MAIN_SHOW.includes(o.value)).map((o) => (
          <MenuItem
            key={o.value}
            selected={show === o.value}
            onClick={() => {
              setMoreShow(null);
              onShow(o.value);
            }}
            sx={{ typography: "s2", gap: 1 }}
          >
            <CustomTooltip
              show
              size="small"
              placement="right"
              title={o.tooltip}
            >
              <span>{o.label}</span>
            </CustomTooltip>
            <Box component="span" sx={{ ml: "auto", color: "text.secondary" }}>
              {fmtCount(showCounts?.[o.value] ?? 0)}
            </Box>
          </MenuItem>
        ))}
      </Menu>
      <Box sx={{ flex: 1 }} />
      {onReview && showOpt?.rail && reviewCount > 0 && (
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title="Step through these tasks with every run side by side. J/K to move, 1–4 to judge."
        >
          <Button
            size="small"
            variant="outlined"
            color="primary"
            startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
            onClick={() => onReview(showOpt.rail)}
            sx={{ height: 30, typography: "s2", whiteSpace: "nowrap" }}
            data-testid="matrix-review-side-by-side"
          >
            {`Review ${fmtCount(reviewCount, "task")} side by side`}
          </Button>
        </CustomTooltip>
      )}
      <ViewMenu
        surface="matrix"
        scopeId={scopeId}
        iconOnly={false}
        choices={viewChoices}
        actions={[
          ...(viewActions ?? []),
          {
            id: "how",
            label: "How is this computed?",
            icon: "mdi:information-outline",
            onClick: () => setHowAnchor(howRef.current),
          },
        ]}
        extras={[
          {
            id: "dense",
            label: "Dense rows",
            checked: Boolean(dense),
            onChange: (v) => onDense(v),
          },
          {
            id: "costBars",
            label: "Cost bars",
            checked: Boolean(showCost),
            onChange: (v) => onShowCost(v),
            description: "A bar under each cell for its cost per task",
          },
          ...(onMoreMetrics
            ? [
                {
                  id: "moreMetrics",
                  label: "More metrics",
                  checked: Boolean(moreMetrics),
                  onChange: (v) => onMoreMetrics(v),
                  description: `Reliable on all ${k} tries and Time per task in the header`,
                },
              ]
            : []),
        ]}
        onReset={onResetView}
      />
      <HowComputed
        anchor={howAnchor}
        onClose={() => setHowAnchor(null)}
        k={k}
      />
    </Stack>
  );
}

MatrixToolbar.propTypes = {
  // IT-R4 G6 P3: one-click actions in View (Row checks…) and the group
  // that counts as default (persona on voice data).
  viewActions: PropTypes.array,
  defaultGroup: PropTypes.string,
  heat: PropTypes.string,
  heatOptions: PropTypes.array,
  onHeat: PropTypes.func,
  heatLegend: PropTypes.string,
  headerText: PropTypes.string,
  running: PropTypes.string,
  finished: PropTypes.string,
  group: PropTypes.string,
  groupOptions: PropTypes.array,
  onGroup: PropTypes.func,
  sort: PropTypes.string,
  sortOptions: PropTypes.array,
  onSort: PropTypes.func,
  show: PropTypes.string,
  showCounts: PropTypes.object,
  onShow: PropTypes.func,
  dense: PropTypes.bool,
  onDense: PropTypes.func,
  showCost: PropTypes.bool,
  onShowCost: PropTypes.func,
  k: PropTypes.number,
  extra: PropTypes.node,
  onReview: PropTypes.func,
  moreMetrics: PropTypes.bool,
  onMoreMetrics: PropTypes.func,
  scopeId: PropTypes.string,
  onResetView: PropTypes.func,
};

// 8c: one name line + the provenance line (kind, models, where)
// replace the source badge and the 2-line name; the note rides in the delta
// line's ⓘ, so a header is ~118 px and 8+ rows fit at 1280×720.
export const HEADER_LINES = {
  chip: 24,
  name: 16,
  prov: 18,
  value: 17,
  delta: 20,
  note: 0,
};

// The baseline chip (`#2B Baseline ⌄`, ~120 px) + remove (~24 px) + a
// readable version tag (~28 px): narrower chip lines drop the tag rather
// than squeeze it to a stray ellipsis dot (1280 × 720 with 5 runs).
const BASELINE_TAG_MIN_LINE = 172;

const valueSx = {
  height: HEADER_LINES.value,
  lineHeight: `${HEADER_LINES.value}px`,
  typography: "s2",
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  whiteSpace: "nowrap",
};

/** The metric rows of a column block, in order (R3 F3 names). */
// IT-R1 G2 P5/P6: plain subtitles after the jargon (`pass@1 · passed on
// the first try`) and `Cost per solved task` (cost ÷ tasks solved: what
// "value" means) under Cost per task.
// IT-R5 G6 P3: `costs` false (a window under 820 px high, More metrics
// off) puts Cost per task and Cost per solved task behind More metrics.
export const metricRows = ({ k = 3, moreMetrics = false, costs = true } = {}) =>
  [
    {
      key: "success",
      term: "successRate",
      label: "Success rate",
      sub: "pass@1 · passed on the first try",
      strong: true,
    },
    moreMetrics && {
      key: "reliable",
      term: "reliable",
      label: "Reliable",
      sub: `pass^${k} · passed all ${k} tries`,
    },
    (costs || moreMetrics) && {
      key: "cost",
      term: "costPerTask",
      label: "Cost per task",
    },
    (costs || moreMetrics) && {
      key: "costSolved",
      term: "costPerSolved",
      label: "Cost per solved task",
    },
    moreMetrics && {
      key: "time",
      term: "timePerTask",
      label: "Time per task (p50)",
    },
  ].filter(Boolean);

/** The top-left cell: what the numbers under each run are (each a Term). */
export function HeaderLabels({ k, moreMetrics, extra, costs = true, legend }) {
  return (
    <Stack
      sx={{
        px: 1.25,
        pt: 0.75,
        pb: 0.5,
        height: "100%",
        justifyContent: "flex-end",
      }}
    >
      {extra ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ mb: "auto", ml: -0.75 }}
        >
          {extra}
        </Stack>
      ) : null}
      {metricRows({ k, moreMetrics, costs }).map((m) => (
        <Typography
          key={m.key}
          typography="s2"
          color="text.secondary"
          noWrap
          component="div"
          sx={{
            height: HEADER_LINES.value,
            lineHeight: `${HEADER_LINES.value}px`,
          }}
        >
          <Term k={m.term}>{m.label}</Term>
          {m.sub ? (
            <Typography
              component="span"
              typography="s3"
              color="text.disabled"
            >{` · ${m.sub}`}</Typography>
          ) : null}
          {/* IT-R5 G6 P3: the legend is a `?` on the Success header. */}
          {m.key === "success" && legend ? legend : null}
        </Typography>
      ))}
      <Box sx={{ height: HEADER_LINES.delta }} />
    </Stack>
  );
}

HeaderLabels.propTypes = {
  costs: PropTypes.bool,
  legend: PropTypes.node,
  // 8c: `Row checks ▸` in the header cell's free space.
  extra: PropTypes.node,
  k: PropTypes.number,
  moreMetrics: PropTypes.bool,
  onMoreMetrics: PropTypes.func,
};

/**
 * IT-R4 G6 P3 (budget): `▲ 10 ▼ 1` as ONE control; it opens `Show the 10
 * tasks that got better` / `Show the 1 task that got worse` (each filters
 * the rows, as the two buttons did).
 */
function DeltaPair({ better, worse, active, onDelta, upTip, downTip }) {
  const [anchor, setAnchor] = useState(null);
  const item = (dir, n, tip) => (
    <MenuItem
      key={dir}
      disabled={!n}
      selected={active === dir}
      onClick={() => {
        setAnchor(null);
        onDelta(dir);
      }}
      sx={{ typography: "s2", gap: 1 }}
    >
      <Box
        component="span"
        sx={{ color: dir === "up" ? "success.main" : "error.main" }}
      >
        {dir === "up" ? "▲" : "▼"}
      </Box>
      {active === dir ? "Show every task" : tip}
    </MenuItem>
  );
  return (
    <>
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`${upTip} · ${downTip}`}
      >
        <ButtonBase
          onClick={(e) => setAnchor(e.currentTarget)}
          disabled={!better && !worse}
          aria-haspopup="menu"
          aria-pressed={Boolean(active)}
          aria-label={`${better} better, ${worse} worse: filter the rows`}
          data-testid="matrix-delta-pair"
          sx={(t) => ({
            px: 0.5,
            height: 20,
            gap: 0.75,
            borderRadius: 0.5,
            typography: "s2",
            fontVariantNumeric: "tabular-nums",
            bgcolor: active ? t.palette.action.selected : "transparent",
            "&:hover": { bgcolor: "action.hover" },
          })}
        >
          <Box
            component="span"
            sx={{ color: better ? "success.main" : "text.disabled" }}
          >
            {`▲ ${better}`}
          </Box>
          <Box
            component="span"
            sx={{ color: worse ? "error.main" : "text.disabled" }}
          >
            {`▼ ${worse}`}
          </Box>
        </ButtonBase>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
      >
        {item("up", better, upTip)}
        {item("down", worse, downTip)}
      </Menu>
    </>
  );
}
DeltaPair.propTypes = {
  better: PropTypes.number,
  worse: PropTypes.number,
  active: PropTypes.string,
  onDelta: PropTypes.func,
  upTip: PropTypes.string,
  downTip: PropTypes.string,
};

// Rule 30 role words; ` · current` folds away in a narrow column (the
// title keeps it).
const roleNode = (text) => {
  if (!text) return undefined;
  const [head, ...rest] = String(text).split(" · ");
  if (!rest.length) return text;
  return (
    <span title={text}>
      {head}
      <Box
        component="span"
        sx={{
          [`@container mxchip (max-width: 200px)`]: { display: "none" },
        }}
      >
        {` · ${rest.join(" · ")}`}
      </Box>
    </span>
  );
};

/**
 * Column header of one run: RunChip + 2-line name, pass@1 · pass^k · $ / row
 * · p50 / row, `▲ 6 ▼ 14` vs the baseline (each filters the rows) and the
 * infra / error line.
 */
export function MatrixColumnHeader({
  run,
  stats,
  color,
  isBaseline,
  baselineLabel,
  menu,
  activeDelta,
  onDelta,
  highlighted,
  moreMetrics,
  costs = true,
  voice,
  versionTag,
  onRemove,
  removeReason,
  roleText,
  picked = false,
  onPick,
  sameAs = null,
}) {
  const how = runHowItRan(run);
  // IT-R5 G6 P3: the config fingerprint (model · framework · tools).
  const fingerprint = runFingerprint(run);
  // Rule 28: the success value states its population.
  const p1 = stats.passAt1;
  const scoredTries = p1?.n ?? 0;
  const unscored = Math.max(0, (stats.attempts ?? 0) - scoredTries);
  const population =
    p1?.n > 0
      ? populationText({
          rate: p1.rate,
          passed: p1.pass,
          scored: p1.n,
          total: stats.attempts,
        })
      : null;
  // IT-R2 G2 P7: two readable lines, never cut mid-word: who ran
  // (`Claude Code`) and every model it called (`claude-sonnet-4-5`); the
  // model line opens the run's profile card (framework, models, tools /
  // MCP, steps, tokens, cost, where it ran).
  const fullName = runShortName(run) || stats.name || "";
  const name = runSpokenName(run) || fullName;
  const models = runModelsText(run);
  const regs = voice?.regressions ?? [];
  const solvedRate = stats.passAt1?.rate;
  const values = {
    success:
      unscored > 0 && p1?.n > 0 ? (
        <CustomTooltip show size="small" placement="top" title={population}>
          <span data-testid="matrix-success-population">
            {stats.passAt1Text}
            <Box
              component="span"
              className="mx-pop"
              sx={{ color: "text.secondary" }}
            >
              {` · ${unscored} not scored`}
            </Box>
          </span>
        </CustomTooltip>
      ) : population ? (
        <CustomTooltip show size="small" placement="top" title={population}>
          <span data-testid="matrix-success-population">
            {stats.passAt1Text}
          </span>
        </CustomTooltip>
      ) : (
        stats.passAt1Text
      ),
    reliable: stats.k > 1 ? stats.passHatKText : "—",
    cost: stats.costText,
    // Mean cost of a try ÷ share of tries solved = cost per solved try.
    costSolved:
      Number.isFinite(stats.costPerRow) && solvedRate > 0
        ? fmtMoney(stats.costPerRow / solvedRate)
        : "—",
    time: stats.p50Text,
  };
  const note = [stats.infraText, stats.errorText].filter(Boolean).join(" · ");
  return (
    <Stack
      sx={(t) => ({
        position: "relative",
        width: "100%",
        minWidth: CELL_WIDTH,
        px: 1,
        pt: 0.5,
        pb: 0.25,
        height: "100%",
        boxShadow: highlighted
          ? `inset 0 -2px 0 ${color?.solid}`
          : picked
            ? `inset 0 0 0 1px ${t.palette.primary.main}`
            : "none",
        bgcolor: highlighted ? t.palette.action.hover : "transparent",
        containerType: "inline-size",
        containerName: "mxcol",
        "@container mxcol (max-width: 200px)": {
          "& .mx-pop": { display: "none" },
        },
        // IT-R4 G6 P3: the chip's caret and the × show on hover / focus
        // only (still in the tab order).
        "& .mx-hover-only, & [data-run-id] [aria-label$=' menu']": {
          opacity: 0,
          transition: "opacity 120ms ease",
        },
        "&:hover .mx-hover-only, &:focus-within .mx-hover-only, &:hover [data-run-id] [aria-label$=' menu'], &:focus-within [data-run-id] [aria-label$=' menu']":
          { opacity: 1 },
        "&:hover .mx-picked-badge, &:focus-within .mx-picked-badge": {
          opacity: 0,
        },
        "@media (hover: none)": {
          "& .mx-hover-only, & [data-run-id] [aria-label$=' menu']": {
            opacity: 1,
          },
        },
      })}
    >
      <Box
        sx={{
          height: HEADER_LINES.chip,
          display: "flex",
          minWidth: 0,
          containerType: "inline-size",
          containerName: "mxchip",
        }}
      >
        {/* The chip keeps its words (`#2B Baseline`); the version tag is
            the part that ellipsizes when the column is narrow. */}
        <Box sx={{ display: "flex", flexShrink: 0, maxWidth: "100%" }}>
          <RunChip
            run={run}
            color={color}
            isBaseline={isBaseline}
            roleText={roleNode(roleText)}
            showName={false}
            showStatus={false}
            showSource={false}
            menu={menu}
          />
        </Box>
        <Box sx={{ flex: 1 }} />
        {/* IT-R5 G6 P3: tick two run headers → `Compare these two`. */}
        {onPick ? (
          <CustomTooltip
            show
            size="small"
            placement="top"
            title={
              picked
                ? `Untick ${run?.label ?? "this run"}`
                : `Tick ${run?.label ?? "this run"} and one more run to compare them task by task`
            }
          >
            {/* Over the version tag (top right), so it never squeezes it. */}
            <Box
              component="span"
              className="mx-hover-only"
              sx={{
                position: "absolute",
                top: 4,
                right: onRemove ? 26 : 4,
                zIndex: 2,
                display: "inline-flex",
                borderRadius: 0.5,
                bgcolor: "background.paper",
              }}
            >
              <Checkbox
                size="small"
                checked={picked}
                onChange={onPick}
                inputProps={{
                  "aria-label": `Pick ${run?.label ?? "run"} to compare`,
                }}
                data-testid="matrix-pick-run"
                sx={{ p: 0.25, "& .MuiSvgIcon-root": { fontSize: 16 } }}
              />
            </Box>
          </CustomTooltip>
        ) : null}
        {/* Ticked: a small corner check (the box itself shows on hover),
            so it never covers the role tag. */}
        {onPick && picked ? (
          <Box
            component="span"
            aria-hidden
            className="mx-picked-badge"
            sx={{
              position: "absolute",
              top: 1,
              right: 1,
              zIndex: 1,
              display: "inline-flex",
              color: "primary.main",
              bgcolor: "background.paper",
              borderRadius: "50%",
            }}
          >
            <Iconify icon="mdi:check-circle" width={14} />
          </Box>
        ) : null}
        {versionTag ? (
          <CustomTooltip
            show
            size="small"
            placement="top"
            title={`${versionTag.name ?? ""} ${versionTag.text}${versionTag.role ? ` · ${versionTag.role}` : ""}`}
          >
            <Typography
              typography="s3"
              color={versionTag.role ? "text.primary" : "text.secondary"}
              noWrap
              data-testid="matrix-version-tag"
              sx={{
                minWidth: 0,
                alignSelf: "center",
                ml: 0.5,
                ...(isBaseline
                  ? {
                      [`@container mxchip (max-width: ${BASELINE_TAG_MIN_LINE}px)`]:
                        { display: "none" },
                    }
                  : {}),
              }}
            >
              {versionTag.role
                ? `${versionTag.text} · ${versionTag.role.split(" ")[0]}`
                : versionTag.text}
            </Typography>
          </CustomTooltip>
        ) : null}
        {onRemove ? (
          <CustomTooltip
            show
            size="small"
            placement="top"
            title={
              removeReason ??
              `Remove ${run?.label ?? "this run"} from this comparison`
            }
          >
            <span style={{ alignSelf: "center" }} className="mx-hover-only">
              <IconButton
                size="small"
                aria-label={removeReason ?? `Remove ${run?.label ?? "run"}`}
                disabled={Boolean(removeReason)}
                onClick={onRemove}
                data-testid="matrix-remove-run"
                sx={{ p: 0.25, ml: 0.25, color: "text.disabled" }}
              >
                <Iconify icon="mdi:close" width={14} />
              </IconButton>
            </span>
          </CustomTooltip>
        ) : null}
      </Box>
      {/* IT-R4 G6 P4: hover / focus the name for how the run ran. */}
      <CustomTooltip
        show={Boolean(fullName || how.lines.length)}
        size="small"
        placement="bottom-start"
        title={
          <Stack gap={0.25} data-testid="matrix-run-how" sx={{ maxWidth: 320 }}>
            <Typography typography="s3" fontWeight="fontWeightSemiBold">
              {fullName || how.title}
            </Typography>
            {how.lines.map((l) => (
              <Typography key={l.k} typography="s3">
                <Box component="span" sx={{ opacity: 0.75 }}>
                  {l.k}:
                </Box>{" "}
                {l.v}
              </Typography>
            ))}
            {population ? (
              <Typography typography="s3">
                <Box component="span" sx={{ opacity: 0.75 }}>
                  Success:
                </Box>{" "}
                {population}
              </Typography>
            ) : null}
            <Typography typography="s3" sx={{ opacity: 0.75, mt: 0.25 }}>
              {how.note}
            </Typography>
          </Stack>
        }
      >
        <Typography
          typography="s3"
          color="text.primary"
          fontWeight="fontWeightSemiBold"
          noWrap
          tabIndex={0}
          aria-label={`${name}: how it ran`}
          data-testid="matrix-run-name"
          sx={{
            height: HEADER_LINES.name,
            lineHeight: "16px",
            cursor: "help",
            outline: "none",
            "&:focus-visible": { textDecoration: "underline" },
          }}
        >
          {name}
        </Typography>
      </CustomTooltip>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{ height: HEADER_LINES.prov, minWidth: 0 }}
      >
        <Box
          sx={{
            minWidth: 0,
            flex: 1,
            display: "flex",
            "& .rp-line": { typography: "s3" },
          }}
        >
          {sameAs ? (
            // IT-R5 G6 P3: identical headers side by side show their
            // setup once.
            <CustomTooltip
              show
              size="small"
              placement="bottom-start"
              title={`Same model, framework and tools as ${sameAs}: ${fingerprint.text || models}`}
            >
              <Typography
                typography="s3"
                color="text.secondary"
                noWrap
                data-testid="matrix-same-setup"
              >
                {`Same as ${sameAs}`}
              </Typography>
            </CustomTooltip>
          ) : (
            <RunProvenance
              run={run}
              variant="line"
              text={fingerprint.text || models || undefined}
              interactive={false}
            />
          )}
        </Box>
        {/* IT-R1 G2 P10: no per-column `Preview` word; the run's card
            (click the line) says it once. */}
      </Stack>
      {metricRows({ k: stats.k, moreMetrics, costs }).map((m) => (
        <Typography
          key={m.key}
          sx={valueSx}
          fontWeight={m.strong ? "fontWeightSemiBold" : undefined}
        >
          {values[m.key]}
        </Typography>
      ))}
      <Stack
        direction="row"
        justifyContent="flex-end"
        alignItems="center"
        gap={0.25}
        sx={{ height: HEADER_LINES.delta, minWidth: 0 }}
      >
        {voice?.textOnly ? (
          <CustomTooltip
            show
            size="small"
            placement="bottom"
            title="Text only · no audio, so no latency, interruptions or WER. Compared with voice runs on task completion only."
          >
            <Chip
              size="small"
              label="Text only"
              data-testid="text-only-chip"
              sx={{
                height: 16,
                mr: "auto",
                typography: "s3",
                "& .MuiChip-label": { px: 0.5 },
              }}
            />
          </CustomTooltip>
        ) : regs.length ? (
          <CustomTooltip
            show
            size="small"
            placement="bottom"
            title={
              <Stack gap={0.25}>
                <Typography typography="s3" fontWeight="fontWeightSemiBold">
                  {`Moved more than ${fmtPct(REGRESSION_THRESHOLD, { dp: 0 })} the wrong way vs ${baselineLabel}`}
                </Typography>
                {regs.map((r) => (
                  <Typography key={r.key} typography="s3">
                    {r.text}
                  </Typography>
                ))}
              </Stack>
            }
          >
            <Typography
              typography="s3"
              color="error.main"
              fontWeight="fontWeightMedium"
              data-testid="regression-badge"
              noWrap
              sx={{ mr: "auto", minWidth: 0 }}
            >
              {regressionBadgeText(regs)}
            </Typography>
          </CustomTooltip>
        ) : null}
        {baselineLabel && !isBaseline ? (
          <>
            <DeltaPair
              better={stats.better}
              worse={stats.worse}
              active={activeDelta}
              onDelta={onDelta}
              upTip={`Show the ${stats.better} ${stats.better === 1 ? "task" : "tasks"} where ${stats.label ?? "this run"} passes more tries than ${baselineLabel}`}
              downTip={`Show the ${stats.worse} ${stats.worse === 1 ? "task" : "tasks"} where ${stats.label ?? "this run"} passes fewer tries than ${baselineLabel}`}
            />
          </>
        ) : null}
        {note ? (
          <CustomTooltip show size="small" placement="bottom" title={note}>
            <Box
              component="span"
              aria-label={note}
              data-testid="matrix-header-note"
              sx={{ display: "inline-flex", color: "warning.main", ml: 0.25 }}
            >
              <Iconify icon="mdi:information-outline" width={14} />
            </Box>
          </CustomTooltip>
        ) : null}
      </Stack>
    </Stack>
  );
}

MatrixColumnHeader.propTypes = {
  costs: PropTypes.bool,
  run: PropTypes.object,
  stats: PropTypes.object.isRequired,
  color: PropTypes.object,
  isBaseline: PropTypes.bool,
  baselineLabel: PropTypes.string,
  menu: PropTypes.array,
  activeDelta: PropTypes.string,
  onDelta: PropTypes.func,
  highlighted: PropTypes.bool,
  moreMetrics: PropTypes.bool,
  // `{regressions, textOnly}` on voice data; 8a version tag and
  // the lane `×` (baseline: disabled with the reason).
  voice: PropTypes.object,
  versionTag: PropTypes.object,
  onRemove: PropTypes.func,
  removeReason: PropTypes.string,
  // IT-R4 G6 P1 (rule 30): `Baseline · current` / `New` / `Candidate`.
  roleText: PropTypes.string,
  // IT-R5 G6 P3: the header's pick box (two ticked → Compare these two).
  picked: PropTypes.bool,
  onPick: PropTypes.func,
  // IT-R5 G6 P3: the label of the identical header on the left.
  sameAs: PropTypes.string,
};
