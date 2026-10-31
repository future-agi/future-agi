import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  ButtonBase,
  IconButton,
  Slider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SideDrawer from "../../../components/SideDrawer";
import SoonChip from "../../../components/SoonChip";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import {
  comparableRuns,
  presetWeights,
  rankRuns,
  WEIGHT_PRESETS,
  winnerMetrics,
  winningMargins,
} from "./summaryData";

const SEGMENTS = 10;
const TITLE_ID = "winner-drawer-title";

// Metrics Vel weighs that the runs list doesn't send yet.
const SOON_EVAL_METRICS = [
  { label: "Mean return", what: "The mean return" },
  {
    label: "Said, not done",
    what: "Counts of calls where the agent said it was done but wasn't",
    plural: true,
  },
];
const SOON_SYSTEM_METRICS = [
  { label: "Tokens", what: "Token counts", plural: true },
  { label: "Cost", what: "A run's cost" },
];

const nameOf = (run) =>
  run.agentVersion ? `${run.label} · agent ${run.agentVersion}` : run.label;

// What "winning" means, before a run is called the winner. The runs disagree
// (the best pass rate is rarely the fastest), so weights say what a better
// agent means here and the ranking follows from them, on the runs' real pass
// rate, run time and eval scores. Saving the winner has no API yet.
export default function WinnerDrawer({ open, onClose, rows, evals, onApply }) {
  const metrics = useMemo(() => winnerMetrics(evals), [evals]);
  const [preset, setPreset] = useState(WEIGHT_PRESETS[0].id);
  const [weights, setWeights] = useState(() =>
    presetWeights(WEIGHT_PRESETS[0], metrics),
  );
  // An eval that shows up after the drawer opened (its scores loading) gets
  // the current preset's weight rather than none.
  useEffect(() => {
    setWeights((w) => {
      const missing = metrics.filter((m) => w[m.id] == null);
      if (!missing.length) return w;
      const p =
        WEIGHT_PRESETS.find((x) => x.id === preset) || WEIGHT_PRESETS[0];
      return { ...presetWeights(p, missing), ...w };
    });
  }, [metrics, preset]);

  const field = useMemo(() => comparableRuns(rows), [rows]);
  const ranked = useMemo(
    () => rankRuns(field.runs, metrics, weights),
    [field, metrics, weights],
  );
  const winner = ranked[0];
  const margins = useMemo(() => winningMargins(ranked), [ranked]);

  const set = (id, value) => {
    setPreset(null);
    setWeights((w) => ({ ...w, [id]: Math.max(0, Math.min(SEGMENTS, value)) }));
  };
  const apply = (p) => {
    setPreset(p.id);
    setWeights(presetWeights(p, metrics));
  };

  const evalMetrics = metrics.filter((m) => m.group === "eval");
  const sysMetrics = metrics.filter((m) => m.group === "system");
  const fieldLine = [
    field.repeats &&
      `${field.repeats} ${field.repeats === 1 ? "repeat" : "repeats"}`,
    `${field.runs.length} ${field.runs.length === 1 ? "run" : "runs"} ranked`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <SideDrawer open={open} onClose={onClose} width={480}>
      <Stack role="dialog" aria-labelledby={TITLE_ID} sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            px: 2.5,
            py: 2,
            pr: 6,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon="solar:cup-star-bold"
            width={20}
            sx={{ color: "warning.main", flexShrink: 0 }}
          />
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                id={TITLE_ID}
                sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}
              >
                Winner settings
              </Typography>
              <UiOnlyFlag feature="chooseWinner" />
            </Stack>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ mt: 0.25 }}
            >
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Ranking on {fieldLine}
              </Typography>
              {field.excluded > 0 && (
                <CustomTooltip
                  show
                  arrow
                  size="small"
                  title={`${field.excluded} left out: still going, stopped, or run with a different repeat count.`}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.25}
                    sx={{ color: "text.subtitle", cursor: "help" }}
                  >
                    <Typography sx={{ typography: "s3", color: "inherit" }}>
                      {`· ${field.runs.length ? field.excluded : "all"} left out`}
                    </Typography>
                    <Iconify icon="solar:info-circle-linear" width={12} />
                  </Stack>
                </CustomTooltip>
              )}
            </Stack>
          </Box>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <Stack
            direction="row"
            spacing={0.75}
            useFlexGap
            sx={{ px: 2.5, pt: 2, pb: 1, flexWrap: "wrap", rowGap: 0.75 }}
          >
            {WEIGHT_PRESETS.map((p) => {
              const on = preset === p.id;
              return (
                <CustomTooltip
                  key={p.id}
                  show
                  arrow
                  size="small"
                  title={p.blurb}
                >
                  <ButtonBase
                    aria-pressed={on}
                    aria-label={p.label}
                    onClick={() => apply(p)}
                    sx={{
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
                        fontWeight: "fontWeightSemiBold",
                        color: on ? "text.primary" : "text.secondary",
                      }}
                    >
                      {p.label}
                    </Typography>
                  </ButtonBase>
                </CustomTooltip>
              );
            })}
          </Stack>

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
              soon={SOON_EVAL_METRICS}
              weights={weights}
              onChange={set}
            />
            <Box sx={{ mt: 2 }}>
              <MetricGroup
                label="System metrics"
                metrics={sysMetrics}
                soon={SOON_SYSTEM_METRICS}
                weights={weights}
                onChange={set}
              />
            </Box>
          </Box>
        </Box>

        <Box
          sx={{ borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          {winner ? (
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
                  color: "warning.main",
                  bgcolor: (t) => alpha(t.palette.warning.main, 0.12),
                }}
              >
                <Iconify icon="solar:cup-star-bold" width={16} />
              </Box>
              <Box flex={1} minWidth={0}>
                <Typography
                  noWrap
                  data-testid="winner-name"
                  sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
                >
                  {nameOf(winner.run)}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.subtitle" }}
                >
                  {margins.length
                    ? `Ahead on ${margins.map((d) => d.label.toLowerCase()).join(" and ")}`
                    : "Every metric ties"}
                </Typography>
              </Box>
              <Box sx={{ textAlign: "right", flexShrink: 0 }}>
                <Typography
                  sx={{
                    typography: "m3",
                    fontWeight: "fontWeightSemiBold",
                    fontVariantNumeric: "tabular-nums",
                    lineHeight: 1.1,
                  }}
                >
                  {Math.round(winner.score * 100)}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  score
                </Typography>
              </Box>
            </Stack>
          ) : (
            <Box sx={{ px: 2.5, py: 1.5, bgcolor: "background.neutral" }}>
              <Typography
                sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
              >
                Nothing comparable yet
              </Typography>
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
              >
                No finished runs to rank yet.
              </Typography>
            </Box>
          )}

          {winner && (
            <Box
              sx={{
                px: 2.5,
                py: 1.25,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: "fontWeightSemiBold",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`${winner.run.passed} of ${winner.run.tasks} calls passed`}
              </Typography>
            </Box>
          )}

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
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  // Finished summary rows in scope, with their eval scores.
  rows: PropTypes.arrayOf(PropTypes.object).isRequired,
  evals: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  ).isRequired,
  onApply: PropTypes.func.isRequired,
};

