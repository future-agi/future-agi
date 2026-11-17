// Cell renderers of the leaderboard's plain columns. Run rows show the run;
// experiment group rows follow the §3.7 group rules (lowest latency with
// its letter; sums for cost, tokens and errors; the experiment's version,
// trials and created time); other group-bys show means prefixed `≈`.
import React from "react";
import { causesText } from "src/sections/common/Runs/errorCause";
import PropTypes from "prop-types";
import {
  Avatar,
  Box,
  ButtonBase,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import VersionChip from "src/sections/common/Datasets/VersionChip";
import SplitChips from "src/sections/common/Datasets/SplitChips";
import {
  EMPTY_VALUE,
  TONE_COLOR,
  deltaTone,
  formatDelta,
  formatValue,
} from "src/sections/common/Metrics/metricFormat";
import ExperimentNameCell from "./ExperimentNameCell";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import {
  TARGET_ICON,
  TARGET_LABEL,
  aggValueOf,
  compareUrl,
  costOf,
  errorsOf,
  examplesText,
  experimentUrl,
  fastestRun,
  latencyP50,
  latencyP99,
  meanValue,
  sumValue,
  targetMix,
  tokensOf,
} from "./leaderboardModel";
import { useCell } from "./cellContext";
import { nestWordsOfRoot } from "./attemptsModel";
import { BestMark } from "./LeaderboardSummaryCells";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const MS = { key: "ops:latency", output_type: "ms", higher_is_better: false };
const USD = { key: "ops:cost", output_type: "usd", higher_is_better: false };

const leafRuns = (node) =>
  (node.allLeafChildren ?? []).map((n) => n.data).filter(Boolean);

const Cell = ({ children, gap = 0.75 }) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={gap}
    sx={{ height: "100%", minWidth: 0 }}
  >
    {children}
  </Stack>
);

Cell.propTypes = { children: PropTypes.node, gap: PropTypes.number };

const Text = ({ children, color = "text.primary", title, strong = false }) => (
  <CustomTooltip
    show={!!title}
    title={title}
    size="small"
    placement="top"
    arrow
  >
    <Typography
      component="span"
      typography="s1"
      color={color}
      fontWeight={strong ? "fontWeightMedium" : "fontWeightRegular"}
      noWrap
      sx={{ fontVariantNumeric: "tabular-nums", minWidth: 0 }}
    >
      {children}
    </Typography>
  </CustomTooltip>
);

Text.propTypes = {
  children: PropTypes.node,
  color: PropTypes.oneOfType([PropTypes.string, PropTypes.func]),
  title: PropTypes.node,
  strong: PropTypes.bool,
};

/** `+0.5 s` / `−12%` coloured by direction (no significance: display only). */
// Ops values move when they change by 10% or more (C4); smaller changes
// show no delta, so the column only flags real moves.
const PlainDelta = ({ def, delta, base, title }) => {
  if (!isNum(delta) || delta === 0) return null;
  if (isNum(base) && base !== 0 && Math.abs(delta / base) < 0.1) return null;
  const tone = deltaTone(def, delta);
  return (
    <Text color={TONE_COLOR[tone]} title={title}>
      {formatDelta(def, delta, { base })}
    </Text>
  );
};

PlainDelta.propTypes = {
  def: PropTypes.object,
  delta: PropTypes.number,
  base: PropTypes.number,
  title: PropTypes.string,
};

const approx = (context) => context.group !== "experiment";

// ---------------------------------------------------------------- target
export const TargetCell = (params) => {
  const { node, data } = useCell(params);
  if (node.group)
    return (
      <Cell>
        <Text color="text.secondary">{targetMix(leafRuns(node))}</Text>
      </Cell>
    );
  if (!data) return null;
  return (
    <Cell gap={0.5}>
      <Iconify
        icon={TARGET_ICON[data.target_type] ?? "mdi:help-circle-outline"}
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Text>{TARGET_LABEL[data.target_type] ?? data.target_type}</Text>
    </Cell>
  );
};

// ---------------------------------------------------------------- dataset
const DatasetValue = ({ run }) => (
  <Cell gap={0.5}>
    <VersionChip
      version={{
        number: run.dataset_version?.number,
        tag: run.dataset_version?.tag,
        is_latest: run.dataset_version?.is_latest,
      }}
      staleInfo={run.stale_info ?? undefined}
    />
    {run.splits?.length ? (
      <SplitChips splits={run.splits} max={2} />
    ) : (
      <Typography typography="s3" color="text.secondary" noWrap>
        All rows
      </Typography>
    )}
  </Cell>
);

DatasetValue.propTypes = { run: PropTypes.object.isRequired };

