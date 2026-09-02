import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Chip, Tooltip, IconButton, Tabs, Tab, Divider, Menu, MenuItem, ListItemIcon, ListItemText } from "@mui/material";
import Iconify from "src/components/iconify";
import { RunTracePanel, RunTraceLog } from "../../components/RunTrace";
import { searchTrace } from "../../_mock/optimizer";
import { optimizationVerdict, OPTIMIZER_MODELS } from "../../_mock/optimizationRuns";
import { changeFileFor } from "../../_mock/optimize";
import TrialHeatmap from "./TrialHeatmap";
import OmegaHandoff from "../OmegaHandoff";
import TaskLinks from "./TaskLinks";

/**
 * One optimization run.
 *
 * The old screen had the right bones — a header with the run's identity, the
 * steps it took, a graph, a bar of results and a grid of trials — so those are
 * kept. What changes is what a trial means. Against a dataset a trial is a
 * scored string; here it is a full sweep of the scenarios through real tools,
 * which makes three things worth showing that a dataset optimizer never had to.
 *
 * The winner is checked for gaming before it can leave. An optimizer pointed at
 * a scoring function will find whatever that function rewards, and the cheapest
 * way to pass a check that reads words is better words — so the same analyzer
 * that flagged the agent's own inflated scores re-reads the winner's. Shipping
 * a prompt that learned to say "I have processed your refund" is the failure
 * this whole product exists to prevent; it would be strange to build the
 * machine that produces it and not look.
 *
 * The winner is the best candidate that did not break a release blocker, not
 * the best candidate. And the grid shows which scenarios each trial moved,
 * because two candidates a point apart are usually fixing different things.
 */

