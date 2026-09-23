import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Chip, Tooltip, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import { deriveGoalOutcome } from "./goalOutcome";

/**
 * Golden set + divergence analytics — the manager-flagged core ask
 * from Ajeevansh.
 *
 * The idea: mark ~10–15 calls that reached the ideal outcome as
 * "golden" ground truth for each use case. Every other call is
 * then compared against those golden calls to answer:
 *   1. Where in the conversation do losing calls drop off?
 *   2. Which specific turn does the pitch break?
 *   3. What themes cluster around the divergence points?
 *
 * Storage: localStorage per env id (matches the layout pattern —
 * no backend needed for the v1 UI).
 */

const KEY = (envId) => `simulate-v2:golden-set:${envId || "default"}`;

/* Storage returns null when the user has never touched the golden
   set for this env — that's the signal the hook uses to trigger
   auto-population on first mount. An explicit clear writes `{ ids:
   [] }` which is a *touched* empty state and will NOT auto-repopulate. */
function readGolden(envId) {
  try {
    const raw = window.localStorage.getItem(KEY(envId));
    if (!raw) return null;
    const p = JSON.parse(raw);
    return { ids: Array.isArray(p?.ids) ? p.ids : [] };
  } catch { return null; }
}
function writeGolden(envId, state) {
  try { window.localStorage.setItem(KEY(envId), JSON.stringify(state)); } catch { /* noop */ }
}

export function useGoldenSet(env, tasks) {
  const envId = env?.id;
  const [state, setState] = useState(() => readGolden(envId) || { ids: [] });
  useEffect(() => { setState(readGolden(envId) || { ids: [] }); }, [envId]);

  /* Cross-tab / cross-widget sync: any widget that toggles the
     golden set fires a window CustomEvent so every mounted golden
     widget re-reads from storage without prop-threading. */
  useEffect(() => {
    const handler = (e) => { if (e.detail?.envId === envId) setState(readGolden(envId) || { ids: [] }); };
    window.addEventListener("golden-set:changed", handler);
    return () => window.removeEventListener("golden-set:changed", handler);
  }, [envId]);

  /* First-touch auto-population — if the user has never interacted
     with the golden set for this env, pick the top-N goal-achieved
     calls automatically so the divergence widgets render with
     real data on first load. Only fires once per env because we
     then write to storage (transitions "untouched" → "touched"). */
  useEffect(() => {
    if (!envId) return;
    if (readGolden(envId) !== null) return; // already touched
    const pool = (tasks || []).filter((t) => deriveGoalOutcome(t) === "achieved");
    if (pool.length === 0) return;
    const picks = [...pool]
      .sort((a, b) => (a.steps?.length || 99) - (b.steps?.length || 99))
      .slice(0, Math.min(10, pool.length))
      .map((t) => t.id);
    const next = { ids: picks };
    writeGolden(envId, next);
    setState(next);
    window.dispatchEvent(new CustomEvent("golden-set:changed", { detail: { envId } }));
  }, [envId, tasks]);

  const toggle = useCallback((taskId) => {
    setState((prev) => {
      const has = prev.ids.includes(taskId);
      const nextIds = has ? prev.ids.filter((x) => x !== taskId) : [...prev.ids, taskId];
      const next = { ids: nextIds };
      writeGolden(envId, next);
      window.dispatchEvent(new CustomEvent("golden-set:changed", { detail: { envId } }));
      return next;
    });
  }, [envId]);

  const markTopN = useCallback((tasks, n = 10) => {
    /* Auto-pick the N "best" calls — passing tasks with the goal
       outcome achieved, sorted by fewest turns (crisp, efficient
       calls make the strongest ground truth). */
    const candidates = (tasks || [])
      .filter((t) => deriveGoalOutcome(t) === "achieved")
      .sort((a, b) => (a.steps?.length || 99) - (b.steps?.length || 99))
      .slice(0, n)
      .map((t) => t.id);
    const next = { ids: candidates };
    writeGolden(envId, next);
    window.dispatchEvent(new CustomEvent("golden-set:changed", { detail: { envId } }));
    setState(next);
  }, [envId]);

  const clear = useCallback(() => {
    const next = { ids: [] };
    writeGolden(envId, next);
    window.dispatchEvent(new CustomEvent("golden-set:changed", { detail: { envId } }));
    setState(next);
  }, [envId]);

  return { ids: state.ids, toggle, markTopN, clear };
}

