import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  ButtonBase,
  IconButton,
  Slider,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";
import SideDrawer from "../components/SideDrawer";
import { runIdentityOf } from "../_mock/runIdentity";
import { envLabelOf } from "../_mock/envVersionKinds";
import { currentEnvSnapshot } from "../_mock/envSnapshot";
import {
  allMetrics,
  WEIGHT_PRESETS,
  presetWeights,
  defaultWeights,
  rankRuns,
  winningMargins,
  comparableRuns,
} from "../_mock/winner";
import { causeCountsLine, runRollupOf } from "../_mock/runRollup";

/**
 * What "winning" means, before anything is declared the winner.
 *
 * The runs disagree — the best pass rate is rarely the fastest or the cheapest
 * — so there is no run that is simply best. Weights say what a better agent
 * means here, and the ranking follows from them. The winner card at the bottom
 * updates as the sliders move.
 */
export default function WinnerDrawer({
  open,
  onClose,
  summaries,
  evals,
  initial,
  onApply,
  scenarioCount,
  env,
  envState,
  metricIds,
  nameOf,
  showSummary = true,
  currentOnly = false,
}) {
  /* `metricIds` keeps only the metrics these runs carry — an improvement's
     runs have no mean return to weigh. */
  const metrics = useMemo(
    () =>
      allMetrics(evals).filter((m) => !metricIds || metricIds.includes(m.id)),
    [evals, metricIds],
  );
  const [weights, setWeights] = useState(
    () => initial || defaultWeights(metrics),
  );
  const [preset, setPreset] = useState(initial ? null : "balanced");

  /* Only runs that took the same test are in the race. */
  const field = useMemo(() => {
    if (!currentOnly || !env) {
      return comparableRuns(
        summaries,
        env ? (r) => runIdentityOf(r, envState, env).env : undefined,
      );
    }
    /* The Runs tab ranks on the environment version in use. A run is on it
       only when its snapshot is that one AND it started after that version
       existed — a label alone can collide (a seeded run's stamp), and a run
       can't have taken a test that didn't exist yet. */
    const current = currentEnvSnapshot(env, envState);
    /* The first version existed before any run, so only a later one is timed. */
    const since = current?.n > 1 && current?.at ? Date.parse(current.at) : 0;
    const on = (r) => {
      const id = runIdentityOf(r, envState, env);
      const started = Date.parse(r.startedAt || r.finishedAt || 0) || 0;
      return id.env === current?.label && (!since || started >= since)
        ? current.label
        : `older:${id.env || "?"}`;
    };
    return comparableRuns(summaries, on, current?.label || null);
  }, [summaries, env, envState, currentOnly]);
  const ranked = useMemo(
    () => rankRuns(field.runs, metrics, weights),
    [field, metrics, weights],
  );
  const winner = ranked[0];
  const margins = useMemo(() => winningMargins(ranked), [ranked]);
  /* The winner's run summary — a report beside the ranking, not a gate. */
  const summary = useMemo(
    () =>
      winner ? runRollupOf(winner.run, { env, envState, scenarioCount }) : null,
    [winner, env, envState, scenarioCount],
  );

  const set = (id, value) => {
    setPreset(null);
    setWeights((w) => ({ ...w, [id]: Math.max(0, Math.min(10, value)) }));
  };
  const apply = (p) => {
    setPreset(p.id);
    setWeights(presetWeights(p, metrics));
  };

  const evalMetrics = metrics.filter((m) => m.group === "eval");
  const sysMetrics = metrics.filter((m) => m.group === "system");

  /* One-line field summary, with the left-out detail behind a tooltip so the
     header stays short. */
  const fieldChips = [
    /* With an environment the field is already its snapshot ("v7"); without,
       the stamped world label is mapped to one. */
    field.envVersion &&
      `environment ${env ? field.envVersion : envLabelOf(env, envState, field.envVersion)}`,
    field.repeats && `${field.repeats} repeat${field.repeats === 1 ? "" : "s"}`,
    `${field.runs.length} run${field.runs.length === 1 ? "" : "s"} ranked`,
  ].filter(Boolean);

  return (
    <SideDrawer open={open} onClose={onClose} width={480}>
      <Stack sx={{ height: "100%" }}>
        {/* ── header ── */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2.5,
            py: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon="solar:cup-star-bold"
            width={20}
            sx={{ color: "status.warning.fg", flexShrink: 0 }}
          />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Winner settings
            </Typography>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ mt: 0.25, flexWrap: "wrap", rowGap: 0.25 }}
            >
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Ranking on {fieldChips.join(" · ")}
              </Typography>
              {field.excluded > 0 && (
                <Tooltip
                  arrow
                  title={`${field.excluded} left out — a different environment, a partial re-run, a different repeat count, or tools the world couldn't answer.`}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.25}
                    sx={{ color: "text.tertiary", cursor: "help" }}
                  >
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      · {field.runs.length ? field.excluded : "all"} left out
                    </Typography>
                    <Iconify icon="solar:info-circle-linear" width={12} />
                  </Stack>
                </Tooltip>
              )}
            </Stack>
          </Box>
          <IconButton size="small" onClick={onClose}>
            <Iconify
              icon="solar:close-linear"
              width={20}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          {/* ── presets: chips with no heading or blurb ── */}
          <Stack
            direction="row"
            spacing={0.75}
            flexWrap="wrap"
            rowGap={0.75}
            sx={{ px: 2.5, pt: 2, pb: 1 }}
          >
            {WEIGHT_PRESETS.map((p) => {
              const on = preset === p.id;
              return (
                <Tooltip key={p.id} arrow title={p.blurb || ""}>
                  <ButtonBase
                    aria-pressed={on}
                    onClick={() => apply(p)}
                    sx={{
                      ...focusRing,
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 0.75,
                      height: 30,
                      px: 1.25,
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: on ? alpha(p.color, 0.5) : "divider",
                      bgcolor: (t) =>
                        on
                          ? alpha(
                              p.color,
                              t.palette.mode === "dark" ? 0.14 : 0.08,
                            )
                          : "transparent",
                      "&:hover": {
                        borderColor: on ? alpha(p.color, 0.5) : "text.disabled",
                      },
                    }}
                  >
                    <Iconify icon={p.icon} width={14} sx={{ color: p.color }} />
                    <Typography
                      sx={{
                        typography: "s2",
                        fontWeight: 600,
                        color: on ? "text.primary" : "text.secondary",
                      }}
                    >
                      {p.label}
                    </Typography>
                  </ButtonBase>
                </Tooltip>
              );
            })}
          </Stack>

          {/* ── weights: one scale hint, then slim rows ── */}
          <Box sx={{ px: 2.5, pt: 1.5, pb: 2 }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              sx={{ mb: 0.5 }}
            >
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                Doesn&apos;t count
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                Counts a lot
              </Typography>
            </Stack>

            <MetricGroup
              label="Evaluation metrics"
              metrics={evalMetrics}
              weights={weights}
              onChange={set}
            />
            {sysMetrics.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <MetricGroup
                  label="System metrics"
                  metrics={sysMetrics}
                  weights={weights}
                  onChange={set}
                />
              </Box>
            )}
          </Box>
        </Box>

        {/* ── footer: winner + its run summary + actions ── */}
        <Box
          sx={{ borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          {!winner && (
            <Box sx={{ px: 2.5, py: 1.5, bgcolor: "background.neutral" }}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                Nothing comparable yet
              </Typography>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
              >
                {currentOnly && summaries?.length
                  ? `Every run is on an older environment version — run environment ${field.envVersion || "the current version"} to rank it.`
                  : "No finished full runs to rank yet."}
              </Typography>
            </Box>
          )}
          {winner && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ px: 2.5, py: 1.5, bgcolor: "background.neutral" }}
            >
              <Box
                sx={{
                  width: 30,
                  height: 30,
                  borderRadius: 1,
                  display: "grid",
                  placeItems: "center",
                  flexShrink: 0,
                  bgcolor: "status.warning.bg",
                  color: "status.warning.fg",
                }}
              >
                <Iconify icon="solar:cup-star-bold" width={16} />
              </Box>
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                  {nameOf
                    ? nameOf(winner.run)
                    : winner.run.kind === "trial"
                      ? `Run ${winner.run.trialN}`
                      : `Run ${winner.run.ordinal}`}{" "}
                  · {runIdentityOf(winner.run, envState, env).label}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {margins.length
                    ? `Ahead on ${margins.map((d) => d.label.toLowerCase()).join(" and ")}`
                    : "Every metric ties"}
                </Typography>
              </Box>
              <Box sx={{ textAlign: "right", flexShrink: 0 }}>
                <Typography
                  sx={{
                    typography: "m2",
                    fontWeight: 600,
                    fontVariantNumeric: "tabular-nums",
                    lineHeight: 1.1,
                  }}
                >
                  {Math.round(winner.score * 100)}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    letterSpacing: 0.2,
                  }}
                >
                  score
                </Typography>
              </Box>
            </Stack>
          )}

          {showSummary && summary && <WinnerSummary summary={summary} />}

          <Stack
            direction="row"
            justifyContent="flex-end"
            spacing={1}
            sx={{ px: 2.5, py: 1.75 }}
          >
            <Button
              size="small"
              onClick={onClose}
              sx={{ color: "text.secondary" }}
            >
              Cancel
            </Button>
            {/* Only one decision on this drawer: which run fits your weights
                best. Whether to ship that agent is yours, outside the harness. */}
            <Button
              variant="contained"
              color="primary"
              size="small"
              disabled={!winner}
              onClick={() =>
                onApply({ runId: winner.run.id, weights, score: winner.score })
              }
            >
              Choose winner
            </Button>
          </Stack>
        </Box>
      </Stack>
    </SideDrawer>
  );
}

