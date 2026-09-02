import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Slider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import {
  allMetrics, WEIGHT_PRESETS, presetWeights, defaultWeights, rankRuns, winningMargins, releaseGate,
} from "../_mock/winner";

/**
 * What "winning" means, before anything is declared the winner.
 *
 * The runs disagree — the best pass rate is rarely the fastest or the cheapest
 * — so there is no run that is simply best. Rather than hide that behind a
 * button that silently applies somebody's idea of a good agent, the weights are
 * the screen: say what matters here, and the ranking follows from it.
 *
 * The preview at the bottom is the part worth having. Weights are abstract
 * until you see which run they pick, and seeing the answer change as you move a
 * slider is what stops this being a form people fill in without reading.
 */
export default function WinnerDrawer({
  open, onClose, summaries, evals, initial, onApply, onRelease, baseline, scenarioCount, released,
}) {
  const metrics = useMemo(() => allMetrics(evals), [evals]);
  const [weights, setWeights] = useState(() => initial || defaultWeights(metrics));
  const [preset, setPreset] = useState(initial ? null : "balanced");

  const ranked = useMemo(
    () => rankRuns(summaries, metrics, weights),
    [summaries, metrics, weights],
  );
  const winner = ranked[0];
  const margins = useMemo(() => winningMargins(ranked), [ranked]);

  /*
    The second verdict. Weights decide which run someone prefers; the gate
    decides whether that run is fit to release, and the two answers are
    routinely different. Shown here rather than after the fact, because the
    moment a winner is crowned is the moment it starts being quoted.
  */
  const gate = useMemo(
    () => releaseGate(winner?.run, { baseline, scenarioCount }),
    [winner, baseline, scenarioCount],
  );

  const set = (id, value) => {
    setPreset(null);
    setWeights((w) => ({ ...w, [id]: Math.max(0, Math.min(10, value)) }));
  };

  const apply = (p) => {
    setPreset(p.id);
    setWeights(presetWeights(p, metrics));
  };

  const groups = [
    { id: "eval", label: "Evaluation metrics", icon: "solar:shield-check-linear" },
    { id: "system", label: "System metrics", icon: "solar:settings-minimalistic-linear" },
  ];

  return (
    <SideDrawer open={open} onClose={onClose} width={560}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row" alignItems="flex-start" spacing={2}
          sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>Winner settings</Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Set how much each metric matters. The runs are ranked on these weights, so this is
              where you say what a better agent means here.
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <Box sx={{ px: 2.5, py: 2 }}>
            <Typography
              sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}
            >
              Presets
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1}>
              {WEIGHT_PRESETS.map((p) => {
                const on = preset === p.id;
                return (
                  <Button
                    key={p.id}
                    size="small"
                    onClick={() => apply(p)}
                    startIcon={<Iconify icon={p.icon} width={15} sx={{ color: p.color }} />}
                    sx={{
                      typography: "s2", fontWeight: 600, borderRadius: 1,
                      border: "1px solid",
                      borderColor: on ? alpha(p.color, 0.5) : "divider",
                      color: on ? "text.primary" : "text.secondary",
                      bgcolor: (t) => (on ? alpha(p.color, t.palette.mode === "dark" ? 0.14 : 0.08) : "transparent"),
                    }}
                  >
                    {p.label}
                  </Button>
                );
              })}
            </Stack>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}>
              {WEIGHT_PRESETS.find((p) => p.id === preset)?.blurb || "Custom weights."}
            </Typography>
          </Box>

          {groups.map((g) => (
            <Box key={g.id} sx={{ px: 2.5, pb: 2 }}>
              <Stack direction="row" alignItems="center" spacing={0.875} sx={{ mb: 1.5 }}>
                <Iconify icon={g.icon} width={15} sx={{ color: "text.subtitle" }} />
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>{g.label}</Typography>
              </Stack>
              <Stack spacing={2.5}>
                {metrics.filter((m) => m.group === g.id).map((m) => (
                  <WeightRow
                    key={m.id}
                    metric={m}
                    value={weights[m.id] ?? 0}
                    onChange={(v) => set(m.id, v)}
                  />
                ))}
              </Stack>
            </Box>
          ))}
        </Box>

        {/* ── what these weights actually pick ── */}
        <Box sx={{ borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          {winner && (
            <Stack
              direction="row" alignItems="center" spacing={1.5}
              sx={{ px: 2.5, py: 1.75, bgcolor: "background.neutral" }}
            >
              <Iconify icon="solar:cup-star-bold" width={18} sx={{ color: "#EA580C", flexShrink: 0 }} />
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                  Run {winner.run.ordinal} · agent {winner.run.agentVersion} wins
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {margins.length
                    ? `Ahead on ${margins.map((d) => `${d.label.toLowerCase()} (${d.value})`).join(" and ")}.`
                    : "Every metric ties — the order here is arbitrary."}
                </Typography>
              </Box>
              <Typography sx={{ typography: "m2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                {Math.round(winner.score * 100)}
              </Typography>
            </Stack>
          )}

          {gate && <GateVerdict gate={gate} />}

          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ px: 2.5, py: 2 }}>
            <Button
              variant="outlined" color="inherit" size="small" onClick={onClose}
              sx={{ typography: "s2", fontWeight: 700, borderColor: "divider" }}
            >
              Cancel
            </Button>
            {/*
              Shipping is a separate decision from winning, so it is a separate
              button. Recording it is what lets the next comparison default its
              baseline to what is actually live rather than to whatever someone
              remembers shipping.
            */}
            <Button
              variant="outlined" color="inherit" size="small"
              disabled={!winner || released === winner?.run?.agentVersion}
              onClick={() => onRelease?.({
                version: winner.run.agentVersion,
                runId: winner.run.id,
                at: new Date().toISOString(),
                gate: gate?.status || "clear",
              })}
              startIcon={<Iconify icon="solar:rocket-2-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: 700, borderColor: "divider" }}
            >
              {released === winner?.run?.agentVersion
                ? `agent ${released} is live`
                : `Mark agent ${winner?.run?.agentVersion} released`}
            </Button>
            <Button
              variant="contained" color="primary" size="small"
              disabled={!winner}
              onClick={() => onApply({ runId: winner.run.id, weights, score: winner.score })}
              startIcon={<Iconify icon="solar:cup-star-bold" width={15} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              {gate?.status === "blocked" ? "Choose winner anyway" : "Choose winner"}
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
  onRelease: PropTypes.func,
  released: PropTypes.string,
  baseline: PropTypes.object,
  scenarioCount: PropTypes.number,
};

/**
 * What the gate says about the run the weights picked.
 *
 * Deliberately not a badge. "Blocked" with no reason attached is a wall; the
 * checks are the whole value, and the failing ones lead because they are what
 * someone has to answer for if they ship anyway.
 */
const GATE_TONE = {
  clear: { color: "#16A34A", icon: "solar:shield-check-bold", label: "Clears the release gate" },
  warn: { color: "#B98A3C", icon: "solar:shield-warning-bold", label: "Clears, with things to accept" },
  blocked: { color: "#C2603F", icon: "solar:shield-cross-bold", label: "Does not clear the release gate" },
};

function GateVerdict({ gate }) {
  const tone = GATE_TONE[gate.status];
  /*
    Failing checks first, then the budgets whether or not they bit.

    A ceiling nobody can see is a ceiling nobody sets: if the cost budget only
    appears on the run that breaches it, the first time anyone learns it exists
    is the first time it blocks them.
  */
  const notable = [...gate.blocked, ...gate.warnings];
  const budgets = gate.checks.filter((c) => c.id.startsWith("budget-") && !notable.includes(c));
  const shown = [...(notable.length ? notable : gate.checks.slice(0, 2)), ...budgets];

  return (
    <Box sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Iconify icon={tone.icon} width={16} sx={{ color: tone.color, flexShrink: 0 }} />
        <Typography sx={{ typography: "s2", fontWeight: 700, color: tone.color }}>{tone.label}</Typography>
        <Box flex={1} />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          winning is a preference · the gate is the decision
        </Typography>
      </Stack>

      <Stack spacing={0.5} sx={{ mt: 1 }}>
        {shown.map((c) => (
          <Stack key={c.id} direction="row" alignItems="flex-start" spacing={0.875}>
            <Iconify
              icon={c.ok ? "solar:check-circle-bold" : c.hard ? "solar:close-circle-bold" : "solar:info-circle-bold"}
              width={14}
              sx={{ mt: "1px", flexShrink: 0, color: c.ok ? "#5AA47B" : c.hard ? "#C2603F" : "#B98A3C" }}
            />
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              <Box component="span" sx={{ fontWeight: 600, color: "text.primary" }}>{c.label}</Box>
              {" — "}{c.detail}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

GateVerdict.propTypes = { gate: PropTypes.object };

/**
 * One weight.
 *
 * The stepper and the slider are the same value: the slider is for deciding
 * roughly how much this matters against the row above it, the stepper for
 * saying "no, exactly seven" without fighting a drag target.
 */
function WeightRow({ metric, value, onChange }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 0.5 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}>
          {metric.label}
          {metric.lowerIsBetter && (
            <Box component="span" sx={{ typography: "s3", color: "text.subtitle", ml: 0.75 }}>
              lower is better
            </Box>
          )}
        </Typography>
        <Stack
          direction="row" alignItems="center"
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 0.875, flexShrink: 0 }}
        >
          <IconButton size="small" onClick={() => onChange(value - 1)} disabled={value <= 0}>
            <Iconify icon="solar:minus-circle-linear" width={15} sx={{ color: "text.subtitle" }} />
          </IconButton>
          <Typography
            sx={{ width: 22, textAlign: "center", typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums" }}
          >
            {value}
          </Typography>
          <IconButton size="small" onClick={() => onChange(value + 1)} disabled={value >= 10}>
            <Iconify icon="solar:add-circle-linear" width={15} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Stack>
      </Stack>

      <Slider
        size="small"
        value={value}
        min={0}
        max={10}
        step={1}
        marks
        onChange={(_, v) => onChange(v)}
        sx={{ py: 1 }}
      />

      <Stack direction="row" justifyContent="space-between">
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Not important</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Very important</Typography>
      </Stack>
    </Box>
  );
}
WeightRow.propTypes = { metric: PropTypes.object, value: PropTypes.number, onChange: PropTypes.func };