export const DatasetCell = (params) => {
  const { node, data, context } = useCell(params);
  if (node.group) {
    const runs = leafRuns(node);
    const versions = new Set(runs.map((r) => r.dataset_version?.number));
    if (context.group === "experiment" || versions.size === 1)
      return runs[0] ? <DatasetValue run={runs[0]} /> : null;
    return (
      <Cell>
        <Text color="text.secondary">{versions.size} versions</Text>
      </Cell>
    );
  }
  return data ? <DatasetValue run={data} /> : null;
};

// ---------------------------------------------------------------- latency
const latencyCell = (getter, label) => {
  const LatencyCell = (params) => {
    const { node, data, context } = useCell(params);
    const base = context.baselineRun ? getter(context.baselineRun) : null;
    if (node.group) {
      const runs = leafRuns(node);
      if (approx(context)) {
        const mean = meanValue(runs, getter);
        return (
          <Cell>
            <Text
              color={isNum(mean) ? "text.primary" : "text.disabled"}
              title="Group averages are not paired per example"
            >
              {isNum(mean) ? `≈ ${formatValue(MS, mean * 1000)}` : EMPTY_VALUE}
            </Text>
          </Cell>
        );
      }
      const best = fastestRun(runs);
      const v = best ? getter(best) : null;
      if (!isNum(v)) return <Text color="text.disabled">{EMPTY_VALUE}</Text>;
      return (
        <Cell gap={0.5}>
          <Text title={`Lowest ${label}: ${best.label}`}>
            {formatValue(MS, v * 1000)}
          </Text>
          <BestMark run={best} word="fastest" iconOnly />
        </Cell>
      );
    }
    if (!data) return null;
    const v = getter(data);
    if (!isNum(v))
      return (
        <Cell>
          <Text color="text.disabled" title="No completed rows yet">
            {EMPTY_VALUE}
          </Text>
        </Cell>
      );
    const showDelta =
      context.showDeltas && data.run_id !== context.baselineId && isNum(base);
    return (
      <Cell>
        <Text>{formatValue(MS, v * 1000)}</Text>
        {showDelta && (
          <PlainDelta
            def={MS}
            delta={(v - base) * 1000}
            base={base * 1000}
            title={`${label} vs ${context.baselineLabel} (${formatValue(MS, base * 1000)}). Lower is better.`}
          />
        )}
      </Cell>
    );
  };
  LatencyCell.propTypes = {
    node: PropTypes.object,
    data: PropTypes.object,
    context: PropTypes.object,
  };
  return LatencyCell;
};
export const LatencyP50Cell = latencyCell(latencyP50, "p50 latency");
export const LatencyP99Cell = latencyCell(latencyP99, "p99 latency");

// ---------------------------------------------------------------- cost
const perExample = (run) => {
  const c = costOf(run);
  const n = run?.progress?.done;
  return isNum(c) && n ? c / n : null;
};

