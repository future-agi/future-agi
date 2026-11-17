import { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { alignTrials } from "../_mock/participantsTrials";

/*
  Two trials side by side (proposal 8, 16 Nov): the same lanes, rows lined up
  by who asked whom. A row both trials share is drawn once; a row that went
  differently is shaded with both arrows — trial A in ink, trial B in the
  accent; a row only one trial has says so. Red keeps meaning a problem in
  either trial. Not in Nikhil's plan — a proposal.
*/

const GUT = 150;
const PAD = 64;
const CH = 6.3;
const cut = (s, n) => {
  const t = String(s || "");
  return n < 4 ? "" : t.length <= n ? t : `${t.slice(0, n - 1).trimEnd()}…`;
};

function useWidth(ref) {
  const [w, setW] = useState(820);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) =>
      setW(Math.round(e.contentRect.width)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

export default function CompareSequence({ seqA, seqB, a, b, onStop, onPick }) {
  const theme = useTheme();
  const ref = useRef(null);
  const width = useWidth(ref);
  const [onlyDiff, setOnlyDiff] = useState(false);
  const c = {
    fg: theme.palette.text.primary,
    sub: theme.palette.text.secondary,
    muted: theme.palette.text.tertiary || theme.palette.text.secondary,
    faint: theme.palette.text.disabled,
    line: theme.palette.divider,
    code: theme.palette.background.neutral,
    paper: theme.palette.background.paper,
    bad: theme.palette.error.main,
    accent: theme.palette.primary.main,
    warn: theme.palette.warning.main,
    tint: alpha(theme.palette.warning.main, 0.08),
  };

  /* Lanes: trial A's, then any only trial B has. */
  const lanes = [...seqA.lanes];
  seqB.lanes.forEach((l) => {
    if (!lanes.some((x) => x.id === l.id)) lanes.push(l);
  });
  const L = lanes.length;
  const laneW = Math.max(240, width - GUT);
  const gap = Math.max(
    110,
    Math.min(230, (laneW - 2 * PAD) / Math.max(1, L - 1)),
  );
  const X = Object.fromEntries(
    lanes.map((l, i) => [l.id, GUT + PAD + i * gap]),
  );
  const svgW = Math.max(width, GUT + 2 * PAD + gap * (L - 1));
  const headW = Math.min(150, gap - 8);

  const all = alignTrials(seqA, seqB);
  const rows = onlyDiff ? all.filter((r) => !r.same) : all;
  const sameCount = all.filter((r) => r.same).length;
  const H_ROW = (r) => (r.a && r.b && !r.same ? 60 : 42);
  let y = 76;
  const placed = rows.map((r) => {
    const h = H_ROW(r);
    const out = { ...r, y: y + h / 2, h, top: y };
    y += h;
    return out;
  });
  const H = y + 8;

  const mk = (id, fill) => (
    <marker
      id={id}
      viewBox="0 0 8 8"
      refX="7"
      refY="4"
      markerWidth="7"
      markerHeight="7"
      orient="auto"
    >
      <path d="M0,0 L8,4 L0,8 z" fill={fill} />
    </marker>
  );

  /* One trial's unit at height yy, in its colour; labelBelow for trial B. */
  const unit = (u, yy, base, mkBase, below) => {
    const s = u.step;
    const r = u.row;
    const items = [];
    const ly = below ? yy + 14 : yy - 4;
    if (r.type === "go") {
      const x1 = X[s.from];
      const x2 = X[s.to];
      const dir = x2 > x1 ? 1 : -1;
      const col = s.bad ? c.bad : base;
      const room = Math.floor(Math.max(Math.abs(x2 - x1) - 14, 190) / CH);
      items.push(
        <line
          key="l"
          x1={x1}
          y1={yy}
          x2={x2 - dir * 3}
          y2={yy}
          stroke={col}
          strokeWidth="1.5"
          markerEnd={`url(#${s.bad ? "cq-bad" : mkBase})`}
        />,
        <text
          key="t"
          x={(x1 + x2) / 2}
          y={ly}
          textAnchor="middle"
          fontSize="11.5"
          fontWeight="500"
          fill={s.bad ? c.bad : base}
        >
          {cut(s.label, room)}
          {s.turns > 1 ? ` · ×${s.turns} turns` : ""}
        </text>,
      );
    } else if (r.type === "tool") {
      /* Same as the sequence: a loop on the lane that ran it, then one line
         (Chintan, 17 Nov); left of the lane near the right edge. */
      const x = X[s.from];
      const right = svgW - x - 28;
      const leftSide = right < Math.min(240, x - GUT - 28);
      const room = leftSide ? x - GUT - 28 : right;
      const col = s.bad ? c.bad : base;
      const name = cut(s.tool, Math.floor((room * 0.55) / 6.4));
      const res = cut(
        `→ ${s.result || ""}`,
        Math.floor((room - name.length * 6.4 - 16) / 6),
      );
      const textW = name.length * 6.4 + 8 + res.length * 6;
      const tx = leftSide ? x - 22 - textW : x + 22;
      items.push(
        <rect
          key="bg"
          x={tx - 4}
          y={yy - 9}
          width={textW + 8}
          height={18}
          rx={4}
          fill={c.paper}
        />,
        <path
          key="loop"
          d={
            leftSide
              ? `M${x},${yy - 5} h-12 a5,5 0 0 0 0,10 h9`
              : `M${x},${yy - 5} h12 a5,5 0 0 1 0,10 h-9`
          }
          fill="none"
          stroke={col}
          strokeWidth="1.3"
        />,
        <text
          key="n"
          x={tx}
          y={yy + 4}
          fontSize="10.5"
          fill={col}
          fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
        >
          {name}
        </text>,
        <text
          key="r"
          x={tx + name.length * 6.4 + 8}
          y={yy + 4}
          fontSize="11"
          fill={col}
          opacity={0.85}
        >
          {res}
        </text>,
      );
    } else {
      const took = r.type === "took";
      const x1 = X[s.to];
      const x2 = took ? X[lanes[0].id] : X[s.from];
      const dir = x2 > x1 ? 1 : -1;
      const bad = !!s.answer?.bad;
      const col = bad ? c.bad : base === c.fg ? c.muted : base;
      const room = Math.floor(Math.max(Math.abs(x2 - x1) - 14, 190) / CH);
      items.push(
        <line
          key="l"
          x1={x1}
          y1={yy}
          x2={x2 - dir * 3}
          y2={yy}
          stroke={col}
          strokeWidth="1.3"
          strokeDasharray={r.type === "none" ? "5 3" : "4 3"}
          markerEnd={`url(#${bad ? "cq-bad" : base === c.fg ? "cq-muted" : mkBase})`}
        />,
        <text
          key="t"
          x={(x1 + x2) / 2}
          y={ly}
          textAnchor="middle"
          fontSize="11"
          fontWeight={r.type === "none" ? 600 : 400}
          fill={bad ? c.bad : base === c.fg ? c.sub : base}
        >
          {cut(s.answer?.label, room)}
        </text>,
      );
    }
    return items;
  };

  const whenOf = (u) => {
    const step = u.row.type === "go" || u.row.type === "tool";
    return step ? u.step.at : u.step.answer?.at;
  };
  /* The missing side: the same step at another moment, or not asked. */
  const missingText = (r, mine) => {
    if (!r.partner) return "not asked";
    const t = whenOf(r.partner);
    const o = whenOf(mine);
    const rel =
      t != null && o != null ? (t < o ? "earlier" : "later") : "moved";
    return rel;
  };
  const gutterText = (u) => {
    if (!u) return "not asked";
    const s = u.step;
    const step = u.row.type === "go" || u.row.type === "tool";
    /* The step, not its clock (Chintan, 17 Nov). */
    return step ? `Step ${s.n}` : `reply to ${s.n}`;
  };

  return (
    <Stack ref={ref}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        useFlexGap
        flexWrap="wrap"
        sx={{
          px: 2,
          py: 1,
          borderTop: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Box sx={{ flex: 1, minWidth: 240 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`Comparing trial ${a + 1} `}
            <Box
              component="span"
              sx={{ color: "text.tertiary", fontWeight: 400 }}
            >
              with
            </Box>
            <Box
              component="span"
              sx={{ color: "primary.main" }}
            >{` trial ${b + 1}`}</Box>
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`Trial ${a + 1} in ink, trial ${b + 1} in colour · shaded rows went differently · "earlier/later" = same step at another moment${onlyDiff && sameCount ? ` · ${sameCount} matching rows hidden` : ""}`}
          </Typography>
        </Box>
        {[
          [onlyDiff, () => setOnlyDiff((v) => !v), "Only differences"],
          [false, onStop, "Stop comparing"],
        ].map(([on, fn, label]) => (
          <Box
            key={label}
            component="button"
            type="button"
            aria-pressed={label === "Only differences" ? on : undefined}
            onClick={fn}
            sx={{
              height: 24,
              px: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
              bgcolor: on ? "action.selected" : "background.paper",
              typography: "s3",
              fontWeight: 500,
              color: "text.primary",
              cursor: "pointer",
            }}
          >
            {label}
          </Box>
        ))}
      </Stack>
      <Box
        sx={{
          overflowX: "auto",
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <svg
          width={svgW}
          height={H}
          viewBox={`0 0 ${svgW} ${H}`}
          style={{
            display: "block",
            fontFamily: "Inter, system-ui, sans-serif",
          }}
          role="img"
          aria-label={`Trial ${a + 1} compared with trial ${b + 1}`}
        >
          <defs>
            {mk("cq-fg", c.fg)}
            {mk("cq-muted", c.muted)}
            {mk("cq-acc", c.accent)}
            {mk("cq-bad", c.bad)}
          </defs>
          <text
            x={14}
            y={64}
            fontSize="10.5"
            fontWeight="600"
            letterSpacing="0.04em"
            fill={c.faint}
          >{`TRIAL ${a + 1}`}</text>
          <text
            x={80}
            y={64}
            fontSize="10.5"
            fontWeight="600"
            letterSpacing="0.04em"
            fill={c.accent}
          >{`TRIAL ${b + 1}`}</text>
          {placed.map((r) =>
            r.same ? null : (
              <rect
                key={`bg-${r.top}`}
                x={0}
                y={r.top}
                width={svgW}
                height={r.h}
                fill={c.tint}
              />
            ),
          )}
          {lanes.map((l) => (
            <line
              key={`ln-${l.id}`}
              x1={X[l.id]}
              y1={52}
              x2={X[l.id]}
              y2={H - 4}
              stroke={c.line}
              strokeWidth="1.2"
            />
          ))}
          {lanes.map((l) => (
            <g key={`h-${l.id}`}>
              <title>{`${l.name} · ${l.role}`}</title>
              <rect
                x={X[l.id] - headW / 2}
                y={10}
                width={headW}
                height={40}
                rx={6}
                fill={c.paper}
                stroke={c.line}
              />
              <text
                x={X[l.id]}
                y={27}
                textAnchor="middle"
                fontSize="12"
                fontWeight="600"
                fill={c.fg}
              >
                {cut(l.name, Math.floor((headW - 12) / 6.6))}
              </text>
              <text
                x={X[l.id]}
                y={42}
                textAnchor="middle"
                fontSize="10.5"
                fill={c.muted}
              >
                {cut(l.role, Math.floor((headW - 12) / 5.8))}
              </text>
            </g>
          ))}
          {placed.map((r) => {
            const both = r.a && r.b;
            const yA = both && !r.same ? r.y - 10 : r.y + 4;
            const yB = both && !r.same ? r.y + 12 : r.y + 4;
            return (
              <g
                key={`r-${r.top}`}
                onClick={() => r.a && onPick(r.a.step.n)}
                style={{ cursor: r.a ? "pointer" : "default" }}
              >
                <rect
                  x={0}
                  y={r.top}
                  width={svgW}
                  height={r.h}
                  fill="transparent"
                />
                <text
                  x={14}
                  y={r.y + 4}
                  fontSize="11"
                  fill={
                    r.a
                      ? r.a.step.bad ||
                        (r.a.row.type === "none" && r.a.step.answer?.bad)
                        ? c.bad
                        : c.muted
                      : r.partner
                        ? c.muted
                        : c.warn
                  }
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {r.a ? gutterText(r.a) : missingText(r, r.b)}
                </text>
                <text
                  x={80}
                  y={r.y + 4}
                  fontSize="11"
                  fill={r.b || r.partner ? c.accent : c.warn}
                  style={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {r.b ? gutterText(r.b) : missingText(r, r.a)}
                </text>
                {r.a && unit(r.a, yA, c.fg, "cq-fg", false)}
                {r.b &&
                  (!r.a || !r.same) &&
                  unit(r.b, yB, c.accent, "cq-acc", !!r.a)}
              </g>
            );
          })}
        </svg>
      </Box>
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          px: 2,
          py: 1,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {`Click a row to open that step in trial ${a + 1}. Rows are matched by who asked whom, in order — never by what was said.`}
      </Typography>
    </Stack>
  );
}

CompareSequence.propTypes = {
  seqA: PropTypes.object.isRequired,
  seqB: PropTypes.object.isRequired,
  a: PropTypes.number.isRequired,
  b: PropTypes.number.isRequired,
  onStop: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
};