WinnerDrawer.propTypes = {
  currentOnly: PropTypes.bool,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  summaries: PropTypes.array,
  evals: PropTypes.array,
  initial: PropTypes.object,
  onApply: PropTypes.func,
  /* Metric ids to weigh (default: all). */
  metricIds: PropTypes.arrayOf(PropTypes.string),
  /* How a run is named on the winner card. */
  nameOf: PropTypes.func,
  /* The winner's scenario report — off where the runs carry no scenarios. */
  showSummary: PropTypes.bool,
  scenarioCount: PropTypes.number,
  env: PropTypes.object,
  envState: PropTypes.object,
};

/* ── metrics ─────────────────────────────────────────────────────────────── */

function MetricGroup({ label, metrics, weights, onChange }) {
  return (
    <>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          textTransform: "none",
          letterSpacing: 0.4,
          mb: 1.25,
        }}
      >
        {label}
      </Typography>
      <Stack spacing={1.25}>
        {metrics.map((m) => (
          <WeightRow
            key={m.id}
            metric={m}
            value={weights[m.id] ?? 0}
            onChange={(v) => onChange(m.id, v)}
          />
        ))}
      </Stack>
    </>
  );
}
MetricGroup.propTypes = {
  label: PropTypes.string,
  metrics: PropTypes.array,
  weights: PropTypes.object,
  onChange: PropTypes.func,
};

