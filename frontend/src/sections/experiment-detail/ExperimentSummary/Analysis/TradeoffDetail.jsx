import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Button, Chip, Link, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { formatDelta } from "src/sections/common/Metrics/metricFormat";
import { useMatrix } from "src/api/develop/r2";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { rowsPill } from "src/sections/common/Results/filterPills";
import {
  failedEvalNote,
  failedEvalSummary,
} from "src/sections/common/Analysis/workbench/workbenchStats";

// F3 locked names for the ops metrics inside the per-metric lines.
const F3_NAME = {
  "ops:latency": "time per task",
  "ops:cost": "cost per task",
};

const GROUPS = [
  { key: "better", label: "Better:", tone: "success" },
  { key: "worse", label: "Worse:", tone: "error" },
  { key: "tie", label: "No clear change:", tone: null },
];

// Dark primary is monochrome (brand guide): links take the brand accent so
// they still read as links (VerdictLine's rule).
const LINK_SX = {
  color: (t) =>
    t.palette.mode === "dark"
      ? t.palette.accent?.brand ?? t.palette.primary.light
      : t.palette.primary.main,
};

const CAVEAT_TIP = {
  low_sample: "Low sample: fewer than 30 comparable rows",
  version_drift:
    "Rows added after the baseline's dataset version have no baseline value; they are not counted.",
  errors: "Rows where a run errored on every trial. Open them in the grid.",
  evaluator_version:
    "Scores from different eval versions are not compared like with like.",
};

/**
 * The trade-offs of one run vs the baseline (one view A5-01: the old
 * VerdictPanel line, opened in place under its verdict line): `Better: time
 * per task −3.6 s, cost per task −$0.0070, steps −0.6 · Worse: Task reply
 * rubric v1 −32.5 pts · No clear change: Task success`, then its caveat
 * badges (low sample, version drift, errored rows, eval versions) and
 * `Re-score #2A on v2` (editors). Metric names open their Section card.
 */
export function TradeoffDetail({
  verdict,
  analysis,
  links,
  onScrollTo,
  onRescore,
  rescoring,
  id,
}) {
  const theme = useTheme();
  const metricByKey = Object.fromEntries(
    (analysis.metrics ?? []).map((m) => [m.key, m]),
  );
  const v = verdict;
  if (!v) return null;
  const label = analysis.runs?.find((r) => r.run_id === v.run_id)?.label;

  const name = (item) => (
    <Link
      component="button"
      type="button"
      underline="hover"
      color="inherit"
      onClick={() => onScrollTo(item.key === "pref" ? "human" : item.key)}
      sx={{
        typography: "inherit",
        fontWeight: 600,
        verticalAlign: "baseline",
      }}
    >
      {F3_NAME[item.key] ?? item.name}
    </Link>
  );
  const term = (item, withDelta) => {
    const m = metricByKey[item.key];
    const text = item.key === "pref" ? item.text : formatDelta(m, item.delta);
    return withDelta ? (
      <span key={item.key}>
        {name(item)}{" "}
        <Box component="span" sx={{ fontVariantNumeric: "tabular-nums" }}>
          {text}
        </Box>
      </span>
    ) : (
      <CustomTooltip
        key={item.key}
        show
        size="small"
        placement="top"
        arrow
        title={`${text}, within noise`}
      >
        <span>{name(item)}</span>
      </CustomTooltip>
    );
  };
  const list = (items, withDelta) =>
    items.flatMap((it, i) => [i ? ", " : null, term(it, withDelta)]);
  const groups = GROUPS.map((g) => {
    const items =
      g.key === "tie"
        ? [...(v.tie ?? [])].sort(
            (a, b) => Math.abs(b.delta ?? 0) - Math.abs(a.delta ?? 0),
          )
        : v[g.key] ?? [];
    return { ...g, items };
  }).filter((g) => g.items.length);

  return (
    <Stack
      id={id}
      gap={0.75}
      data-testid="verdict-tradeoffs"
      sx={{
        mt: 0.5,
        pl: 1,
        borderLeft: "2px solid",
        borderColor: "divider",
      }}
    >
      {v.suppressed ? (
        <Typography typography="s2" color="text.secondary">
          {label}: trade-offs are available when the runs finish or 30 rows are
          comparable.
        </Typography>
      ) : groups.length ? (
        <Typography typography="s2" color="text.primary">
          {groups.map((g, i) => (
            <React.Fragment key={g.key}>
              {i > 0 && (
                <Box component="span" sx={{ color: "text.disabled" }}>
                  {" · "}
                </Box>
              )}
              <Box
                component="span"
                sx={{
                  fontWeight: 600,
                  color: (t) =>
                    g.tone
                      ? t.palette.mode === "dark"
                        ? t.palette[g.tone].light
                        : t.palette[g.tone].dark
                      : t.palette.text.secondary,
                }}
              >
                {g.label}
              </Box>{" "}
              {list(g.items, g.key !== "tie")}
            </React.Fragment>
          ))}
        </Typography>
      ) : (
        <Typography typography="s2" color="text.secondary">
          No metric moved beyond noise.
        </Typography>
      )}
      {v.caveats?.length > 0 && (
        <Stack direction="row" gap={0.75} flexWrap="wrap">
          {v.caveats.map((c) => {
            const amber =
              c.kind === "version_drift" || c.kind === "evaluator_version";
            const chip = (
              <Chip
                size="small"
                icon={
                  <Iconify
                    icon={
                      c.kind === "errors"
                        ? "mdi:alert-circle-outline"
                        : c.kind === "low_sample"
                          ? "mdi:sigma"
                          : "mdi:alert-outline"
                    }
                    width={14}
                  />
                }
                label={c.text}
                component={c.kind === "errors" ? RouterLink : "div"}
                to={c.kind === "errors" ? links.pill(c.pill) : undefined}
                clickable={c.kind === "errors"}
                sx={{
                  height: 22,
                  typography: "s3",
                  fontWeight: 500,
                  color: amber
                    ? theme.palette.amber?.[800] ?? theme.palette.warning.dark
                    : c.kind === "errors"
                      ? theme.palette.red[700]
                      : "text.secondary",
                  bgcolor: amber
                    ? alpha(theme.palette.warning.main, 0.12)
                    : c.kind === "errors"
                      ? alpha(theme.palette.red[500], 0.1)
                      : "action.hover",
                  "& .MuiChip-icon": { color: "inherit" },
                }}
              />
            );
            return (
              <Stack
                key={`${c.kind}-${c.metric ?? ""}`}
                direction="row"
                gap={0.5}
                alignItems="center"
              >
                <CustomTooltip
                  show
                  size="small"
                  placement="top"
                  arrow
                  title={CAVEAT_TIP[c.kind] ?? CAVEAT_TIP.evaluator_version}
                >
                  {chip}
                </CustomTooltip>
                {c.rescore && onRescore && (
                  <Button
                    size="small"
                    variant="text"
                    disabled={rescoring}
                    onClick={() => onRescore(c.rescore)}
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      minWidth: 0,
                      px: 0.75,
                      height: 22,
                    }}
                  >
                    Re-score {c.rescore.run_label} on {c.rescore.version}
                  </Button>
                )}
              </Stack>
            );
          })}
        </Stack>
      )}
    </Stack>
  );
}