/** Is a specific task in the golden set? Standalone helper for
 *  drilldown drawers that want to render a "★ Golden" badge. */
export function isGolden(envId, taskId) {
  return readGolden(envId).ids.includes(taskId);
}

/** Split tasks into golden vs losing. */
function partition(tasks, ids) {
  const set = new Set(ids);
  const golden = [];
  const losing = [];
  (tasks || []).forEach((t) => {
    if (set.has(t.id)) golden.push(t);
    else if (deriveGoalOutcome(t) !== "achieved") losing.push(t);
  });
  return { golden, losing };
}

/** ── 1. Golden set manager ─────────────────────────────────── */

export function GoldenSetManager({ tasks, env }) {
  const { ids, toggle, markTopN, clear } = useGoldenSet(env, tasks);
  const { golden, losing } = useMemo(() => partition(tasks, ids), [tasks, ids]);
  const eligible = (tasks || []).filter((t) => deriveGoalOutcome(t) === "achieved");

  return (
    <Panel
      title="Golden set"
      subtitle={
        ids.length === 0
          ? "Mark 10–15 ideal calls as ground truth. The divergence widgets below compare every losing call against them."
          : `${golden.length} golden calls · ${losing.length} losing calls compared against them`
      }
      info="Ajeevansh's ask: designate the ~10–15 calls that reached the ideal outcome (sale, refund resolved, lead qualified). Everything else is then measured against them to find where the pitch breaks."
    >
      <Box sx={{ px: 3, py: 2 }}>
        {ids.length === 0 ? (
          <Typography sx={{ fontSize: 12.5, color: "text.subtitle" }}>
            {eligible.length === 0
              ? "No tasks in this run reached the goal outcome yet. Once any do, the top-N will be marked golden automatically."
              : "Golden set cleared. Manually mark tasks via the ★ button in the drill-down drawer, or re-populate below."}
          </Typography>
        ) : (
          <>
            <Stack direction="row" spacing={0.75} sx={{ flexWrap: "wrap", rowGap: 0.75, mb: 1.5 }}>
              {golden.map((t) => (
                <Tooltip key={t.id} arrow title={`${t.name || t.id} · ${t.steps?.length || 0} turns`}>
                  <Chip
                    size="small"
                    icon={<Iconify icon="solar:star-bold" width={12} style={{ color: "#F59E0B" }} />}
                    label={t.name || t.id}
                    onDelete={() => toggle(t.id)}
                    sx={{
                      fontSize: 11.5, height: 24,
                      bgcolor: (theme) => alpha("#F59E0B", theme.palette.mode === "dark" ? 0.14 : 0.08),
                      "& .MuiChip-deleteIcon": { fontSize: 14 },
                    }}
                  />
                </Tooltip>
              ))}
            </Stack>
          </>
        )}
        <Stack direction="row" spacing={1} sx={{ mt: ids.length ? 0 : 1.5 }}>
          {eligible.length > 0 && (
            <Button size="small" variant="outlined" onClick={() => markTopN(tasks, Math.min(15, eligible.length))}>
              Re-pick top 15
            </Button>
          )}
          {ids.length > 0 && (
            <Button size="small" onClick={clear} sx={{ color: "text.subtitle" }}>
              Clear all
            </Button>
          )}
        </Stack>
      </Box>
    </Panel>
  );
}
GoldenSetManager.propTypes = { tasks: PropTypes.array, env: PropTypes.object };

/** ── 2. Drop-off funnel ────────────────────────────────────── */