function MetricGroup({ label, metrics, soon, weights, onChange }) {
  return (
    <>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: "fontWeightSemiBold",
          color: "text.subtitle",
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
      {soon.length > 0 && (
        <Stack
          direction="row"
          spacing={0.75}
          useFlexGap
          sx={{ mt: 1.25, flexWrap: "wrap", rowGap: 0.75 }}
        >
          {soon.map((s) => (
            <SoonChip
              key={s.label}
              label={s.label}
              what={s.what}
              plural={s.plural}
              size="tiny"
            />
          ))}
        </Stack>
      )}
    </>
  );
}

MetricGroup.propTypes = {
  label: PropTypes.string,
  metrics: PropTypes.array,
  soon: PropTypes.array,
  weights: PropTypes.object,
  onChange: PropTypes.func,
};

// One weight on a 0 to 10 scale, with steppers beside the exact value.
function WeightRow({ metric, value, onChange }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.625 }}>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
            flex: 1,
            minWidth: 0,
          }}
        >
          {metric.label}
        </Typography>
        {metric.lowerIsBetter && (
          <CustomTooltip
            show
            arrow
            size="small"
            title="Lower is better for this metric."
          >
            <Box sx={{ display: "inline-flex", color: "text.subtitle" }}>
              <Iconify icon="solar:arrow-down-linear" width={12} />
            </Box>
          </CustomTooltip>
        )}
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.375}
          sx={{ flexShrink: 0 }}
        >
          <IconButton
            size="small"
            aria-label={`Lower the ${metric.label} weight`}
            onClick={() => onChange(value - 1)}
            disabled={value <= 0}
          >
            <Iconify icon="solar:minus-square-linear" width={16} />
          </IconButton>
          <Typography
            sx={{
              width: 18,
              textAlign: "center",
              typography: "s2",
              fontWeight: "fontWeightSemiBold",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {value}
          </Typography>
          <IconButton
            size="small"
            aria-label={`Raise the ${metric.label} weight`}
            onClick={() => onChange(value + 1)}
            disabled={value >= SEGMENTS}
          >
            <Iconify icon="solar:add-square-linear" width={16} />
          </IconButton>
        </Stack>
      </Stack>
      <Slider
        size="small"
        value={value}
        min={0}
        max={SEGMENTS}
        step={1}
        onChange={(_, v) => onChange(v)}
        aria-label={`${metric.label} weight`}
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
