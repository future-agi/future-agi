import React, { useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  Box,
  Chip,
  IconButton,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import DeltaBadge from "src/sections/common/Metrics/DeltaBadge";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import MovementButtons from "src/sections/common/Metrics/MovementButtons";
import {
  formatValue,
  formatCi,
} from "src/sections/common/Metrics/metricFormat";
import AnalysisCard from "./AnalysisCard";
import MiniHistogram from "./MiniHistogram";

const tabular = { fontVariantNumeric: "tabular-nums" };

export const KIND_LABEL = {
  pass_fail: "Pass/Fail",
  score: "Score",
  thumbs: "Human",
  stars: "Human",
  pairwise: "Preference",
  ms: "Ops",
  usd: "Ops",
  count: "Ops",
};

/** `↑ Higher is better · Pass ≥ 0.70 · v3`, the metric's grey definition line. */
export function metricDefinitionText(metric) {
  const parts = [];
  if (metric.higher_is_better != null)
    parts.push(
      metric.higher_is_better ? "↑ Higher is better" : "↓ Lower is better",
    );
  if (metric.output_type === "score" && metric.pass_threshold != null)
    parts.push(
      `Pass ${metric.higher_is_better === false ? "≤" : "≥"} ${Number(metric.pass_threshold).toFixed(2)}`,
    );
  if (metric.version) parts.push(metric.version);
  return parts.join(" · ");
}

export const aggOf = (metric) =>
  metric.output_type === "pass_fail" || metric.output_type === "thumbs"
    ? "pass_rate"
    : "avg";

/** One run's line: bar · #4A · value · ±ci · n · errors · delta · ↑↓. */
function RunLine({ metric, run, color, stats, vs, baselineLabel, links }) {
  const navigate = useNavigate();
  const agg = aggOf(metric);
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ minHeight: 26, flexWrap: "wrap", rowGap: 0.25 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ width: 64, flexShrink: 0 }}
      >
        <Box
          sx={{ width: 3, height: 16, borderRadius: 1, bgcolor: color?.solid }}
        />
        <Typography typography="s2" fontWeight={600} sx={tabular}>
          {run.label}
        </Typography>
      </Stack>
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={
          stats?.ci95 != null
            ? `95% CI ${formatCi(metric, stats.ci95)} · ${stats.n} rows${stats.partial ? ` · Partial · ${stats.done} of ${stats.total} rows` : ""}`
            : `${stats?.n ?? 0} rows`
        }
      >
        <Typography
          typography="s1"
          fontWeight={600}
          color={stats?.partial ? "text.disabled" : "text.primary"}
          sx={{ minWidth: 52, ...tabular }}
        >
          {formatValue(metric, stats?.value, { agg })}
        </Typography>
      </CustomTooltip>
      <Typography
        typography="s3"
        color={stats?.low_sample ? "text.disabled" : "text.secondary"}
        sx={tabular}
      >
        n={stats?.n ?? 0}
      </Typography>
      {vs && (
        <>
          <DeltaBadge
            def={metric}
            delta={vs.delta}
            ci95={vs.ci95}
            significant={vs.significant}
            n={vs.n_shared}
            vsLabel={baselineLabel}
            partial={stats?.partial}
          />
          <MovementButtons
            improved={vs.improved}
            regressed={vs.regressed}
            unchanged={vs.unchanged}
            notComparable={vs.not_comparable}
            onSelect={(dir) =>
              navigate(links.movement(metric.key, dir, run.run_id))
            }
          />
        </>
      )}
      <Box sx={{ flex: 1 }} />
      {stats?.errors > 0 && (
        <Link
          component={RouterLink}
          to={links.errors(run.run_id)}
          typography="s3"
          color="error.main"
          underline="hover"
          sx={{ whiteSpace: "nowrap" }}
        >
          {stats.errors} error{stats.errors === 1 ? "" : "s"}
        </Link>
      )}
      {!vs && run.is_baseline && (
        <Typography typography="s3" color="text.disabled">
          Baseline
        </Typography>
      )}
    </Stack>
  );
}