/**
 * One weight — a segmented 0–10 scale with visible fill, so the reader can
 * see the balance across the whole panel without reading a number on every
 * row. Numbers stay next to the label for people who want the exact value.
 */
function WeightRow({ metric, value, onChange }) {
  const SEGMENTS = 10;
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.625 }}>
        <Typography
          sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}
        >
          {metric.label}
        </Typography>
        {metric.lowerIsBetter && (
          <Tooltip arrow title="Lower is better for this metric.">
            <Iconify
              icon="solar:arrow-down-linear"
              width={12}
              sx={{ color: "text.tertiary" }}
            />
          </Tooltip>
        )}
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.375}
          sx={{ flexShrink: 0 }}
        >
          <IconButton
            size="small"
            onClick={() => onChange(value - 1)}
            disabled={value <= 0}
          >
            <Iconify
              icon="solar:minus-square-linear"
              width={16}
              sx={{ color: value <= 0 ? "text.disabled" : "text.tertiary" }}
            />
          </IconButton>
          <Typography
            sx={{
              width: 18,
              textAlign: "center",
              typography: "s2",
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {value}
          </Typography>
          <IconButton
            size="small"
            onClick={() => onChange(value + 1)}
            disabled={value >= SEGMENTS}
          >
            <Iconify
              icon="solar:add-square-linear"
              width={16}
              sx={{
                color: value >= SEGMENTS ? "text.disabled" : "text.tertiary",
              }}
            />
          </IconButton>
        </Stack>
      </Stack>

      {/* Back to the MUI slider — the segmented bar the team didn't want. Kept
          slim (no marks, no per-row endpoint labels) so the panel stays
          scannable; the single "Doesn't count ← Counts a lot" hint at the top
          covers the direction for every row. */}
      <Slider
        size="small"
        value={value}
        min={0}
        max={SEGMENTS}
        step={1}
        onChange={(_, v) => onChange(v)}
        sx={{ py: 1, mt: 0.25 }}
      />
    </Box>
  );
}
WeightRow.propTypes = {
  metric: PropTypes.object,
  value: PropTypes.number,
  onChange: PropTypes.func,
};

/* ── the winner's run summary ───────────────────────────────────────────── */

/**
 * What the winning run said, as a report: pass^1 / pass^k and the fails by
 * cause. Winning is a preference applied; this is the evidence beside it.
 * Nothing here blocks or ships anything.
 */
function WinnerSummary({ summary }) {
  const causes = causeCountsLine(summary);
  return (
    <Box
      sx={{ px: 2.5, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          color: summary.belowBar ? "status.danger.fg" : "text.primary",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {summary.line}
        {!summary.lowVolume && summary.scored > 0 && (
          <Box
            component="span"
            sx={{ typography: "s3", fontWeight: 400, color: "text.tertiary" }}
          >
            {` · your bar ${Math.round(summary.bar.pass1 * 100)}%`}
          </Box>
        )}
      </Typography>
      {(causes || summary.caveat) && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}>
          {[
            causes,
            summary.unreliable
              ? "unreliable — too many test invalid · simulator"
              : null,
            summary.caveat,
          ]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
      )}
    </Box>
  );
}
WinnerSummary.propTypes = { summary: PropTypes.object };