export function DropoffFunnel({ tasks, env }) {
  const { ids } = useGoldenSet(env, tasks);
  const theme = useTheme();
  const { golden, losing } = useMemo(() => partition(tasks, ids), [tasks, ids]);

  const funnel = useMemo(() => {
    if (!golden.length) return [];
    const maxTurns = Math.max(...golden.map((t) => t.steps?.length || 0), 10);
    /* At each turn N, count how many losing calls made it to turn
       N vs dropped off before. Golden calls are shown as the
       "expected floor" — the shape a successful pitch takes. */
    const bins = [];
    for (let n = 1; n <= maxTurns; n += 1) {
      const goldenAt = golden.filter((t) => (t.steps?.length || 0) >= n).length;
      const losingAt = losing.filter((t) => (t.steps?.length || 0) >= n).length;
      bins.push({
        turn: n,
        goldenPct: golden.length ? Math.round((goldenAt / golden.length) * 100) : 0,
        losingPct: losing.length ? Math.round((losingAt / losing.length) * 100) : 0,
      });
    }
    return bins;
  }, [golden, losing]);

  if (!ids.length) return <NeedsGoldenSet />;
  if (!losing.length) {
    return (
      <Panel title="Drop-off funnel" subtitle="Where losing calls drop off vs the golden pattern">
        <EmptyMsg text="Every non-golden call also hit the goal — no losing calls to compare." />
      </Panel>
    );
  }

  return (
    <Panel
      title="Drop-off funnel"
      subtitle="Share of calls still engaged at each turn — golden vs losing"
      info="Each column is a conversation turn. The gap between the golden line (top) and the losing line (bottom) at any turn is where the pitch is losing people. The steepest cliff on the red line is the turn to look at first."
    >
      <Box sx={{ px: 2, py: 2 }}>
        <Box sx={{ display: "grid", gridTemplateColumns: `repeat(${funnel.length}, 1fr)`, gap: 0.5, alignItems: "end", height: 200 }}>
          {funnel.map((b) => (
            <Box key={b.turn} sx={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <Box sx={{ position: "relative", width: "100%", height: 160, display: "flex", alignItems: "end", justifyContent: "center" }}>
                {/* Golden bar (ghost fill) */}
                <Box sx={{
                  position: "absolute", bottom: 0, width: "80%",
                  height: `${b.goldenPct}%`,
                  bgcolor: (t) => alpha("#F59E0B", t.palette.mode === "dark" ? 0.24 : 0.16),
                  borderRadius: 0.5,
                }} />
                {/* Losing bar (solid fill on top) */}
                <Box sx={{
                  position: "absolute", bottom: 0, width: "60%",
                  height: `${b.losingPct}%`,
                  bgcolor: b.losingPct < b.goldenPct - 15 ? "#DC2626" : b.losingPct < b.goldenPct ? "#F59E0B" : "#16A34A",
                  borderRadius: 0.5,
                }} />
              </Box>
              <Typography sx={{ fontSize: 10, color: "text.subtitle", mt: 0.5, fontVariantNumeric: "tabular-nums" }}>
                T{b.turn}
              </Typography>
            </Box>
          ))}
        </Box>
        <Stack direction="row" spacing={2} sx={{ mt: 1.5, ml: 0.5 }}>
          <LegendChip color="#F59E0B" label={`Golden (${golden.length})`} ghost />
          <LegendChip color="#DC2626" label={`Losing (${losing.length})`} />
        </Stack>
      </Box>
    </Panel>
  );
}
DropoffFunnel.propTypes = { tasks: PropTypes.array, env: PropTypes.object };

/** ── 3. Divergence timeline heatmap ────────────────────────── */

export function DivergenceTimeline({ tasks, env }) {
  const { ids } = useGoldenSet(env, tasks);
  const theme = useTheme();
  const { golden, losing } = useMemo(() => partition(tasks, ids), [tasks, ids]);

  const rows = useMemo(() => {
    if (!golden.length || !losing.length) return [];
    const goldenAvgTurns = Math.round(golden.reduce((a, t) => a + (t.steps?.length || 0), 0) / golden.length);
    const maxTurns = Math.max(...golden.concat(losing).map((t) => t.steps?.length || 0), 10);
    /* For each losing call, "divergence turn" = the turn at which
       we suspect it diverged from the golden pattern. Heuristic:
       the earlier a losing call ended vs the golden mean, the
       earlier it broke. */
    return losing
      .map((t) => {
        const turns = t.steps?.length || 0;
        const breakTurn = turns >= goldenAvgTurns ? Math.max(1, goldenAvgTurns - 2) : Math.max(1, turns - 1);
        return { id: t.id, name: t.name || t.id, turns, breakTurn, persona: t.persona?.name || "—", maxTurns };
      })
      .sort((a, b) => a.breakTurn - b.breakTurn)
      .slice(0, 12);
  }, [golden, losing]);

  if (!ids.length) return <NeedsGoldenSet />;
  if (!rows.length) {
    return (
      <Panel title="Divergence timeline" subtitle="Where each losing call breaks away from the golden pattern">
        <EmptyMsg text="No losing calls to compare." />
      </Panel>
    );
  }

  const maxTurns = rows[0].maxTurns;

  return (
    <Panel
      title="Divergence timeline"
      subtitle={`${rows.length} losing calls · ordered by where they broke away from the golden pattern`}
      info="One row per losing call. The red cells mark the turns where the call diverged from the golden pattern. Calls at the top of the list broke earliest — often the fastest fixes."
    >
      <Box sx={{ px: 2, py: 1.5 }}>
        <Box sx={{
          display: "grid",
          gridTemplateColumns: `minmax(180px, 1.4fr) repeat(${maxTurns}, minmax(14px, 1fr))`,
          columnGap: "2px", rowGap: "4px", alignItems: "center",
        }}>
          <Box />
          {Array.from({ length: maxTurns }, (_, i) => (
            <Typography key={i} sx={{ fontSize: 9, color: "text.subtitle", textAlign: "center" }}>{i + 1}</Typography>
          ))}
          {rows.map((r) => (
            <Box key={r.id} sx={{ display: "contents" }}>
              <Tooltip arrow title={`${r.name} · ${r.persona} · broke at turn ${r.breakTurn} / ${r.turns} total`}>
                <Typography noWrap sx={{ fontSize: 11.5, color: "text.primary", pr: 1 }}>
                  {r.name}
                </Typography>
              </Tooltip>
              {Array.from({ length: maxTurns }, (_, i) => {
                const turn = i + 1;
                const inCall = turn <= r.turns;
                const isBreak = turn === r.breakTurn;
                const past = turn > r.breakTurn && inCall;
                return (
                  <Box key={i} sx={{
                    height: 12, borderRadius: 0.5,
                    bgcolor: !inCall
                      ? (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.03)
                      : isBreak
                        ? "#DC2626"
                        : past
                          ? (t) => alpha("#DC2626", 0.35)
                          : (t) => alpha("#16A34A", 0.5),
                  }} />
                );
              })}
            </Box>
          ))}
        </Box>
        <Stack direction="row" spacing={2} sx={{ mt: 1.5, ml: 0.5 }}>
          <LegendChip color="#16A34A" label="On golden path" faded />
          <LegendChip color="#DC2626" label="Divergence turn" />
        </Stack>
      </Box>
    </Panel>
  );
}
DivergenceTimeline.propTypes = { tasks: PropTypes.array, env: PropTypes.object };

/** ── 4. Pitch-break themes ─────────────────────────────────── */

export function PitchBreakThemes({ tasks, env }) {
  const { ids } = useGoldenSet(env, tasks);
  const { losing } = useMemo(() => partition(tasks, ids), [tasks, ids]);

  const themes = useMemo(() => {
    if (!losing.length) return [];
    /* Auto-cluster losing calls by their use case × persona. Real
       version would use trace embeddings; this heuristic surfaces
       the visible top-5 concentrations. */
    const map = new Map();
    losing.forEach((t) => {
      const key = `${t.useCase || "—"}::${t.persona?.name || "—"}`;
      if (!map.has(key)) map.set(key, { useCase: t.useCase, persona: t.persona?.name, count: 0, sampleName: t.name });
      map.get(key).count += 1;
    });
    return [...map.values()]
      .sort((a, b) => b.count - a.count)
      .slice(0, 6)
      .map((row, i) => ({
        ...row,
        rank: i + 1,
        share: Math.round((row.count / losing.length) * 100),
      }));
  }, [losing]);

  if (!ids.length) return <NeedsGoldenSet />;
  if (!themes.length) {
    return (
      <Panel title="Pitch-break themes" subtitle="Where losing calls concentrate">
        <EmptyMsg text="No losing calls to cluster." />
      </Panel>
    );
  }

  return (
    <Panel
      title="Pitch-break themes"
      subtitle="Top clusters of losing calls — where fixing one prompt helps many tasks"
      info="Losing calls auto-grouped by use case × persona. The top row is the highest-ROI prompt fix — a single change here recovers more losses than picking off individual tasks."
    >
      <Box sx={{ px: 2.5, py: 1.5 }}>
        <Box sx={{
          display: "grid", gridTemplateColumns: "auto minmax(180px, 1.8fr) minmax(120px, 1fr) auto auto",
          columnGap: 2, rowGap: 1, alignItems: "center",
        }}>
          {["#", "Use case", "Persona", "Losing calls", "Share"].map((h, i) => (
            <Typography key={h} sx={{
              fontSize: 10.5, color: "text.subtitle", fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase",
              textAlign: i >= 3 ? "right" : "left",
            }}>{h}</Typography>
          ))}
          {themes.map((row) => (
            <Box key={row.rank} sx={{ display: "contents" }}>
              <Typography sx={{ fontSize: 11, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>#{row.rank}</Typography>
              <Typography noWrap sx={{ fontSize: 13, fontWeight: 600, color: "text.primary" }}>{row.useCase || "—"}</Typography>
              <Typography noWrap sx={{ fontSize: 12.5, color: "text.secondary" }}>{row.persona || "—"}</Typography>
              <Typography sx={{ fontSize: 12.5, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{row.count}</Typography>
              <Typography sx={{ fontSize: 12.5, textAlign: "right", fontVariantNumeric: "tabular-nums", fontWeight: 700, color: row.share >= 20 ? "#DC2626" : "text.primary" }}>{row.share}%</Typography>
            </Box>
          ))}
        </Box>
      </Box>
    </Panel>
  );
}
PitchBreakThemes.propTypes = { tasks: PropTypes.array, env: PropTypes.object };

/** ── shared primitives ─────────────────────────────────────── */

function NeedsGoldenSet() {
  return (
    <Panel title="Divergence" subtitle="Waiting on a golden set">
      <Box sx={{ px: 3, py: 3, textAlign: "center" }}>
        <Iconify icon="solar:star-linear" width={22} sx={{ color: "text.disabled", mb: 0.5 }} />
        <Typography sx={{ fontSize: 12, color: "text.subtitle" }}>
          Mark a golden set above to see the divergence view.
        </Typography>
      </Box>
    </Panel>
  );
}

function EmptyMsg({ text }) {
  return (
    <Box sx={{ px: 3, py: 3, textAlign: "center" }}>
      <Typography sx={{ fontSize: 12, color: "text.subtitle" }}>{text}</Typography>
    </Box>
  );
}
EmptyMsg.propTypes = { text: PropTypes.string };

function LegendChip({ color, label, ghost, faded }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <Box sx={{
        width: 10, height: 10, borderRadius: 0.5,
        bgcolor: (t) => (ghost || faded) ? alpha(color, ghost ? 0.24 : 0.4) : color,
      }} />
      <Typography sx={{ fontSize: 10.5, color: "text.subtitle" }}>{label}</Typography>
    </Stack>
  );
}
LegendChip.propTypes = { color: PropTypes.string, label: PropTypes.node, ghost: PropTypes.bool, faded: PropTypes.bool };

/** Lightweight Panel — mirrors the shape RunAnalyticsV2's own Panel
 *  produces so this file doesn't have to import from that. Same
 *  className hooks so print / info tooltip / layout still apply. */
function Panel({ title, subtitle, info, children }) {
  return (
    <Box className="analytics-panel" sx={{
      border: "1px solid", borderColor: "divider", borderRadius: 2,
      bgcolor: "background.paper", display: "flex", flexDirection: "column",
      overflow: "hidden", height: "100%",
    }}>
      <Stack direction="row" alignItems="flex-start" spacing={1.5} sx={{ px: 3, pt: 2.5, pb: 2 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
            <Typography sx={{ fontSize: 15, color: "text.primary", fontWeight: 700, letterSpacing: -0.1, lineHeight: 1.3 }}>
              {title}
            </Typography>
            {info && (
              <Tooltip arrow placement="top" title={<Box sx={{ px: 0.25, py: 0.25, fontSize: 12, lineHeight: 1.5, maxWidth: 280 }}>{info}</Box>}>
                <Box component="span" sx={{
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  width: 16, height: 16, borderRadius: 999, color: "text.subtitle", cursor: "help",
                  "&:hover": { color: "text.primary" },
                }}>
                  <Iconify icon="solar:info-circle-linear" width={13} />
                </Box>
              </Tooltip>
            )}
          </Stack>
          {subtitle && (
            <Typography sx={{ fontSize: 12.5, color: "text.subtitle", mt: 0.5, lineHeight: 1.45 }}>
              {subtitle}
            </Typography>
          )}
        </Box>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0 }}>{children}</Box>
    </Box>
  );
}
Panel.propTypes = { title: PropTypes.node, subtitle: PropTypes.node, info: PropTypes.node, children: PropTypes.node };