RunLine.propTypes = {
  metric: PropTypes.object.isRequired,
  run: PropTypes.object.isRequired,
  color: PropTypes.object,
  stats: PropTypes.object,
  vs: PropTypes.object,
  baselineLabel: PropTypes.string,
  links: PropTypes.object.isRequired,
};

/**
 * MetricCard — one eval on the Summary (PRODUCT-SPEC §3.13 M8.3): header
 * (name, direction, kind, version, ⋮); per run value, n, errors and, for
 * candidates, the delta and `↑14 ↓7` (each opens the grid filtered to those
 * rows); the 10-bin histogram with the threshold; "N rows below threshold on
 * #4A → View rows"; and with trials `×3 · pass@3 … · 6 flaky rows`.
 *
 * links: { movement(metric, dir, runId), fails(metric, runId), errors(runId),
 *          sort(metric, runId), grid() } -> href
 */
const MetricCard = ({
  metric,
  runs,
  colors,
  baselineId,
  baselineLabel,
  candidateId,
  links,
  onRescore,
}) => {
  const [menuEl, setMenuEl] = useState(null);
  const navigate = useNavigate();
  const statsOf = (id) => metric.per_run?.find((p) => p.run_id === id);
  const vsOf = (id) => metric.vs_baseline?.find((v) => v.run_id === id);
  const focus =
    runs.find((r) => r.run_id === candidateId) ?? runs[runs.length - 1];
  const focusStats = statsOf(focus?.run_id);
  const isPassFail = metric.output_type === "pass_fail";
  // External runs waiting for an upload have no scores: no empty charts.
  const scoredAny = runs.some((r) => (statsOf(r.run_id)?.n ?? 0) > 0);
  const threshold = isPassFail ? null : metric.pass_threshold;
  const versions = Object.values(metric.versions ?? {}).filter(Boolean);
  const mixed = metric.mixed_versions;
  const trialRun = runs.find((r) => statsOf(r.run_id)?.trials);
  const trials = trialRun ? statsOf(trialRun.run_id).trials : null;
  const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);

  const header = (
    <Stack direction="row" alignItems="center" gap={0.75}>
      <DirectionIcon higherIsBetter={metric.higher_is_better} />
      <Chip
        size="small"
        label={KIND_LABEL[metric.output_type] ?? "Eval"}
        sx={{
          height: 20,
          typography: "s3",
          bgcolor: "action.hover",
          color: "text.secondary",
        }}
      />
      {versions.length > 0 && (
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={
            mixed
              ? `Scores come from different eval versions: ${runs
                  .map((r) => `${r.label} ${metric.versions[r.run_id]}`)
                  .join(", ")}. Re-score to compare like with like.`
              : `Eval version ${versions[0]}`
          }
        >
          <Chip
            size="small"
            label={
              mixed ? [...new Set(versions)].sort().join(" + ") : versions[0]
            }
            sx={(theme) => ({
              height: 20,
              typography: "s3",
              bgcolor: mixed
                ? theme.palette.amber?.[50] ?? "warning.lighter"
                : "action.hover",
              color: mixed
                ? theme.palette.amber?.[800] ?? "warning.dark"
                : "text.secondary",
            })}
          />
        </CustomTooltip>
      )}
    </Stack>
  );

  return (
    <AnalysisCard
      id={`metric-${metric.key}`}
      icon="material-symbols:check-circle-outline"
      title={metric.name}
      titleAdornment={header}
      actions={
        <>
          <IconButton
            size="small"
            aria-label={`${metric.name} actions`}
            onClick={(e) => setMenuEl(e.currentTarget)}
          >
            <Iconify icon="mdi:dots-vertical" width={18} />
          </IconButton>
          <Menu
            anchorEl={menuEl}
            open={Boolean(menuEl)}
            onClose={() => setMenuEl(null)}
          >
            <MenuItem
              onClick={() => {
                setMenuEl(null);
                navigate(links.grid());
              }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:table" width={18} />
              </ListItemIcon>
              <ListItemText primaryTypographyProps={{ typography: "s1" }}>
                Open rows in grid
              </ListItemText>
            </MenuItem>
            {baselineId && focus && focus.run_id !== baselineId && (
              <MenuItem
                onClick={() => {
                  setMenuEl(null);
                  navigate(links.sort(metric.key, focus.run_id));
                }}
              >
                <ListItemIcon>
                  <Iconify icon="mdi:sort-ascending" width={18} />
                </ListItemIcon>
                <ListItemText primaryTypographyProps={{ typography: "s1" }}>
                  Sort grid by change vs {baselineLabel}
                </ListItemText>
              </MenuItem>
            )}
            {mixed && onRescore && (
              <MenuItem
                onClick={() => {
                  setMenuEl(null);
                  onRescore(metric);
                }}
              >
                <ListItemIcon>
                  <Iconify icon="mdi:refresh" width={18} />
                </ListItemIcon>
                <ListItemText primaryTypographyProps={{ typography: "s1" }}>
                  Re-score on {[...new Set(versions)].sort().pop()}
                </ListItemText>
              </MenuItem>
            )}
          </Menu>
        </>
      }
      subtitle={metricDefinitionText({ ...metric, version: null }) || null}
    >
      <Stack gap={0.25}>
        {runs.map((run) => (
          <RunLine
            key={run.run_id}
            metric={metric}
            run={run}
            color={colors?.[run.run_id]}
            stats={statsOf(run.run_id)}
            vs={run.run_id !== baselineId ? vsOf(run.run_id) : null}
            baselineLabel={baselineLabel}
            links={links}
          />
        ))}
      </Stack>
      {!scoredAny ? (
        <Typography typography="s2" color="text.secondary" sx={{ mt: 1.25 }}>
          No scores yet. The distribution appears when results arrive.
        </Typography>
      ) : (
        <Box sx={{ mt: 1.25 }}>
          <MiniHistogram
            histogram={metric.histogram}
            runs={runs}
            colors={colors}
            threshold={threshold ?? undefined}
            thresholdLabel={
              threshold != null
                ? `Pass ${metric.higher_is_better === false ? "≤" : "≥"} ${threshold.toFixed(2)}`
                : undefined
            }
            formatX={(v) => Number(v).toFixed(1)}
          />
        </Box>
      )}
      <Stack gap={0.5} sx={{ mt: 1 }}>
        {scoredAny && focusStats?.below_threshold != null && focus && (
          <Typography typography="s2" color="text.secondary">
            {focusStats.below_threshold}{" "}
            {isPassFail
              ? "rows failing"
              : `rows below ${threshold?.toFixed(2)}`}{" "}
            on {focus.label}
            {focusStats.below_threshold > 0 && (
              <>
                {" · "}
                <Link
                  component={RouterLink}
                  to={links.fails(metric.key, focus.run_id)}
                  underline="hover"
                  typography="s2"
                >
                  View rows
                </Link>
              </>
            )}
          </Typography>
        )}
        {trials && (
          <Typography typography="s2" color="text.secondary" sx={tabular}>
            {trialRun.label} ×{trials.k} trials · pass@{trials.k}{" "}
            {pct(trials.pass_at_k)} · pass^{trials.k} {pct(trials.pass_all_k)} ·{" "}
            <CustomTooltip
              show
              size="small"
              placement="top"
              arrow
              title={`Rows whose ${trials.k} trials disagree on ${metric.name}. The Trials column of All runs opens every flaky row.`}
            >
              <span>
                {trials.flaky} flaky row{trials.flaky === 1 ? "" : "s"}
              </span>
            </CustomTooltip>
          </Typography>
        )}
      </Stack>
    </AnalysisCard>
  );
};

MetricCard.propTypes = {
  metric: PropTypes.object.isRequired,
  runs: PropTypes.array.isRequired,
  colors: PropTypes.object,
  baselineId: PropTypes.string,
  baselineLabel: PropTypes.string,
  candidateId: PropTypes.string,
  links: PropTypes.object.isRequired,
  onRescore: PropTypes.func,
};

export default MetricCard;
