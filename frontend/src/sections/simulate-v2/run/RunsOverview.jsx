import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import {
  previousRunOf,
  runStatsOf,
  sharedDeltaOf,
  sameEnvVersion,
} from "../_mock/runCompare";
import { clickable } from "../components/clickable";

const num = { fontVariantNumeric: "tabular-nums" };
/* A run that carries its own numbers (an improvement's run, scored on
   repeats) is read as given; any other is counted off its tasks. */
const statsOf = (r) => r.stats || runStatsOf(r);
const nameOf = (r) =>
  r.kind === "trial" || r.trialN ? `Trial ${r.trialN}` : `Run ${r.ordinal}`;
const signed = (n) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n)}`;

/**
 * The top of the Runs tab: the latest run's scenario pass rate as one number,
 * how it moved, and the pass rate across every run — with the checks as small
 * toggles that draw themselves on the line.
 */
export default function RunsOverview({
  runs,
  evals,
  referenceVersion,
  onOpenRun,
  headline = null,
  versionOf = undefined,
  /* What a point is called — an improvement's page charts its trials. */
  unit = "run",
}) {
  /* Runs someone started, oldest first — candidates are read by their improvement. */
  const main = useMemo(() => runs.filter((r) => r.kind !== "trial"), [runs]);
  const finished = useMemo(
    () => main.filter((r) => r.finishedAt && statsOf(r).measured > 0),
    [main],
  );
  const latest = finished[finished.length - 1] || null;
  const [focusEval, setFocusEval] = useState(null);

  /* Read against the reference version when the latest run is on another
     one; else against the run before it. Either way only on the same
     environment version — across versions there is no delta. */
  const refRun =
    referenceVersion && latest && latest.agentVersion !== referenceVersion
      ? [...finished]
          .reverse()
          .find(
            (r) =>
              r.agentVersion === referenceVersion &&
              sameEnvVersion(r, latest, versionOf),
          ) || null
      : null;
  const base = refRun || previousRunOf(finished, latest, versionOf);
  const delta = latest && base ? sharedDeltaOf(latest, base) : null;
  const stats = latest ? statsOf(latest) : null;
  /* A caller can headline a run other than the latest — an improvement's
     winner, against the run it started from — with its own comparison. */
  const head = headline?.run || null;
  const headStats = head ? statsOf(head) : stats;
  const focus = evals.find((e) => e.id === focusEval) || null;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 2.5, py: 1.5 }}
      >
        {head ? (
          <Stack
            direction="row"
            alignItems="baseline"
            spacing={1.25}
            sx={{ flex: 1, minWidth: 0 }}
          >
            <Typography
              sx={{ fontSize: 20, fontWeight: 600, lineHeight: 1, ...num }}
            >
              {headStats.scenarioRate}
              <Box
                component="span"
                sx={{
                  fontSize: 13,
                  fontWeight: 500,
                  color: "text.tertiary",
                  ml: 0.25,
                }}
              >
                %
              </Box>
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
            >
              {`${headline.metric || "scenario pass rate"} · ${headline.label || nameOf(head)}`}
              {headline.delta && !headline.delta.flat && (
                <Box component="span" sx={{ ml: 1, color: "text.primary" }}>
                  <Box
                    component="span"
                    sx={{
                      color: headline.delta.up
                        ? "text.secondary"
                        : "status.danger.fg",
                      fontSize: 12,
                      mr: 0.5,
                    }}
                  >
                    {headline.delta.up ? "▲" : "▼"}
                  </Box>
                  {`${headline.delta.text} vs ${headline.against}`}
                </Box>
              )}
            </Typography>
          </Stack>
        ) : stats ? (
          <Stack
            direction="row"
            alignItems="baseline"
            spacing={1.25}
            sx={{ flex: 1, minWidth: 0 }}
          >
            <Typography
              sx={{ fontSize: 20, fontWeight: 600, lineHeight: 1, ...num }}
            >
              {stats.scenarioRate}
              <Box
                component="span"
                sx={{
                  fontSize: 13,
                  fontWeight: 500,
                  color: "text.tertiary",
                  ml: 0.25,
                }}
              >
                %
              </Box>
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
            >
              {`scenario pass rate · ${nameOf(latest)}`}
              {delta && (
                <Tooltip
                  arrow
                  title={`On the ${delta.shared} scenarios both runs scored. Two runs of that size differ by up to ±${delta.noise} pts by chance (95%).`}
                >
                  <Box
                    component="span"
                    sx={{
                      ml: 1,
                      cursor: "default",
                      color: delta.within ? "text.tertiary" : "text.primary",
                    }}
                  >
                    {delta.within ? (
                      `within noise of ${refRun ? `reference ${referenceVersion}` : nameOf(base)}`
                    ) : (
                      <>
                        <Box
                          component="span"
                          sx={{
                            color:
                              delta.pts < 0
                                ? "status.danger.fg"
                                : "text.secondary",
                            fontSize: 12,
                            mr: 0.5,
                          }}
                        >
                          {delta.pts < 0 ? "▼" : "▲"}
                        </Box>
                        {`${Math.abs(delta.pts)} pts vs ${refRun ? `reference ${referenceVersion}` : nameOf(base)}`}
                      </>
                    )}
                  </Box>
                </Tooltip>
              )}
            </Typography>
          </Stack>
        ) : (
          <Typography sx={{ typography: "s1", fontWeight: 600, flex: 1 }}>
            Pass rate by run
          </Typography>
        )}
        {evals.length > 0 && (
          <CheckChips
            runs={finished}
            evals={evals}
            focus={focusEval}
            onFocus={setFocusEval}
          />
        )}
      </Stack>

      <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
        <TrendChart
          unit={unit}
          runs={finished}
          referenceVersion={referenceVersion}
          focusEval={focus}
          onOpenRun={onOpenRun}
        />
      </Box>
    </Box>
  );
}
RunsOverview.propTypes = {
  unit: PropTypes.string,
  /* runSummaries rows (and trials), oldest first. */
  runs: PropTypes.array.isRequired,
  /* [{ id, name, color }] — the environment's evals. */
  evals: PropTypes.array.isRequired,
  referenceVersion: PropTypes.string,
  barPct: PropTypes.number,
  onOpenRun: PropTypes.func,
  /* { run, label, metric?, delta: deltaAgainst(...), against } — headline this run instead of the latest. */
  headline: PropTypes.object,
  /* (run) → its environment version; deltas only within one version. */
  versionOf: PropTypes.func,
};

/* ── the trend ──────────────────────────────────────────────────────────── */

const H = 208;
const PAD = { l: 44, r: 56, t: 38, b: 24 };

/* Scenario pass rate per run — the same number the table prints — as a line,
   failing scenarios as faint bars beneath, agent versions marked across the
   top. Hover a point for its numbers; click to open the run. A focused check
   draws its own dashed line. */
function TrendChart({
  runs,
  referenceVersion,
  focusEval,
  onOpenRun,
  unit = "run",
}) {
  const theme = useTheme();
  const ref = useRef(null);
  const [w, setW] = useState(900);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) =>
      setW(Math.max(320, Math.round(e.contentRect.width))),
    );
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  const points = useMemo(
    () =>
      runs.map((r) => {
        const st = statsOf(r);
        return {
          run: r,
          rate: st.scenarioRate ?? 0,
          failing: st.failed,
          passed: st.scenarioPassed,
          of: st.measured,
        };
      }),
    [runs],
  );

  const caption = (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{ px: 2.5, pt: 1.5 }}
    >
      <Typography
        noWrap
        sx={{ typography: "s3", color: "text.tertiary", flex: 1, minWidth: 0 }}
      >
        {`Each point is one finished ${unit}, oldest to latest${points.length > 1 ? ` · ${points.length} ${unit}s` : ""} · hover for its numbers, click to open`}
      </Typography>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Box
          sx={{
            width: 12,
            height: 2,
            borderRadius: 1,
            bgcolor: "text.primary",
          }}
        />
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Scenario pass rate
        </Typography>
      </Stack>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: "4px",
            bgcolor: (t) => alpha(t.palette.text.primary, 0.16),
          }}
        />
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Failing scenarios
        </Typography>
      </Stack>
      {focusEval && (
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Box
            sx={{
              width: 12,
              borderTop: "2px dashed",
              borderColor: focusEval.color,
            }}
          />
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.tertiary", maxWidth: 160 }}
          >
            {focusEval.name}
          </Typography>
        </Stack>
      )}
    </Stack>
  );

  if (!points.length) {
    return (
      <Box ref={ref}>
        {caption}
        <Typography
          sx={{ px: 2.5, py: 3, typography: "s2", color: "text.tertiary" }}
        >
          No finished run yet — the line starts with the first one.
        </Typography>
      </Box>
    );
  }

  const n = points.length;
  const iw = w - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const inset = n > 1 ? 18 : 0;
  const x = (i) =>
    n === 1 ? PAD.l + iw / 2 : PAD.l + inset + (i / (n - 1)) * (iw - inset * 2);
  const focusVals = focusEval
    ? points.map((p) => p.run.scores?.[focusEval.id] ?? null)
    : [];
  const lowest = Math.min(
    ...points.map((p) => p.rate),
    ...focusVals.filter((v) => v != null),
  );
  const lo = Math.max(0, Math.floor((lowest - 10) / 20) * 20);
  const y = (v) => PAD.t + ih - ((v - lo) / (100 - lo)) * ih;
  const ticks = [];
  for (let t = 100; t >= lo; t -= (100 - lo) / 2) ticks.push(Math.round(t));
  const ink = theme.palette.text.primary;
  const paper = theme.palette.background.paper;
  const sub = theme.palette.text.disabled;
  const maxFail = Math.max(1, ...points.map((p) => p.failing));
  const gap = n > 1 ? (iw - inset * 2) / (n - 1) : iw;
  const bw = Math.max(5, Math.min(22, gap * 0.5));
  /* Where the agent version changes: a dashed rule, its label across the top. */
  const groups = [];
  points.forEach((p, i) => {
    if (!groups.length || groups[groups.length - 1].v !== p.run.agentVersion)
      groups.push({ v: p.run.agentVersion, from: i });
  });
  const step = Math.ceil(n / Math.max(1, Math.floor(iw / 40)));
  const hp = hover != null ? points[hover] : null;
  const line = (vals) =>
    vals
      .map((v, i) => (v == null ? null : `${x(i)},${y(v)}`))
      .filter(Boolean)
      .join(" ");

  return (
    <Box ref={ref} sx={{ position: "relative", minWidth: 0 }}>
      {caption}
      <svg
        width={w}
        height={H}
        role="group"
        aria-label="Scenario pass rate across runs"
        style={{ display: "block", maxWidth: "100%" }}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.l}
              x2={w - PAD.r}
              y1={y(t)}
              y2={y(t)}
              stroke={alpha(ink, 0.07)}
            />
            <text
              x={w - PAD.r + 8}
              y={y(t) + 3.5}
              fontSize="10.5"
              fill={sub}
              style={num}
            >{`${t}%`}</text>
          </g>
        ))}
        {/* axis titles */}
        <text
          x={PAD.l - 10}
          y={H - 6}
          textAnchor="end"
          fontSize="10.5"
          fill={sub}
        >
          {unit === "trial" ? "Trial" : "Run"}
        </text>
        <text
          x={w - 6}
          y={PAD.t - 14}
          textAnchor="end"
          fontSize="10.5"
          fill={sub}
        >
          Pass rate
        </text>
        {/* failing scenarios */}
        {points.map((p, i) => {
          const bh = (p.failing / maxFail) * ih * 0.32;
          return (
            <rect
              key={`f${p.run.id}`}
              x={x(i) - bw / 2}
              y={PAD.t + ih - bh}
              width={bw}
              height={bh}
              rx={1.5}
              fill={alpha(ink, hover === i ? 0.22 : 0.12)}
            />
          );
        })}
        {/* agent versions */}
        {groups.map((g) => {
          const at = g.from === 0 ? PAD.l : (x(g.from) + x(g.from - 1)) / 2;
          return (
            <g key={`v${g.from}`}>
              {g.from > 0 && (
                <line
                  x1={at}
                  x2={at}
                  y1={PAD.t - 18}
                  y2={PAD.t + ih}
                  stroke={alpha(ink, 0.18)}
                  strokeDasharray="3 4"
                />
              )}
              <text
                x={at + (g.from > 0 ? 6 : 0)}
                y={PAD.t - 14}
                fontSize="12"
                fill={alpha(ink, 0.55)}
              >
                {`${g.v}${g.v === referenceVersion ? " ★" : ""}`}
              </text>
            </g>
          );
        })}
        {hp && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={PAD.t - 4}
            y2={PAD.t + ih}
            stroke={alpha(ink, 0.2)}
          />
        )}
        {focusEval && (
          <polyline
            points={line(focusVals)}
            fill="none"
            stroke={focusEval.color}
            strokeWidth={1.5}
            strokeDasharray="4 3"
          />
        )}
        <polyline
          points={line(points.map((p) => p.rate))}
          fill="none"
          stroke={alpha(ink, 0.9)}
          strokeWidth={1.5}
          strokeLinejoin="round"
        />
        {points.map((p, i) => {
          const last = i === n - 1;
          return (
            <g key={p.run.id}>
              <circle
                cx={x(i)}
                cy={y(p.rate)}
                r={last ? 5 : hover === i ? 4.5 : 3.5}
                fill={last ? ink : paper}
                stroke={ink}
                strokeWidth={1.5}
              />
              {last && hover == null && (
                <text
                  x={x(i)}
                  y={y(p.rate) - 11}
                  textAnchor={n > 1 ? "end" : "middle"}
                  fontSize="11.5"
                  fontWeight={600}
                  fill={ink}
                  style={num}
                >{`${p.rate}%`}</text>
              )}
              {(i % step === 0 || last) && (
                <text
                  x={x(i)}
                  y={H - 6}
                  textAnchor="middle"
                  fontSize="10.5"
                  fill={last ? alpha(ink, 0.8) : sub}
                  style={num}
                >
                  {p.run.ordinal}
                </text>
              )}
            </g>
          );
        })}
        {/* hit areas */}
        {points.map((p, i) => (
          <rect
            key={`h${p.run.id}`}
            x={x(i) - Math.max(10, gap / 2)}
            y={PAD.t - 10}
            width={Math.max(20, gap)}
            height={ih + 30}
            fill="transparent"
            style={{ cursor: "pointer" }}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            {...clickable(onOpenRun ? () => onOpenRun(p.run) : null, {
              role: "link",
              label: `Open ${nameOf(p.run)}`,
            })}
          />
        ))}
      </svg>
      {hp && (
        <Box
          sx={{
            position: "absolute",
            pointerEvents: "none",
            zIndex: 2,
            top: 44,
            left: Math.min(Math.max(x(hover) + 12, 8), w - 260),
            px: 1.25,
            py: 0.75,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            boxShadow: (t) => t.customShadows?.z8 || "none",
            whiteSpace: "nowrap",
          }}
        >
          <Typography sx={{ typography: "s2", ...num }}>
            <Box component="span" sx={{ fontWeight: 600 }}>
              {nameOf(hp.run)}
            </Box>
            {` · ${hp.run.agentVersion} · ${hp.rate}% · ${hp.passed}/${hp.of} passed`}
            {focusEval && hp.run.scores?.[focusEval.id] != null
              ? ` · ${focusEval.name} ${hp.run.scores[focusEval.id]}%`
              : ""}
          </Typography>
        </Box>
      )}
    </Box>
  );
}
TrendChart.propTypes = {
  unit: PropTypes.string,
  runs: PropTypes.array,
  referenceVersion: PropTypes.string,
  focusEval: PropTypes.object,
  onOpenRun: PropTypes.func,
};

/* ── the checks ─────────────────────────────────────────────────────────── */

/* Each eval as a compact toggle: its latest score and how it moved since the
   run before, lowest first. On, it draws its line over the pass rate. */
function CheckChips({ runs, evals, focus, onFocus }) {
  const latest = runs[runs.length - 1] || null;
  const prev = runs[runs.length - 2] || null;
  const rows = evals
    .map((e) => {
      const now = latest?.scores?.[e.id] ?? null;
      const before = prev?.scores?.[e.id] ?? null;
      return {
        ...e,
        now,
        d: now != null && before != null ? now - before : null,
      };
    })
    .sort((a, b) => (a.now ?? 101) - (b.now ?? 101));
  const shown = rows.slice(0, 4);
  return (
    <Stack direction="row" spacing={0.75} sx={{ flexShrink: 0, minWidth: 0 }}>
      {shown.map((e) => {
        const on = focus === e.id;
        return (
          <Tooltip
            key={e.id}
            arrow
            title={`${e.name} in ${latest ? nameOf(latest) : "the latest run"}${e.d != null && prev ? `, ${signed(e.d)} vs ${nameOf(prev)}` : ""}. ${on ? "Click to take it off the chart." : "Click to draw it on the chart."}`}
          >
            <Box
              role="button"
              tabIndex={0}
              aria-pressed={on}
              onClick={() => onFocus(on ? null : e.id)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter") onFocus(on ? null : e.id);
              }}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.75,
                height: 28,
                px: 1.25,
                borderRadius: "4px",
                cursor: "pointer",
                maxWidth: 240,
                border: "1px solid",
                borderColor: on ? "text.disabled" : "divider",
                bgcolor: on ? "action.hover" : "transparent",
                "&:hover": { borderColor: "text.disabled" },
              }}
            >
              {on && (
                <Box
                  sx={{
                    width: 7,
                    height: 7,
                    borderRadius: "50%",
                    bgcolor: e.color,
                    flexShrink: 0,
                  }}
                />
              )}
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
              >
                {e.name}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.primary",
                  flexShrink: 0,
                  ...num,
                }}
              >
                {e.now == null ? "—" : `${e.now}%`}
              </Typography>
              {e.d ? (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                    ...num,
                  }}
                >
                  {signed(e.d)}
                </Typography>
              ) : null}
            </Box>
          </Tooltip>
        );
      })}
      {rows.length > shown.length && (
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", alignSelf: "center" }}
        >{`+${rows.length - shown.length}`}</Typography>
      )}
    </Stack>
  );
}
CheckChips.propTypes = {
  runs: PropTypes.array,
  evals: PropTypes.array,
  focus: PropTypes.string,
  onFocus: PropTypes.func,
};