TradeoffDetail.propTypes = {
  verdict: PropTypes.object,
  analysis: PropTypes.object.isRequired,
  links: PropTypes.object.isRequired,
  onScrollTo: PropTypes.func.isRequired,
  onRescore: PropTypes.func,
  rescoring: PropTypes.bool,
  id: PropTypes.string,
};

/**
 * F1 for task suites (R2-SPEC §6.3.4), the Verdict card's footer: `Most
 * failed evals: order_found (18 attempts, all #2B) · all_requests_handled
 * (15) · …` — failed attempts by their first failed Check over the runs in
 * view; each name opens its rows.
 */
export function FailedEvalsLine({ analysis, links }) {
  const { data: datasets } = useDevelopDatasetList();
  const kind = (datasets ?? []).find(
    (d) => d.datasetId === analysis.dataset_id,
  )?.kind;
  const runIds = (analysis.runs ?? []).map((r) => r.run_id);
  const { data: matrix } = useMatrix(
    { datasetId: analysis.dataset_id, runIds },
    { enabled: kind === "task_suite" },
  );
  if (kind !== "task_suite" || !matrix) return null;
  const judges = new Set(
    (analysis.metrics ?? []).map((m) => m.key.replace(/^eval:/, "")),
  );
  const labels = Object.fromEntries(
    (analysis.runs ?? []).map((r) => [r.run_id, r.label]),
  );
  const top = failedEvalSummary(matrix, {
    isCheck: (k) => !judges.has(k),
    labels,
    // IT-R5 G6 P3: the top 2 (Compare Summary budget); the rest are in the
    // Data tab's eval filter.
  }).slice(0, 2);
  if (!top.length) return null;
  return (
    <Typography
      typography="s2"
      color="text.secondary"
      data-testid="verdict-failed-evals"
    >
      Most failed evals:{" "}
      {top.map((t, i) => (
        <React.Fragment key={t.key}>
          {i > 0 && " · "}
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title={`${t.attempts} failed attempts on ${t.rowIds.length} rows · open the rows`}
          >
            <Link
              component={RouterLink}
              to={links.pill(
                rowsPill(t.rowIds, { label: `Failed eval ${t.key}` }),
              )}
              underline="hover"
              sx={LINK_SX}
            >
              {t.key}
            </Link>
          </CustomTooltip>{" "}
          ({failedEvalNote(t, i === 0)})
        </React.Fragment>
      ))}
    </Typography>
  );
}

FailedEvalsLine.propTypes = {
  analysis: PropTypes.object.isRequired,
  links: PropTypes.object.isRequired,
};