export default function OptimizationRunView({ record, env, envState, patch, tasks, onOpenTask, onBack, onClose, onDone, onRerun }) {
  const theme = useTheme();
  const [tab, setTab] = useState("trials");
  const [openTrial, setOpenTrial] = useState(null);
  const running = record.status === "running";
  const result = record.result;
  const verdict = optimizationVerdict(record);
  const model = OPTIMIZER_MODELS.find((m) => m.id === record.model);

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      {/* ── identity ── */}
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <IconButton size="small" onClick={onBack}>
          <Iconify icon="eva:arrow-ios-back-fill" width={17} />
        </IconButton>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" rowGap={0.5}>
            <Typography sx={{ typography: "s1", fontWeight: 700 }}>{record.name}</Typography>
            <Chip
              size="small"
              label={running ? "Running" : "Completed"}
              sx={{
                height: 19, borderRadius: 0.5,
                color: running ? "#CA8A04" : "#16A34A",
                border: "1px solid",
                borderColor: alpha(running ? "#CA8A04" : "#16A34A", 0.4),
                bgcolor: "transparent",
                "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700 },
              }}
            />
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {record.id} · {result?.optimizer?.label} · {model?.label || record.model} ·{" "}
            {result?.trials?.length || 0} trials × {result?.trainMeasured || 0} scenarios
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="eva:close-fill" width={17} />
        </IconButton>
      </Stack>

      {running && (
        <Box sx={{ flex: 1, overflowY: "auto" }}>
          <RunTracePanel
            title={`Searching with ${result.optimizer.label}`}
            subtitle={`${result.trials.length} trials × ${result.trainMeasured} training scenarios — each trial is a full run`}
            steps={searchTrace(result)}
            stepMs={800}
            onDone={onDone}
          />
        </Box>
      )}

      {!running && (
        <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {/* ── the numbers ── */}
          <Stack
            direction="row" alignItems="flex-start" spacing={4} flexWrap="wrap" rowGap={2}
            sx={{ px: 2.5, py: 2, bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.08 : 0.04) }}
          >
            <Metric label="Best on training" value={`${result.winner.score}%`}
              sub={`trial ${result.winner.n} of ${result.trials.length} · from ${result.base}%`} />
            <Metric
              label="Held out" value={`${result.heldScore}%`}
              tone={result.heldScore < result.heldBase ? "#DC2626" : result.heldBase >= 100 ? "#CA8A04" : "#16A34A"}
              sub={result.heldBase >= 100
                ? `${result.heldMeasured} scenarios · all passing already`
                : `${result.heldMeasured} scenarios · from ${result.heldBase}%`}
              hint="Scenarios the optimizer never saw. This is the number worth quoting — the training score is what the search was allowed to fit to."
            />
            {/* Signed and labelled, because "+6" under the word "gap" reads as
                six points of overfitting when it means the opposite. */}
            <Metric
              label={result.gap > 0 ? "Overfit gap" : "Held out vs training"}
              value={result.gap > 0 ? `−${result.gap}` : `+${-result.gap}`}
              tone={result.gap > 12 ? "#DC2626" : result.gap > 6 ? "#CA8A04" : undefined}
              sub={result.gap > 12
                ? "large — the prompt learned the suite"
                : result.gap > 0 ? "training score did not fully hold" : "held out scored higher"}
            />
            <Metric
              label="Training scenarios moved" value={`+${result.winner.fixed || 0}`}
              tone={result.winner.broke ? "#CA8A04" : "#16A34A"}
              sub={result.winner.broke ? `${result.winner.broke} regressed` : "none regressed"}
            />
          </Stack>

          {/* ── the verdict, which is often not the headline ── */}
          {verdict && (
            <Stack
              direction="row" alignItems="flex-start" spacing={1.5}
              sx={{
                mx: 2.5, mt: 2, px: 2, py: 1.75, borderRadius: 1, border: "1px solid",
                borderColor: alpha(verdict.tone, 0.35),
                bgcolor: (t) => alpha(verdict.tone, t.palette.mode === "dark" ? 0.1 : 0.05),
              }}
            >
              <Iconify
                icon={verdict.tone === "#16A34A" ? "solar:check-circle-bold" : "solar:danger-triangle-bold"}
                width={16} sx={{ color: verdict.tone, flexShrink: 0, mt: "1px" }}
              />
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>{verdict.title}</Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>{verdict.body}</Typography>
                {!!result.hollow?.length && (
                  <TaskLinks ids={result.hollow.map((h) => h.id)} tasks={tasks} onOpen={onOpenTask} />
                )}
              </Box>
            </Stack>
          )}

          {/*
            The winner as an action — right after the numbers, so "what
            now" is answered before the trials list. Two paths sit here:
            run the winner in simulation as v_next, or export the code
            (patch, zip, clipboard, PR). Everything below stays for the
            reader who wants to walk the search.
          */}
          <WinnerActions
            env={env}
            envState={envState}
            winner={result.winner}
            onRun={() => onRerun?.(result.winner.proposals || [])}
          />

          {/* ── graph ── */}
          <Box sx={{ px: 2.5, pt: 2.5 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700, mb: 1 }}>Best so far</Typography>
            <Growth trials={result.trials} base={result.base} theme={theme} winner={result.winner} />
          </Box>

          {/* ── trials, two ways to read them ── */}
          <Box sx={{ px: 2.5, pt: 2 }}>
            <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
              <Tabs
                value={tab} onChange={(_, v) => setTab(v)}
                sx={{ px: 1.5, minHeight: 40, borderBottom: "1px solid", borderColor: "divider" }}
              >
                <Tab value="trials" label="Trials" sx={{ minHeight: 40 }} />
                <Tab value="code" label="Code" sx={{ minHeight: 40 }} />
                <Tab value="grid" label="Per scenario" sx={{ minHeight: 40 }} />
              </Tabs>

              {tab === "trials" && (
                <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
                  {result.trials.map((t) => {
                    const win = t.n === result.winner.n;
                    const open = openTrial === t.n;
                    return (
                      <Box key={t.n}>
                        <Stack
                          direction="row" alignItems="center" spacing={1.5}
                          onClick={() => setOpenTrial(open ? null : t.n)}
                          sx={{
                            px: 2, py: 1.25, cursor: "pointer",
                            bgcolor: (th) => (win ? alpha("#16A34A", th.palette.mode === "dark" ? 0.08 : 0.04) : "transparent"),
                            "&:hover": { bgcolor: "action.hover" },
                          }}
                        >
                          <Typography sx={{ typography: "s3", color: "text.disabled", width: 26, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
                            {t.n}
                          </Typography>
                          <Typography sx={{ typography: "s2", fontWeight: 700, width: 44, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
                            {t.score}%
                          </Typography>
                          <Typography
                            sx={{
                              typography: "s3", width: 40, flexShrink: 0, fontVariantNumeric: "tabular-nums",
                              color: t.delta > 0 ? "#16A34A" : t.delta < 0 ? "#DC2626" : "text.disabled",
                            }}
                          >
                            {t.delta > 0 ? `+${t.delta}` : t.delta}
                          </Typography>
                          <Typography sx={{ typography: "s2", color: "text.subtitle", flex: 1, minWidth: 0 }} noWrap>
                            {t.tried}
                          </Typography>
                          {t.brokeBlocker && (
                            <Tooltip arrow title="Rejected — broke a scenario that blocks a release">
                              <Chip
                                size="small" label="blocker"
                                sx={{
                                  height: 18, borderRadius: 0.5, flexShrink: 0, color: "#DC2626",
                                  border: "1px solid", borderColor: alpha("#DC2626", 0.4), bgcolor: "transparent",
                                  "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 700 },
                                }}
                              />
                            </Tooltip>
                          )}
                          {win && (
                            <Chip
                              size="small" label="winner"
                              sx={{
                                height: 18, borderRadius: 0.5, flexShrink: 0, color: "#16A34A",
                                border: "1px solid", borderColor: alpha("#16A34A", 0.4), bgcolor: "transparent",
                                "& .MuiChip-label": { px: 0.625, typography: "s3", fontWeight: 700 },
                              }}
                            />
                          )}
                          <Iconify
                            icon={open ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
                            width={14} sx={{ color: "text.disabled", flexShrink: 0 }}
                          />
                        </Stack>
                        {open && (
                          <Stack spacing={0.5} sx={{ px: 2, pb: 2, pl: 6 }}>
                            {t.lines.length ? t.lines.map((line) => (
                              <Stack
                                key={line} direction="row" spacing={1}
                                sx={{ px: 1.5, py: 1, borderRadius: 1, bgcolor: (th) => alpha("#16A34A", th.palette.mode === "dark" ? 0.1 : 0.05) }}
                              >
                                <Typography sx={{ typography: "s2", fontWeight: 700, color: "#16A34A", flexShrink: 0 }}>+</Typography>
                                <Typography sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" }}>
                                  {line}
                                </Typography>
                              </Stack>
                            )) : (
                              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                                This candidate changed nothing — the score is the starting prompt re-run, which is
                                how much of the spread between trials is noise rather than the search.
                              </Typography>
                            )}
                          </Stack>
                        )}
                      </Box>
                    );
                  })}
                </Stack>
              )}

              {tab === "code" && (
                <TrialCodeView
                  env={env}
                  trials={result.trials}
                  pool={result.pool || []}
                  winner={result.winner}
                />
              )}

              {tab === "grid" && (
                <TrialHeatmap trials={result.trials} scenarios={result.trainMeasuredTasks || []} winner={result.winner} />
              )}
            </Box>

            <Box sx={{ mt: 0.5, mx: -1 }}>
              <RunTraceLog
                label={`Searched by ${result.optimizer.label} · ${result.trials.length} trials`}
                steps={searchTrace(result)}
              />
            </Box>
          </Box>

          {/* ── the winner still has to reach the agent ── */}
          <Box sx={{ px: 2.5, py: 2.5 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>Hand the winner over</Typography>
            {/*
              The boundary between what was optimised and what decides a
              release. The search moves the pass rate; the gate also weighs mean
              return, latency and cost. Calling a winning prompt "released"
              because one of those numbers went up is how a regression on the
              other three ships unnoticed.
            */}
            <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>
              This search optimised the pass rate. The release gate also weighs mean return, latency and cost —
              so the winner is a candidate for release, and the gate still has to be run against it.
            </Typography>
            <OmegaHandoff
              env={env}
              envState={envState}
              patch={patch}
              included={result.winner.proposals || []}
              projected={result.heldScore}
              current={result.heldBase}
              willFix={result.winner.fixed || 0}
              onRerun={() => onRerun?.(result.winner.proposals || [])}
            />
          </Box>
        </Box>
      )}
    </Stack>
  );
}

OptimizationRunView.propTypes = {
  record: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  tasks: PropTypes.array,
  onOpenTask: PropTypes.func,
  onBack: PropTypes.func,
  onClose: PropTypes.func,
  onDone: PropTypes.func,
  onRerun: PropTypes.func,
};

function Metric({ label, value, sub, tone, hint }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
        {hint && (
          <Tooltip arrow title={hint}>
            <Box component="span" sx={{ display: "flex" }}>
              <Iconify icon="solar:info-circle-linear" width={12} sx={{ color: "text.disabled" }} />
            </Box>
          </Tooltip>
        )}
      </Stack>
      <Typography sx={{ typography: "m2", fontWeight: 700, color: tone || "text.primary", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      {sub && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{sub}</Typography>}
    </Box>
  );
}

Metric.propTypes = {
  label: PropTypes.string, value: PropTypes.string, sub: PropTypes.string,
  tone: PropTypes.string, hint: PropTypes.string,
};

/**
 * Best-so-far against trial number.
 *
 * Both series, deliberately. The staircase alone makes every search look like
 * it worked; the scatter under it shows how many candidates came in below where
 * it started, which is what says whether the algorithm searched or got lucky.
 */
function Growth({ trials, base, theme, winner }) {
  const W = 640;
  const H = 150;
  const L = 34;
  const T = 10;
  const B = 20;
  const scores = trials.map((t) => t.score);
  const top = Math.min(100, Math.max(base, ...scores) + 6);
  const bottom = Math.max(0, Math.min(base, ...scores) - 6);
  const x = (i) => L + (i / Math.max(1, trials.length - 1)) * (W - L - 12);
  const y = (v) => T + (1 - (v - bottom) / Math.max(1, top - bottom)) * (H - T - B);

  const stair = trials.map((t, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(t.bestSoFar)}`).join(" ");
  const area = `${stair} L${x(trials.length - 1)},${y(bottom)} L${x(0)},${y(bottom)} Z`;
  const ticks = [bottom, Math.round((bottom + top) / 2), top];
  const winIndex = trials.findIndex((t) => t.n === winner?.n);

  return (
    <Box sx={{ width: "100%", maxWidth: 860 }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ display: "block", width: "100%", height: "auto" }}>
        <defs>
          <linearGradient id="opt-growth" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#16A34A" stopOpacity={theme.palette.mode === "dark" ? 0.22 : 0.14} />
            <stop offset="100%" stopColor="#16A34A" stopOpacity={0} />
          </linearGradient>
        </defs>

        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - 12} y1={y(v)} y2={y(v)} stroke={theme.palette.divider} strokeWidth={1} />
            <text
              x={L - 7} y={y(v) + 3} textAnchor="end"
              style={{ font: "500 9.5px ui-monospace, Menlo, monospace", fill: theme.palette.text.disabled }}
            >
              {v}%
            </text>
          </g>
        ))}

        <line
          x1={L} x2={W - 12} y1={y(base)} y2={y(base)}
          stroke={theme.palette.text.disabled} strokeWidth={1} strokeDasharray="3 3"
        />
        <text
          x={W - 12} y={y(base) - 5} textAnchor="end"
          style={{ font: "500 9.5px ui-monospace, Menlo, monospace", fill: theme.palette.text.disabled }}
        >
          starting prompt
        </text>

        <path d={area} fill="url(#opt-growth)" />
        <path d={stair} fill="none" stroke="#16A34A" strokeWidth={1.75} strokeLinejoin="round" />

        {trials.map((t, i) => (
          <g key={t.n}>
            <circle
              cx={x(i)} cy={y(t.score)} r={i === winIndex ? 4 : 2.75}
              fill={i === winIndex ? "#16A34A" : theme.palette.background.paper}
              stroke={t.brokeBlocker ? "#DC2626" : t.score >= base ? "#16A34A" : "#94A3B8"}
              strokeWidth={1.5}
            />
            {i === winIndex && (
              <text
                x={i === 0 ? x(i) + 7 : x(i)} y={y(t.score) - 9}
                textAnchor={i === 0 ? "start" : "middle"}
                style={{ font: "700 9.5px ui-monospace, Menlo, monospace", fill: "#16A34A" }}
              >
                {t.score}%
              </text>
            )}
          </g>
        ))}

        {trials.map((t, i) => (
          (i === 0 || i === trials.length - 1 || (i + 1) % Math.ceil(trials.length / 6) === 0) ? (
            <text
              key={`x${t.n}`} x={x(i)} y={H - 5} textAnchor="middle"
              style={{ font: "500 9.5px ui-monospace, Menlo, monospace", fill: theme.palette.text.disabled }}
            >
              {t.n}
            </text>
          ) : null
        ))}
      </svg>
    </Box>
  );
}

Growth.propTypes = {
  trials: PropTypes.array, base: PropTypes.number, theme: PropTypes.object, winner: PropTypes.object,
};

/**
 * The code, evolving trial by trial.
 *
 * The concept: an optimizer trial is not just a score — it is a version of
 * the agent's code. Each trial's `picks` map to a subset of the candidate
 * proposals, and each proposal has a filepath and a diff. So per trial we
 * can reconstruct what the agent's files would look like at that trial —
 * and, more usefully, the delta from the previous trial's version.
 *
 * Layout: rail of trials on the left (scores, delta, winner badge, dot on
 * the currently-selected one), code pane on the right showing every file
 * this trial touches. Lines the trial added are green, lines it removed
 * are red; a small "added / removed / unchanged from trial N-1" badge sits
 * at the top of each file. Winner is preselected so you land on it.
 */
function TrialCodeView({ env, trials, pool, winner }) {
  const proposalById = useMemo(
    () => Object.fromEntries((pool || []).map((p) => [p.id, p])),
    [pool],
  );
  const [selected, setSelected] = useState(() => winner?.n || trials[0]?.n);

  const trial = trials.find((t) => t.n === selected) || trials[0];
  const prev = trials.find((t) => t.n === selected - 1);

  /* The candidate proposals this trial ran with, and the ones the previous
     trial ran with — used together to show what CHANGED between them. */
  const currentPicks = (trial?.picks || []).map((id) => proposalById[id]).filter(Boolean);
  const prevPicks = (prev?.picks || []).map((id) => proposalById[id]).filter(Boolean);

  /* Every file the current trial's picks touch, plus the picks assigned to
     that file for both the current and the previous trial, so the per-file
     view can render the state and the delta. */
  const files = useMemo(() => {
    const map = new Map();
    currentPicks.forEach((p) => {
      const file = changeFileFor(env, p);
      if (!file) return;
      if (!map.has(file.path)) {
        map.set(file.path, { file, current: [], previous: [] });
      }
      map.get(file.path).current.push(p);
    });
    prevPicks.forEach((p) => {
      const file = changeFileFor(env, p);
      if (!file) return;
      if (!map.has(file.path)) {
        map.set(file.path, { file, current: [], previous: [] });
      }
      map.get(file.path).previous.push(p);
    });
    return [...map.values()].sort((a, b) => a.file.path.localeCompare(b.file.path));
  }, [currentPicks, prevPicks, env]);

  return (
    <Stack direction="row" sx={{ minHeight: 0 }}>
      {/* ── rail: trials ── */}
      <Box
        sx={{
          width: 200, flexShrink: 0, borderRight: "1px solid", borderColor: "divider",
          maxHeight: 520, overflowY: "auto",
        }}
      >
        {trials.map((t) => {
          const win = t.n === winner?.n;
          const on = t.n === selected;
          return (
            <Stack
              key={t.n} direction="row" alignItems="center" spacing={1}
              onClick={() => setSelected(t.n)}
              sx={{
                px: 1.5, py: 1, cursor: "pointer",
                borderLeft: "2px solid",
                borderLeftColor: on ? "#7857FC" : "transparent",
                bgcolor: (th) => (on
                  ? alpha("#7857FC", th.palette.mode === "dark" ? 0.08 : 0.04)
                  : "transparent"),
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Typography sx={{ typography: "s3", color: "text.disabled", width: 22, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>
                {t.n}
              </Typography>
              <Typography sx={{ typography: "s2", fontWeight: 700, fontVariantNumeric: "tabular-nums", width: 40, flexShrink: 0 }}>
                {t.score}%
              </Typography>
              <Typography
                sx={{
                  typography: "s3", flex: 1, fontVariantNumeric: "tabular-nums", textAlign: "right",
                  color: t.delta > 0 ? "#16A34A" : t.delta < 0 ? "#DC2626" : "text.disabled",
                }}
              >
                {t.delta > 0 ? `+${t.delta}` : t.delta}
              </Typography>
              {win && (
                <Iconify icon="solar:crown-bold" width={12} sx={{ color: "#CA8A04", flexShrink: 0 }} />
              )}
            </Stack>
          );
        })}
      </Box>

      {/* ── pane: files for the selected trial ── */}
      <Box sx={{ flex: 1, minWidth: 0, p: 2, maxHeight: 520, overflowY: "auto" }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>
            Trial {trial?.n} · agent code
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {prev ? `changes from trial ${prev.n}` : "the starting point"}
          </Typography>
          <Box flex={1} />
          {trial?.n === winner?.n && (
            <Chip
              size="small" label="winner"
              sx={{
                height: 20, borderRadius: 0.5, color: "#CA8A04",
                border: "1px solid", borderColor: alpha("#CA8A04", 0.4), bgcolor: "transparent",
                "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700 },
              }}
            />
          )}
        </Stack>

        {files.length === 0 ? (
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            This trial re-ran the incumbent — no code changed since the last one.
          </Typography>
        ) : (
          <Stack spacing={1.5}>
            {files.map(({ file, current, previous }) => (
              <FileTrialDiff
                key={file.path}
                file={file}
                current={current}
                previous={previous}
                showDelta={!!prev}
              />
            ))}
          </Stack>
        )}
      </Box>
    </Stack>
  );
}

TrialCodeView.propTypes = {
  env: PropTypes.object, trials: PropTypes.array, pool: PropTypes.array, winner: PropTypes.object,
};

/*
  One file's diff at one trial.

  Each proposal that touches this file is rendered as a block. Lines that
  were newly added by THIS trial (not present in the previous trial's
  picks) get a purple "new" dot so it is visible what the search's most
  recent step actually did.
*/
function FileTrialDiff({ file, current, previous, showDelta }) {
  const prevIds = new Set(previous.map((p) => p.id));
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 1.5, py: 0.875, bgcolor: "background.neutral", borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Iconify icon="solar:file-text-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography noWrap sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary", flex: 1 }}>
          {file.path}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.disabled" }}>{file.language}</Typography>
      </Stack>

      <Stack divider={<Box sx={{ borderTop: "1px dashed", borderColor: "divider" }} />}>
        {current.map((p) => {
          const isNewInThisTrial = showDelta && !prevIds.has(p.id);
          return (
            <Box key={p.id} sx={{ px: 1.5, py: 1.25 }}>
              <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.5 }}>
                {isNewInThisTrial && (
                  <Box
                    sx={{
                      width: 6, height: 6, borderRadius: "50%", bgcolor: "#7857FC", flexShrink: 0,
                    }}
                  />
                )}
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>{p.title}</Typography>
                {isNewInThisTrial && (
                  <Typography sx={{ typography: "s3", color: "#7857FC", fontWeight: 700 }}>
                    added this trial
                  </Typography>
                )}
              </Stack>
              <Stack spacing={0.375}>
                {p.diff.map((d, i) => (
                  <Stack
                    key={`${d.type}-${i}`} direction="row" spacing={1}
                    sx={{
                      px: 1, py: 0.5, borderRadius: 0.5,
                      bgcolor: (t) => alpha(d.type === "add" ? "#16A34A" : "#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
                    }}
                  >
                    <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", fontWeight: 700, flexShrink: 0, color: d.type === "add" ? "#16A34A" : "#DC2626" }}>
                      {d.type === "add" ? "+" : "−"}
                    </Typography>
                    <Typography sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: d.type === "add" ? "text.primary" : "text.disabled", textDecoration: d.type === "add" ? "none" : "line-through" }}>
                      {d.text}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </Box>
          );
        })}
      </Stack>
    </Box>
  );
}

FileTrialDiff.propTypes = {
  file: PropTypes.object, current: PropTypes.array, previous: PropTypes.array, showDelta: PropTypes.bool,
};

/**
 * What to do with the winner.
 *
 * The optimizer produced a candidate; this bar is the two decisions the
 * user cares about — run it against the environment (it lands as a new
 * agent version, ready to be compared to v1 in the runs table), or
 * export the code (four routes: patch download, zip download, clipboard
 * copy, GitHub PR). Both are one click from here so a user doesn't have
 * to scroll past the trial history to make either.
 */
function WinnerActions({ env, envState, winner, onRun }) {
  const [exportAnchor, setExportAnchor] = useState(null);
  const [feedback, setFeedback] = useState(null);
  const closeExport = () => setExportAnchor(null);

  const proposals = winner?.proposals || [];
  const filed = proposals
    .map((p) => ({ p, file: changeFileFor(env, p) }))
    .filter((row) => row.file);

  /* The diff, as text — one hunk per file, `+` for added lines and `−`
     for removed. Human-readable, and small enough to hold a whole set of
     optimizer proposals without any special formatter. */
  const patchText = () => {
    const groups = new Map();
    filed.forEach(({ p, file }) => {
      if (!groups.has(file.path)) groups.set(file.path, { file, changes: [] });
      groups.get(file.path).changes.push(p);
    });
    return [...groups.values()].map(({ file, changes }) => {
      const lines = [`# ${file.path}`];
      changes.forEach((p) => {
        lines.push(`## ${p.kind}: ${p.title}`);
        p.diff.forEach((d) => {
          lines.push(`${d.type === "add" ? "+" : "-"} ${d.text}`);
        });
      });
      return lines.join("\n");
    }).join("\n\n");
  };

  const downloadBlob = (name, mime, contents) => {
    const blob = new Blob([contents], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const doPatch = () => {
    downloadBlob(`${env?.id || "agent"}-optimized.diff`, "text/plain", patchText());
    setFeedback("patch");
    closeExport();
  };
  const doZip = () => {
    /* The prototype ships the diff as a text bundle rather than a real
       zip — a real backend would build the tree with the changes applied
       and stream it back. Named .zip so the flow reads correctly. */
    downloadBlob(`${env?.id || "agent"}-optimized-bundle.txt`, "text/plain", patchText());
    setFeedback("zip");
    closeExport();
  };
  const doCopy = () => {
    navigator.clipboard?.writeText(patchText()).catch(() => {});
    setFeedback("clipboard");
    closeExport();
  };
  const doPR = () => {
    /* Reuses the repo-connect flow the OmegaHandoff panel below already
       handles — the button here opens the same panel by scrolling to it.
       Without a repo we can't open a PR ourselves, so the user is
       redirected to the connect flow rather than being handed a broken
       success state. */
    closeExport();
    const repo = envState?.agentRepo?.url || (env?.builtFrom?.kind === "repo" ? env.builtFrom.value : null);
    if (!repo) {
      setFeedback("need-repo");
    } else {
      setFeedback("pr");
    }
  };

  return (
    <Box sx={{ px: 2.5, pt: 2.5 }}>
      <Stack
        direction="row" alignItems="center" spacing={1.5} flexWrap="wrap" rowGap={1.25}
        sx={{
          px: 2, py: 1.75, borderRadius: 1.25, border: "1px solid",
          borderColor: (t) => alpha("#16A34A", 0.35),
          bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.08 : 0.04),
        }}
      >
        <Iconify icon="solar:crown-bold" width={16} sx={{ color: "#CA8A04", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>
            Trial {winner?.n} wins · +{winner?.delta}%
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {filed.length} {filed.length === 1 ? "change" : "changes"} across{" "}
            {new Set(filed.map((r) => r.file.path)).size} {new Set(filed.map((r) => r.file.path)).size === 1 ? "file" : "files"}. Ready to run or export.
          </Typography>
        </Box>
        <Button
          variant="outlined" size="small"
          onClick={(e) => setExportAnchor(e.currentTarget)}
          startIcon={<Iconify icon="solar:download-minimalistic-linear" width={15} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
          sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
        >
          Export
        </Button>
        <Button
          variant="contained" color="primary" size="small"
          onClick={onRun}
          startIcon={<Iconify icon="solar:play-bold" width={15} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Run in simulation
        </Button>
      </Stack>

      {feedback && (
        <Typography sx={{ typography: "s3", color: feedback === "need-repo" ? "#CA8A04" : "text.subtitle", mt: 0.875, ml: 0.5 }}>
          {feedback === "patch"     && "Diff downloaded — apply with git apply."}
          {feedback === "zip"       && "Bundle downloaded — the prototype ships text; a real export would build the tree."}
          {feedback === "clipboard" && "Diff copied to clipboard."}
          {feedback === "pr"        && "Ready to raise — the hand-off panel below carries the PR options."}
          {feedback === "need-repo" && "Connect your agent's repository in the hand-off panel below to open a PR."}
        </Typography>
      )}

      <Menu
        anchorEl={exportAnchor}
        open={!!exportAnchor}
        onClose={closeExport}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 240 } } }}
      >
        <MenuItem onClick={doPatch} sx={{ typography: "s2" }}>
          <ListItemIcon sx={{ minWidth: 28 }}>
            <Iconify icon="solar:file-download-linear" width={16} />
          </ListItemIcon>
          <ListItemText primary="Download patch (.diff)" primaryTypographyProps={{ sx: { typography: "s2" } }} />
        </MenuItem>
        <MenuItem onClick={doZip} sx={{ typography: "s2" }}>
          <ListItemIcon sx={{ minWidth: 28 }}>
            <Iconify icon="solar:archive-down-minimlistic-linear" width={16} />
          </ListItemIcon>
          <ListItemText primary="Download agent as zip" primaryTypographyProps={{ sx: { typography: "s2" } }} />
        </MenuItem>
        <MenuItem onClick={doCopy} sx={{ typography: "s2" }}>
          <ListItemIcon sx={{ minWidth: 28 }}>
            <Iconify icon="solar:copy-linear" width={16} />
          </ListItemIcon>
          <ListItemText primary="Copy diff to clipboard" primaryTypographyProps={{ sx: { typography: "s2" } }} />
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />
        <MenuItem onClick={doPR} sx={{ typography: "s2" }}>
          <ListItemIcon sx={{ minWidth: 28 }}>
            <Iconify icon="solar:code-square-linear" width={16} />
          </ListItemIcon>
          <ListItemText
            primary="Push as GitHub PR"
            secondary={envState?.agentRepo?.url ? envState.agentRepo.url : "Repository not connected"}
            primaryTypographyProps={{ sx: { typography: "s2" } }}
            secondaryTypographyProps={{ sx: { typography: "s3", color: "text.subtitle" } }}
          />
        </MenuItem>
      </Menu>
    </Box>
  );
}

WinnerActions.propTypes = {
  env: PropTypes.object, envState: PropTypes.object, winner: PropTypes.object, onRun: PropTypes.func,
};
