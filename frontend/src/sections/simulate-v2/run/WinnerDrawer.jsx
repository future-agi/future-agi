import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Slider, Tooltip, Collapse,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import {
  allMetrics, WEIGHT_PRESETS, presetWeights, defaultWeights, rankRuns, winningMargins, releaseGate, comparableRuns,
} from "../_mock/winner";

/**
 * What "winning" means, before anything is declared the winner.
 *
 * The runs disagree — the best pass rate is rarely the fastest or the cheapest
 * — so there is no run that is simply best. Weights say what a better agent
 * means here, and the ranking follows from them. The winner card at the bottom
 * updates as the sliders move.
 */
export default function WinnerDrawer({
  open, onClose, summaries, evals, initial, onApply, baseline, scenarioCount,
}) {
  const metrics = useMemo(() => allMetrics(evals), [evals]);
  const [weights, setWeights] = useState(() => initial || defaultWeights(metrics));
  const [preset, setPreset] = useState(initial ? null : "balanced");

  /* Only runs that took the same test are in the race. */
  const field = useMemo(() => comparableRuns(summaries), [summaries]);
  const ranked = useMemo(
    () => rankRuns(field.runs, metrics, weights),
    [field, metrics, weights],
  );
  const winner = ranked[0];
  const margins = useMemo(() => winningMargins(ranked), [ranked]);
  const gate = useMemo(
    () => releaseGate(winner?.run, { baseline, scenarioCount }),
    [winner, baseline, scenarioCount],
  );

  const set = (id, value) => {
    setPreset(null);
    setWeights((w) => ({ ...w, [id]: Math.max(0, Math.min(10, value)) }));
  };
  const apply = (p) => { setPreset(p.id); setWeights(presetWeights(p, metrics)); };

  const evalMetrics = metrics.filter((m) => m.group === "eval");
  const sysMetrics = metrics.filter((m) => m.group === "system");

  /* One-line field summary, with the left-out detail behind a tooltip so the
     header stays short. */
  const fieldChips = [
    field.envVersion && `env ${field.envVersion}`,
    field.repeats && `${field.repeats} trial${field.repeats === 1 ? "" : "s"}`,
    `${field.runs.length} run${field.runs.length === 1 ? "" : "s"} ranked`,
  ].filter(Boolean);

  return (
    <SideDrawer open={open} onClose={onClose} width={560}>
      <Stack sx={{ height: "100%" }}>
        {/* ── header ── */}
        <Stack
          direction="row" alignItems="center" spacing={1.5}
          sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Iconify icon="solar:cup-star-bold" width={18} sx={{ color: "#EA580C", flexShrink: 0 }} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>Winner settings</Typography>
            <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 0.25, flexWrap: "wrap", rowGap: 0.25 }}>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Ranking on {fieldChips.join(" · ")}
              </Typography>
              {field.excluded > 0 && (
                <Tooltip
                  arrow
                  title={`${field.excluded} left out — a different environment, a partial re-run, a different trial count, or tools the world couldn't answer.`}
                >
                  <Stack direction="row" alignItems="center" spacing={0.25} sx={{ color: "text.subtitle", cursor: "help" }}>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>· {field.excluded} left out</Typography>
                    <Iconify icon="solar:info-circle-linear" width={11} />
                  </Stack>
                </Tooltip>
              )}
            </Stack>
          </Box>
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          {/* ── presets: chips with no heading or blurb ── */}
          <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75} sx={{ px: 2.5, pt: 2, pb: 1 }}>
            {WEIGHT_PRESETS.map((p) => {
              const on = preset === p.id;
              return (
                <Tooltip key={p.id} arrow title={p.blurb || ""}>
                  <Box
                    role="button"
                    onClick={() => apply(p)}
                    sx={{
                      display: "inline-flex", alignItems: "center", gap: 0.75, cursor: "pointer",
                      height: 30, px: 1.25, borderRadius: 1,
                      border: "1px solid",
                      borderColor: on ? alpha(p.color, 0.5) : "divider",
                      bgcolor: (t) => (on ? alpha(p.color, t.palette.mode === "dark" ? 0.14 : 0.08) : "transparent"),
                      "&:hover": { borderColor: on ? alpha(p.color, 0.5) : "text.disabled" },
                    }}
                  >
                    <Iconify icon={p.icon} width={13} sx={{ color: p.color }} />
                    <Typography sx={{ typography: "s2", fontWeight: 600, color: on ? "text.primary" : "text.secondary" }}>
                      {p.label}
                    </Typography>
                  </Box>
                </Tooltip>
              );
            })}
          </Stack>

          {/* ── weights: one scale hint, then slim rows ── */}
          <Box sx={{ px: 2.5, pt: 1.5, pb: 2 }}>
            <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>Doesn&apos;t count</Typography>
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>Counts a lot</Typography>
            </Stack>

            <MetricGroup label="Evaluation metrics" metrics={evalMetrics} weights={weights} onChange={set} />
            {sysMetrics.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <MetricGroup label="System metrics" metrics={sysMetrics} weights={weights} onChange={set} />
              </Box>
            )}
          </Box>
        </Box>

        {/* ── footer: winner + gate + actions ── */}
        <Box sx={{ borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          {winner && (
            <Stack
              direction="row" alignItems="center" spacing={1.5}
              sx={{ px: 2.5, py: 1.5, bgcolor: "background.neutral" }}
            >
              <Box
                sx={{
                  width: 30, height: 30, borderRadius: 1, display: "grid", placeItems: "center", flexShrink: 0,
                  bgcolor: (t) => alpha("#EA580C", t.palette.mode === "dark" ? 0.18 : 0.1), color: "#EA580C",
                }}
              >
                <Iconify icon="solar:cup-star-bold" width={16} />
              </Box>
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>
                  Run {winner.run.ordinal} · agent {winner.run.agentVersion}
                </Typography>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                  {margins.length
                    ? `Ahead on ${margins.map((d) => d.label.toLowerCase()).join(" and ")}`
                    : "Every metric ties"}
                </Typography>
              </Box>
              <Box sx={{ textAlign: "right", flexShrink: 0 }}>
                <Typography sx={{ typography: "m2", fontWeight: 700, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>
                  {Math.round(winner.score * 100)}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", letterSpacing: 0.2 }}>
                  score
                </Typography>
              </Box>
            </Stack>
          )}

          {gate && <GateVerdict gate={gate} />}

          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ px: 2.5, py: 1.75 }}>
            <Button
              size="small" onClick={onClose}
              sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
            >
              Cancel
            </Button>
            {/* Only one primary decision on this drawer. Marking a version as
                released is a separate act — done from the winner's row in the
                Runs list, where the reader has just been. */}
            <Button
              variant="contained" color="primary" size="small"
              disabled={!winner}
              onClick={() => onApply({ runId: winner.run.id, weights, score: winner.score })}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              {gate?.status === "blocked" ? "Choose anyway" : "Choose winner"}
            </Button>
          </Stack>
        </Box>
      </Stack>
    </SideDrawer>
  );
}

WinnerDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  summaries: PropTypes.array,
  evals: PropTypes.array,
  initial: PropTypes.object,
  onApply: PropTypes.func,
  baseline: PropTypes.object,
  scenarioCount: PropTypes.number,
};

/* ── metrics ─────────────────────────────────────────────────────────────── */

function MetricGroup({ label, metrics, weights, onChange }) {
  return (
    <>
      <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1.25 }}>
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
MetricGroup.propTypes = { label: PropTypes.string, metrics: PropTypes.array, weights: PropTypes.object, onChange: PropTypes.func };

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
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}>
          {metric.label}
        </Typography>
        {metric.lowerIsBetter && (
          <Tooltip arrow title="Lower is better for this metric.">
            <Iconify icon="solar:arrow-down-linear" width={12} sx={{ color: "text.subtitle" }} />
          </Tooltip>
        )}
        <Stack
          direction="row" alignItems="center" spacing={0.375} sx={{ flexShrink: 0 }}
        >
          <IconButton
            size="small" onClick={() => onChange(value - 1)} disabled={value <= 0}
            sx={{ p: 0.25 }}
          >
            <Iconify icon="solar:minus-square-linear" width={16} sx={{ color: value <= 0 ? "text.disabled" : "text.subtitle" }} />
          </IconButton>
          <Typography sx={{ width: 18, textAlign: "center", typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
            {value}
          </Typography>
          <IconButton
            size="small" onClick={() => onChange(value + 1)} disabled={value >= SEGMENTS}
            sx={{ p: 0.25 }}
          >
            <Iconify icon="solar:add-square-linear" width={16} sx={{ color: value >= SEGMENTS ? "text.disabled" : "text.subtitle" }} />
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
WeightRow.propTypes = { metric: PropTypes.object, value: PropTypes.number, onChange: PropTypes.func };

/* ── gate verdict ────────────────────────────────────────────────────────── */

const GATE_TONE = {
  clear: { color: "#16A34A", icon: "solar:shield-check-bold", label: "Clears the release gate" },
  warn: { color: "#B98A3C", icon: "solar:shield-warning-bold", label: "Clears with things to accept" },
  blocked: { color: "#C2603F", icon: "solar:shield-cross-bold", label: "Does not clear the release gate" },
};

/**
 * The gate. One line and up to two failing reasons; anything else opens on
 * demand. "Blocked with no reason attached is a wall", but that reason is one
 * sentence — not five checks with their budgets restated.
 */
function GateVerdict({ gate }) {
  const tone = GATE_TONE[gate.status];
  const [expanded, setExpanded] = useState(false);
  const notable = [...gate.blocked, ...gate.warnings];
  const primary = notable.slice(0, 2);
  const rest = [...notable.slice(2), ...gate.checks.filter((c) => c.ok)];

  return (
    <Box sx={{ px: 2.5, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Iconify icon={tone.icon} width={15} sx={{ color: tone.color, flexShrink: 0 }} />
        <Typography sx={{ typography: "s2", fontWeight: 700, color: tone.color, flex: 1, minWidth: 0 }} noWrap>
          {tone.label}
        </Typography>
        {rest.length > 0 && (
          <Box
            role="button"
            onClick={() => setExpanded((o) => !o)}
            sx={{ typography: "s3", color: "text.subtitle", cursor: "pointer", "&:hover": { color: "text.primary" } }}
          >
            {expanded ? "Hide" : `Show ${rest.length} more`}
          </Box>
        )}
      </Stack>

      {primary.length > 0 && (
        <Stack spacing={0.5} sx={{ mt: 0.875 }}>
          {primary.map((c) => <CheckLine key={c.id} check={c} />)}
        </Stack>
      )}

      <Collapse in={expanded} unmountOnExit>
        <Stack spacing={0.5} sx={{ mt: primary.length ? 0.5 : 0.875 }}>
          {rest.map((c) => <CheckLine key={c.id} check={c} />)}
        </Stack>
      </Collapse>
    </Box>
  );
}
GateVerdict.propTypes = { gate: PropTypes.object };

function CheckLine({ check }) {
  return (
    <Stack direction="row" alignItems="flex-start" spacing={0.875}>
      <Iconify
        icon={check.ok ? "solar:check-circle-bold" : check.hard ? "solar:close-circle-bold" : "solar:info-circle-bold"}
        width={13}
        sx={{ mt: "2px", flexShrink: 0, color: check.ok ? "#5AA47B" : check.hard ? "#C2603F" : "#B98A3C" }}
      />
      <Typography sx={{ typography: "s3", color: "text.secondary", lineHeight: 1.5 }}>
        <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>{check.label}</Box>
        {" — "}{check.detail}
      </Typography>
    </Stack>
  );
}
CheckLine.propTypes = { check: PropTypes.object };