export const CostCell = (params) => {
  const { node, data, context } = useCell(params);
  if (node.group) {
    const runs = leafRuns(node);
    const total = approx(context)
      ? meanValue(runs, costOf)
      : sumValue(runs, costOf);
    return (
      <Cell>
        <Text
          color={isNum(total) ? "text.primary" : "text.disabled"}
          title={
            approx(context)
              ? "Group averages are not paired per example"
              : `Total of ${runs.length} runs`
          }
        >
          {isNum(total)
            ? `${approx(context) ? "≈ " : ""}${formatValue(USD, total)}`
            : EMPTY_VALUE}
        </Text>
      </Cell>
    );
  }
  if (!data) return null;
  const c = costOf(data);
  if (!isNum(c) || !data.progress?.done)
    return (
      <Cell>
        <Text color="text.disabled">{EMPTY_VALUE}</Text>
      </Cell>
    );
  const per = perExample(data);
  const basePer = context.baselineRun ? perExample(context.baselineRun) : null;
  const tip = [
    isNum(per) ? `${formatValue(USD, per)} per example` : null,
    data.metrics?.tokens
      ? `${(data.metrics.tokens.prompt ?? 0).toLocaleString("en-US")} prompt · ${(data.metrics.tokens.completion ?? 0).toLocaleString("en-US")} completion tokens`
      : null,
    c === 0 && data.target_type === "http"
      ? "The endpoint reports no token cost"
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const showDelta =
    context.showDeltas &&
    data.run_id !== context.baselineId &&
    isNum(per) &&
    isNum(basePer) &&
    basePer > 0 &&
    per > 0;
  return (
    <Cell>
      <Text title={tip}>{formatValue(USD, c)}</Text>
      {showDelta && (
        <PlainDelta
          def={USD}
          delta={per - basePer}
          base={basePer}
          title={`Cost per example vs ${context.baselineLabel}`}
        />
      )}
    </Cell>
  );
};

// ---------------------------------------------------------------- tokens
const compact = (n) =>
  n >= 1e6
    ? `${(n / 1e6).toFixed(1)}M`
    : n >= 1e3
      ? `${(n / 1e3).toFixed(1)}k`
      : String(n);

export const TokensCell = (params) => {
  const { node, data, context } = useCell(params);
  const runs = node.group ? leafRuns(node) : data ? [data] : [];
  const total = node.group
    ? approx(context)
      ? meanValue(runs, tokensOf)
      : sumValue(runs, tokensOf)
    : tokensOf(data);
  if (!isNum(total) || total === 0)
    return (
      <Cell>
        <Text
          color="text.disabled"
          title={
            data?.target_type === "http" || data?.target_type === "external"
              ? "This target reports no token usage"
              : undefined
          }
        >
          {EMPTY_VALUE}
        </Text>
      </Cell>
    );
  const t = !node.group ? data.metrics?.tokens : null;
  return (
    <Cell>
      <Text
        title={
          t
            ? `${t.prompt.toLocaleString("en-US")} prompt · ${t.completion.toLocaleString("en-US")} completion`
            : `${Math.round(total).toLocaleString("en-US")} tokens`
        }
      >
        {node.group && approx(context) ? "≈ " : ""}
        {compact(Math.round(total))}
      </Text>
    </Cell>
  );
};

// ---------------------------------------------------------------- errors
export const ErrorsCell = (params) => {
  const { node, data, context } = useCell(params);
  const count = node.group
    ? sumValue(leafRuns(node), errorsOf)
    : errorsOf(data);
  if (!isNum(count) || count === 0)
    return (
      <Cell>
        <Text color="text.disabled">0</Text>
      </Cell>
    );
  if (node.group)
    return (
      <Cell>
        <Text color="error.main" strong>
          {count}
        </Text>
      </Cell>
    );
  const to = experimentUrl(context.datasetId, data.experiment_id, {
    runId: data.run_id,
    pills: [{ type: "error", run: data.run_id }],
  });
  return (
    <Cell>
      <CustomTooltip
        show
        title={`${count} ${count === 1 ? "row" : "rows"} errored${
          // W1-7: by cause (errorCause), else the first error's words.
          data.errors_by_cause?.length
            ? `: ${causesText(data.errors_by_cause)}`
            : data.first_error
              ? ` · ${data.first_error}`
              : ""
        }. Open them.`}
        size="small"
        placement="top"
        arrow
      >
        <Link
          component={RouterLink}
          to={to}
          data-row-link
          onClick={(e) => e.stopPropagation()}
          underline="none"
          sx={{
            px: 0.75,
            borderRadius: "10px",
            bgcolor: "error.main",
            color: "common.white",
            typography: "s3",
            fontWeight: 600,
            lineHeight: "18px",
          }}
        >
          {count}
        </Link>
      </CustomTooltip>
    </Cell>
  );
};

// ---------------------------------------------------------------- trials
export const TrialsCell = (params) => {
  const { node, data, context } = useCell(params);
  const run = node.group ? leafRuns(node)[0] : data;
  if (!run) return null;
  const trials = run.trials ?? 1;
  const flaky = node.group
    ? sumValue(leafRuns(node), (r) => r.metrics?.flaky)
    : run.metrics?.flaky;
  if (!flaky || node.group)
    return (
      <Cell>
        <Text color={trials > 1 ? "text.primary" : "text.secondary"}>
          ×{trials}
          {flaky ? ` · ${flaky} flaky` : ""}
        </Text>
      </Cell>
    );
  const to = experimentUrl(context.datasetId, run.experiment_id, {
    runId: run.run_id,
    pills: [{ type: "flaky", run: run.run_id }],
  });
  return (
    <Cell>
      <CustomTooltip
        show
        title={`${flaky} rows gave different results across the ${trials} trials. Open them.`}
        size="small"
        placement="top"
        arrow
      >
        <Link
          component={RouterLink}
          to={to}
          data-row-link
          onClick={(e) => e.stopPropagation()}
          underline="hover"
          typography="s1"
          sx={{ color: "warning.dark", fontVariantNumeric: "tabular-nums" }}
        >
          ×{trials} · {flaky} flaky
        </Link>
      </CustomTooltip>
    </Cell>
  );
};

// ---------------------------------------------------------------- examples
export const ExamplesCell = (params) => {
  const { node, data } = useCell(params);
  const run = node.group ? leafRuns(node)[0] : data;
  const text = run ? examplesText(run) : null;
  return (
    <Cell>
      <Text color={text ? "text.primary" : "text.disabled"}>
        {text ?? EMPTY_VALUE}
      </Text>
    </Cell>
  );
};

// ---------------------------------------------------------------- human
export const HumanCell = (params) => {
  const { node, data } = useCell(params);
  const { colDef } = params;
  const def = colDef?.cellRendererParams?.def;
  const runs = node.group ? leafRuns(node) : data ? [data] : [];
  const scored = runs.filter((r) => isNum(r.metrics?.human?.[def?.key]?.avg));
  if (!scored.length)
    return (
      <Cell>
        <Text color="text.disabled" title="No human scores on this run">
          {EMPTY_VALUE}
        </Text>
      </Cell>
    );
  const best = scored.reduce((a, b) =>
    b.metrics.human[def.key].avg > a.metrics.human[def.key].avg ? b : a,
  );
  const h = best.metrics.human[def.key];
  return (
    <Cell gap={0.5}>
      <Text title={`${def.name} · average of ${h.n} reviews by people`}>
        {formatValue(def, h.avg)}
      </Text>
      {node.group && <BestMark run={best} iconOnly />}
      {!node.group && (
        <Typography typography="s3" color="text.disabled">
          n={h.n}
        </Typography>
      )}
    </Cell>
  );
};

const pct = (v) => `${Math.round(v * 1000) / 10}%`;

export const WinRateCell = (params) => {
  const { node, data } = useCell(params);
  const runs = node.group ? leafRuns(node) : data ? [data] : [];
  const p = runs.map((r) => r.metrics?.preference).find(Boolean);
  if (!p)
    return (
      <Cell>
        <Text color="text.disabled" title="No pairwise review for this run">
          {EMPTY_VALUE}
        </Text>
      </Cell>
    );
  return (
    <Cell gap={0.5}>
      <Text
        strong
        title={`W${p.wins} · T${p.ties} · L${p.losses} over ${p.judged} reviewed rows · 95% CI ${pct(p.ci[0])}–${pct(p.ci[1])}`}
      >
        {pct(p.win_rate)}
      </Text>
      <Typography typography="s3" color="text.secondary" noWrap>
        vs {p.vs_label}
      </Typography>
    </Cell>
  );
};

// ---------------------------------------------------------------- people, time, text
export const CreatedByCell = (params) => {
  const { node, data } = useCell(params);
  const run = node.group ? leafRuns(node)[0] : data;
  const who = run?.created_by;
  if (!who) return null;
  const ci = who.kind === "ci";
  return (
    <Cell gap={0.75}>
      <Avatar
        sx={{
          width: 20,
          height: 20,
          fontSize: 10,
          bgcolor: ci ? "action.selected" : "primary.main",
          color: ci ? "text.primary" : "common.white",
        }}
      >
        {ci ? (
          <Iconify icon="mdi:robot-outline" width={13} />
        ) : (
          who.name
            .split(" ")
            .map((w) => w[0])
            .join("")
            .slice(0, 2)
        )}
      </Avatar>
      <Text>{who.name}</Text>
    </Cell>
  );
};

export const CreatedCell = (params) => {
  const { node, data } = useCell(params);
  const run = node.group ? leafRuns(node)[0] : data;
  if (!run?.created_at) return null;
  // EL#32: `2h ago`, absolute time + timezone + who on hover.
  return (
    <Cell>
      <RelativeTime
        iso={run.created_at}
        by={run.created_by?.name}
        sx={{ typography: "s1", color: "text.secondary", whiteSpace: "nowrap" }}
      />
    </Cell>
  );
};

export const DescriptionCell = (params) => {
  const { node, data } = useCell(params);
  const run = node.group ? leafRuns(node)[0] : data;
  const text = run?.experiment_description;
  return (
    <Cell>
      <Text color={text ? "text.secondary" : "text.disabled"} title={text}>
        {text || EMPTY_VALUE}
      </Text>
    </Cell>
  );
};

export const MetaCell = (params) => {
  const { node, data } = useCell(params);
  const { colDef } = params;
  const key = colDef?.cellRendererParams?.metaKey;
  const run = node.group ? leafRuns(node)[0] : data;
  const v = run?.metadata?.[key];
  if (v == null || v === "")
    return (
      <Cell>
        <Text color="text.disabled">{EMPTY_VALUE}</Text>
      </Cell>
    );
  const text = String(v);
  if (/^https?:\/\//.test(text))
    return (
      <Cell>
        <Link
          href={text}
          target="_blank"
          rel="noopener noreferrer"
          data-row-link
          onClick={(e) => e.stopPropagation()}
          typography="s1"
          noWrap
        >
          {text.replace(/^https?:\/\/(www\.)?/, "")}
        </Link>
      </Cell>
    );
  return (
    <Cell>
      <Text title={text}>{text}</Text>
    </Cell>
  );
};

const cellPropTypes = {
  node: PropTypes.object,
  data: PropTypes.object,
  context: PropTypes.object,
  colDef: PropTypes.object,
};
TargetCell.propTypes = cellPropTypes;
DatasetCell.propTypes = cellPropTypes;
CostCell.propTypes = cellPropTypes;
TokensCell.propTypes = cellPropTypes;
ErrorsCell.propTypes = cellPropTypes;
TrialsCell.propTypes = cellPropTypes;
ExamplesCell.propTypes = cellPropTypes;
HumanCell.propTypes = cellPropTypes;
WinRateCell.propTypes = cellPropTypes;
CreatedByCell.propTypes = cellPropTypes;
CreatedCell.propTypes = cellPropTypes;
DescriptionCell.propTypes = cellPropTypes;
MetaCell.propTypes = cellPropTypes;

// ------------------------------------------------------------ R2 (L2)

/**
 * The Experiment column with iteration nesting (R2-SPEC §4.3.4): a root run
 * gets `▸ 2 iterations` (collapsed by default; → / ← on the toggle) and
 * `latest iteration #2G 84.4%`; its iterations render indented 24 px.
 * Everything else is the plain ExperimentNameCell.
 */
export const IterationNameCell = (params) => {
  const { node, data, context } = useCell(params);
  const iter = context?.iter;
  const nest = Boolean(iter?.enabled) && !node.group && !node.rowPinned && data;
  if (!nest || (!data.__iterations && data.__depth !== 1))
    return <ExperimentNameCell {...params} />;
  const isRoot = Boolean(data.__iterations);
  const open = isRoot && iter.expanded.has(data.run_id);
  const latest = data.__latest;
  const def = iter.primaryDef;
  const value = latest && def ? aggValueOf(latest, def, context.agg) : null;
  const words = nestWordsOfRoot(data);
  const toggle = (want) => {
    if (want === undefined || want !== open) iter.toggle(data.run_id);
  };
  return (
    <Stack
      justifyContent="center"
      sx={{ minWidth: 0, width: "100%", height: "100%", pl: isRoot ? 0 : 3 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        sx={{ minWidth: 0, height: 24 }}
      >
        {!isRoot ? (
          <Iconify
            icon="mdi:subdirectory-arrow-right"
            width={14}
            sx={{ color: "text.disabled", mr: 0.5, flexShrink: 0 }}
          />
        ) : null}
        <Box sx={{ minWidth: 0, flex: 1, height: "100%", display: "flex" }}>
          <ExperimentNameCell {...params} />
        </Box>
      </Stack>
      {isRoot ? (
        <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
          <ButtonBase
            data-testid="iterations-toggle"
            data-row-link
            aria-expanded={open}
            aria-label={`${open ? "Hide" : "Show"} ${words.count}`}
            onClick={(e) => {
              e.stopPropagation();
              toggle();
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                e.preventDefault();
                e.stopPropagation();
                toggle(e.key === "ArrowRight");
              }
            }}
            sx={{
              typography: "s3",
              color: "text.secondary",
              borderRadius: 0.5,
              pr: 0.5,
              "&:hover, &:focus-visible": {
                color: "text.primary",
                bgcolor: "action.hover",
              },
            }}
          >
            <Iconify
              icon="mdi:chevron-right"
              width={16}
              sx={{
                transition: "transform 120ms",
                transform: open ? "rotate(90deg)" : "none",
              }}
            />
            {words.count}
          </ButtonBase>
          {latest ? (
            <CustomTooltip
              show
              title={`Compare ${latest.label} with ${data.label}`}
              size="small"
              arrow
            >
              <Link
                component={RouterLink}
                data-row-link
                to={compareUrl(context.datasetId, {
                  runIds: [data.run_id, latest.run_id],
                  baselineId: data.run_id,
                  from: context.fromUrl,
                })}
                onClick={(e) => e.stopPropagation()}
                underline="hover"
                typography="s3"
                color="text.secondary"
                noWrap
                sx={{ minWidth: 0 }}
              >
                {`${words.latest} ${latest.label}${value === null ? "" : ` ${formatValue(def, value)}`}`}
              </Link>
            </CustomTooltip>
          ) : null}
        </Stack>
      ) : null}
    </Stack>
  );
};
