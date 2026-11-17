import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { SEQUENCE_COPY as C } from "./sequence.constants";
import {
  CH,
  GUT,
  LANE_PAD,
  LONG_STEPS,
  MIN_GAP,
  ROW_H,
  SLOW_SEC,
  TAG_W,
  cut,
  kindOf,
  nameLines,
  took,
  useWidth,
} from "./sequenceLayout";
import { Legend, Toggle } from "./SequenceParts";
import HoverCard from "./SequenceHoverCard";
import StepDetail from "./StepDetail";

/*
  A call's participants as a sequence: one lane per participant, time going
  down. A solid arrow is a step with its exact words; a dashed arrow is what
  came back, tagged with the step it answers; a red dashed arrow is an answer
  that never came. Tool calls are steps on the lane of the agent that ran
  them. Picking a step shows the full message and answer under the diagram.
*/
export default function SequenceDiagram({
  seq,
  selected,
  onSelect,
  onOpenTrace,
  onShowInTranscript,
  linkOf,
}) {
  const theme = useTheme();
  const wrapRef = useRef(null);
  const width = useWidth(wrapRef);
  const [hover, setHover] = useState(null);
  const [lane, setLane] = useState(null); // followed participant
  /* Following a lane ends with a click anywhere else on the screen (or
     Esc) — not only Show all or its name again. */
  useEffect(() => {
    if (!lane) return undefined;
    const off = (e) => {
      if (e.target?.closest?.("[data-lane-head]")) return;
      setLane(null);
    };
    const esc = (e) => {
      if (e.key === "Escape") setLane(null);
    };
    document.addEventListener("click", off);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("click", off);
      document.removeEventListener("keydown", esc);
    };
  }, [lane]);
  const [foldTools, setFoldTools] = useState(false);
  const [problemsOnly, setProblemsOnly] = useState(false);
  /* Loops open on click; a long call's turns fold (open: those with a
     problem, or the picked step). */
  const [openLoops, setOpenLoops] = useState(() => new Set());
  const [turnOpen, setTurnOpen] = useState({});
  const c = {
    fg: theme.palette.text.primary,
    sub: theme.palette.text.secondary,
    muted: theme.palette.text.tertiary || theme.palette.text.secondary,
    faint: theme.palette.text.disabled,
    line: theme.palette.divider,
    code: theme.palette.background.neutral,
    paper: theme.palette.background.paper,
    bad: theme.palette.error.main,
    warn: theme.palette.warning.main,
    sel: alpha(theme.palette.primary.main, 0.08),
    selLine: theme.palette.primary.main,
    hover: theme.palette.action.hover,
  };
  const byN = Object.fromEntries(seq.steps.map((s) => [s.n, s]));
  const hasTools = seq.steps.some((s) => s.kind === "tool");
  const chain = new Set(seq.chain);
  const hasProblems = chain.size > 0;

  const loops = seq.loops || [];
  const loopById = Object.fromEntries(loops.map((l) => [l.id, l]));
  /* Open when asked, when the picked step is inside, or when a failed
     problem sits inside it. */
  const loopOpen = (l) =>
    openLoops.has(l.id) ||
    (selected != null && selected !== l.first && l.steps.includes(selected)) ||
    /* Problems only never hides a problem inside a loop. */
    (problemsOnly &&
      hasProblems &&
      l.steps.some((n) => n !== l.first && chain.has(n)));
  const toggleLoop = (id) =>
    setOpenLoops((was) => {
      const next = new Set(was);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const turnGroups = seq.turnGroups || [];
  const long = seq.steps.length > LONG_STEPS && turnGroups.length > 1;
  const turnIsOpen = (g) =>
    !long ||
    (turnOpen[g.turn] ??
      (g.bad || (selected != null && g.steps.includes(selected))));
  /* Picking a step (from a problem line, a link, ↑ ↓) opens its turn even
     if it was folded; folding it again afterwards still works. */
  const selTurn = selected != null ? byN[selected]?.turn : null;
  useEffect(() => {
    if (selTurn == null) return;
    setTurnOpen((was) =>
      was[selTurn] === false ? { ...was, [selTurn]: true } : was,
    );
  }, [selected, selTurn]);
  const foldedTurn = new Set(
    turnGroups.filter((g) => !turnIsOpen(g)).map((g) => g.turn),
  );
  /* A closed loop shows only its first step, standing for all of them. */
  const inClosedLoop = (s) =>
    s.loop && !loopOpen(loopById[s.loop]) && s.n !== loopById[s.loop].first;

  /* What's on show: tool calls folded into their hand-off, or only the
     problems and the steps that led to them. Numbers never change. */
  const shown = seq.steps.filter(
    (s) =>
      // A tool call the record ties to no hand-off has nowhere to fold into.
      !(foldTools && s.kind === "tool" && s.parent != null) &&
      !(problemsOnly && hasProblems && !chain.has(s.n)) &&
      !inClosedLoop(s) &&
      !foldedTurn.has(s.turn),
  );
  const shownSet = new Set(shown.map((s) => s.n));
  const involves = (s) => !lane || s.from === lane || s.to === lane;

  const L = seq.lanes.length;
  const laneW = Math.max(240, width - GUT);
  const gap = Math.max(
    MIN_GAP,
    Math.min(230, (laneW - 2 * LANE_PAD) / Math.max(1, L - 1)),
  );
  const X = Object.fromEntries(
    seq.lanes.map((ln, i) => [ln.id, LANE_PAD + i * gap]),
  );
  /* Never wider than the first lane's padding, or it clips at the edge. */
  const headW = Math.min(150, gap - 8, 2 * LANE_PAD - 6);
  const nameChars = Math.floor((headW - 12) / 6.6);
  const heads = seq.lanes.map((ln) => nameLines(ln.name, nameChars));
  const headH = heads.some((h) => h.length > 1) ? 54 : 40;
  const top = 10 + headH + 42; // first row's centre line
  const svgW = Math.max(laneW, 2 * LANE_PAD + gap * (L - 1));
  /* A tool call is a row of its own, so its one line may run across the
     lanes beside it (on a paper backing); left of the lane when the lane is
     near the right edge. */
  const toolSide = (from) => {
    const x = X[from];
    const right = svgW - x - 30;
    const left = x - 30;
    return right >= Math.min(260, left)
      ? { left: false, room: right }
      : { left: true, room: left };
  };

  /* Rows on show: a closed loop is its first step's row with no replies;
     a long call puts each turn's header above its rows. */
  const rowOn = (r) => {
    if (!shownSet.has(r.n)) return false;
    const st = byN[r.n];
    const l = st.loop ? loopById[st.loop] : null;
    return !(l && !loopOpen(l) && r.type !== "go" && r.type !== "tool");
  };
  const asRow = (r) => {
    const st = byN[r.n];
    const l = st.loop ? loopById[st.loop] : null;
    return l && st.n === l.first ? { ...r, loop: l, closed: !loopOpen(l) } : r;
  };
  const ordered = long
    ? turnGroups.flatMap((g) => [
        { type: "turn", n: null, group: g },
        ...seq.rows
          .filter((r) => byN[r.n].turn === g.turn && rowOn(r))
          .map(asRow),
      ])
    : seq.rows.filter(rowOn).map(asRow);
  let y = top;
  const rows = ordered.map((r) => {
    const out = { ...r, y };
    y += ROW_H[r.type];
    return out;
  });
  const H = y + 6;

  const pick = (n) => onSelect(selected === n ? null : n);
  const onEnter = (act) => (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      e.stopPropagation();
      act();
    }
  };
  // The chip on an open loop folds it back, by mouse or keyboard.
  const foldLoopProps = (id) => ({
    role: "button",
    tabIndex: 0,
    "aria-label": C.foldLoop,
    style: { cursor: "pointer" },
    onClick: (e) => {
      e.stopPropagation();
      toggleLoop(id);
    },
    onKeyDown: onEnter(() => toggleLoop(id)),
  });
  /* ↑ ↓ (or k j) move between the steps on show; Esc clears. */
  const onKeyDown = (e) => {
    const tag = e.target?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || e.target?.isContentEditable)
      return;
    const order = shown.map((s) => s.n);
    if (!order.length) return;
    const i = order.indexOf(selected);
    let next = null;
    if (e.key === "ArrowDown" || e.key === "j")
      next = order[i < 0 ? 0 : Math.min(order.length - 1, i + 1)];
    else if (e.key === "ArrowUp" || e.key === "k")
      next = order[i < 0 ? 0 : Math.max(0, i - 1)];
    else if (e.key === "Escape" && (lane || selected != null)) {
      /* Stops following first, then clears the step; one more Esc closes
         the drawer as usual. */
      e.preventDefault();
      e.stopPropagation();
      if (lane) setLane(null);
      else onSelect(null);
      return;
    } else return;
    /* Inside the diagram the arrows move between steps, not between calls
       (the drawer's own ↑ ↓ still work anywhere else). */
    e.preventDefault();
    e.stopPropagation();
    onSelect(next);
    requestAnimationFrame(() => {
      const el = wrapRef.current?.querySelector(`[data-row="${next}"]`);
      el?.focus?.({ preventScroll: true });
      el?.scrollIntoView?.({ block: "nearest" });
    });
  };

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

  /* A label may run past a short arrow; it stays inside the drawing. */
  const roomOf = (x1, x2) =>
    Math.floor(Math.max(Math.abs(x2 - x1) - 16, 210) / CH);
  const placeX = (mid, text) => {
    const half = (text.length * CH) / 2 + 4;
    return Math.min(svgW - half, Math.max(half, mid));
  };

  const bandOf = (r, w, on) => (
    <rect
      x={0}
      y={r.y - ROW_H[r.type] / 2 - 2}
      width={w}
      height={ROW_H[r.type]}
      fill={on ? c.sel : hover === r.n ? c.hover : "transparent"}
    />
  );
  const rowProps = (s, r) => ({
    role: "button",
    tabIndex: 0,
    ...(r.type === "go" || r.type === "tool" ? { "data-row": s.n } : {}),
    "aria-label":
      r.type === "go" || r.type === "tool"
        ? `Step ${s.n}: ${s.label}`
        : `Reply to step ${s.n}: ${s.answer?.label ?? ""}`,
    onClick: () => (r.loop && r.closed ? toggleLoop(r.loop.id) : pick(s.n)),
    onKeyDown: onEnter(() =>
      r.loop && r.closed ? toggleLoop(r.loop.id) : pick(s.n),
    ),
    onMouseEnter: () => setHover(s.n),
    onMouseLeave: () => setHover(null),
    style: {
      cursor: "pointer",
      outline: "none",
      opacity: involves(s) ? 1 : 0.22,
      transition: "opacity 120ms",
    },
  });

  /* A step's number, on its arrow (or beside its tool loop). */
  const bubble = (cx, cy, s, text = String(s.n)) => {
    const w = Math.max(18, text.length * 6.6 + 8);
    return (
      <g key="bubble">
        <rect
          x={cx - w / 2}
          y={cy - 9}
          width={w}
          height={18}
          rx={9}
          fill={s.bad ? c.bad : s.refused ? c.warn : c.fg}
          stroke={c.paper}
          strokeWidth="2"
        />
        <text
          x={cx}
          y={cy + 4}
          textAnchor="middle"
          fontSize="11"
          fontWeight="600"
          fill={s.bad || s.refused ? "#fff" : c.paper}
        >
          {text}
        </text>
      </g>
    );
  };
  /* What a loop row says: how many times, and how many went wrong. */
  const loopChip = (l) =>
    l.closed
      ? `↻ ${l.times} times${l.with?.length ? ` · with ${l.with.map(seq.nameOf).join(", ")}` : ""}${[
          l.outcomes?.failed && `${l.outcomes.failed} failed`,
          l.outcomes?.refused && `${l.outcomes.refused} refused`,
          l.outcomes?.noAnswer && `${l.outcomes.noAnswer} no answer`,
        ]
          .filter(Boolean)
          .map((x) => ` · ${x}`)
          .join("")}`
      : `↻ 1 of ${l.times} · fold`;
  const loopText = (r) =>
    r.loop && r.closed ? `${r.loop.first}–${r.loop.last}` : undefined;

  /* The lanes part of a row: the arrow and its words, or the tool box. */
  /* A small kind tag in front of an arrow's words. */
  const kindTag = (k, color) =>
    k ? (
      <tspan
        fontSize="9"
        fontWeight="700"
        letterSpacing="0.06em"
        fill={color || c.muted}
      >
        {`${k}  `}
      </tspan>
    ) : null;

  /* A long call's turn: what the caller said, how many steps, the workers it
     went to; click to open or fold it. */
  const turnRow = (r) => {
    const g = r.group;
    const open = !foldedTurn.has(g.turn);
    const yy = r.y;
    const who = [
      ...new Set(
        g.steps
          .map((n) => byN[n])
          .flatMap((x) => [x.from, x.to])
          .filter(
            (id) => id && id !== seq.lanes[0].id && id !== seq.lanes[1]?.id,
          ),
      ),
    ].map(seq.nameOf);
    const probs = (seq.problems || []).filter((pr) =>
      g.steps.includes(pr.n),
    ).length;
    /* The record's own turn numbers; back-to-back turns answered alone are
       one step, so its header names them all ("Turns 4–8"). */
    const merged = Math.max(1, ...g.steps.map((n) => byN[n].turns || 1));
    const name =
      g.turn == null
        ? C.noTurn
        : merged > 1
          ? `Turns ${g.turn}–${g.turn + merged - 1}`
          : `Turn ${g.turn}`;
    const words = `${name} · ${g.label ? `“${cut(g.text || g.label, 46)}”` : `${seq.nameOf(seq.lanes[1]?.id)} follows up`} · ${g.steps.length} ${g.steps.length === 1 ? "step" : "steps"}${who.length ? ` · ${who.join(", ")}` : ""}`;
    const toggle = () => setTurnOpen((was) => ({ ...was, [g.turn]: !open }));
    return (
      <Tooltip
        key={`turn-${g.turn}`}
        followCursor
        placement="top"
        enterDelay={250}
        title={
          <Box sx={{ maxWidth: 340 }}>
            <Typography
              sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}
            >
              {`${name} · ${g.steps.length} ${g.steps.length === 1 ? "step" : "steps"}`}
            </Typography>
            {g.text && (
              <Typography sx={{ typography: "s3", color: "inherit", mt: 0.5 }}>
                {`“${g.text}”`}
              </Typography>
            )}
            <Typography
              sx={{ typography: "s3", color: "inherit", opacity: 0.6, mt: 0.5 }}
            >
              {open ? "Click to fold" : "Click to open"}
            </Typography>
          </Box>
        }
      >
        <g
          role="button"
          tabIndex={0}
          aria-expanded={open}
          aria-label={`${open ? "Fold" : "Open"} ${g.turn == null ? C.noTurn.toLowerCase() : `turn ${g.turn}`}`}
          onClick={toggle}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              toggle();
            }
          }}
          style={{ cursor: "pointer", outline: "none" }}
        >
          <rect
            x={0}
            y={yy - ROW_H.turn / 2 + 2}
            width={svgW}
            height={ROW_H.turn - 4}
            fill={c.code}
          />
          <text x={10} y={yy + 4} fontSize="11.5" fill={c.sub}>
            {open ? "▾" : "▸"}
          </text>
          <text x={24} y={yy + 4} fontSize="11.5" fontWeight="600" fill={c.fg}>
            {cut(words, Math.floor((svgW - 140) / 6.2))}
          </text>
          {probs > 0 && (
            <text
              x={svgW - 10}
              y={yy + 4}
              textAnchor="end"
              fontSize="11"
              fontWeight="600"
              fill={c.bad}
            >
              {`${probs} ${probs === 1 ? "problem" : "problems"}`}
            </text>
          )}
        </g>
      </Tooltip>
    );
  };

  const laneRow = (r) => {
    if (r.type === "turn") return turnRow(r);
    const s = byN[r.n];
    const yy = r.y;
    const on = selected === s.n;
    const wt = on ? 600 : 500;
    const items = [];
    if (r.type === "go") {
      const x1 = X[s.from];
      const x2 = X[s.to];
      const dir = x2 > x1 ? 1 : -1;
      /* How long it took, only when it was slow — never for a conversation
         with the caller, whose length is the caller's. */
      const slow =
        s.dur != null &&
        s.dur >= SLOW_SEC &&
        !(s.turns > 1) &&
        s.from !== seq.lanes[0].id &&
        s.answer?.kind !== "none";
      const chip =
        [
          r.loop && loopChip({ ...r.loop, closed: r.closed }),
          s.turns > 1 && `×${s.turns} turns`,
          foldTools &&
            s.tools > 0 &&
            `+${s.tools} tool ${s.tools === 1 ? "call" : "calls"}${(() => {
              const bad = seq.steps.filter(
                (x) => x.kind === "tool" && x.parent === s.n && x.bad,
              ).length;
              return bad ? ` · ${bad} failed` : "";
            })()}`,
          slow && `slow · ${took(s.dur)}`,
        ]
          .filter(Boolean)
          .join(" · ") || null;
      const chipW = chip ? chip.length * 5.9 + 12 : 0;
      const gk = kindOf(r, s);
      /* The tag takes a few characters, so a step's words may run a little
         past a short arrow (still inside the drawing). */
      const room = Math.floor(Math.max(Math.abs(x2 - x1) - 16, 260) / CH);
      const raw = cut(s.label, room - Math.ceil((chipW + TAG_W(gk)) / CH) - 2);
      const label = s.kind === "message" ? `“${raw}”` : raw;
      const lw = label.length * CH + TAG_W(gk);
      const mid =
        placeX(
          (x1 + x2) / 2,
          `${label}${" ".repeat(Math.ceil((chipW + TAG_W(gk)) / CH))}`,
        ) -
        chipW / 2;
      items.push(
        <line
          key="a"
          x1={x1}
          y1={yy + 6}
          x2={x2 - dir * 3}
          y2={yy + 6}
          stroke={s.bad ? c.bad : c.fg}
          strokeWidth="1.5"
          markerEnd={`url(#sq-${s.bad ? "bad" : "fg"})`}
        />,
        <text
          key="l"
          x={mid}
          y={yy - 3}
          textAnchor="middle"
          fontSize="12"
          fontWeight={wt}
          fill={c.fg}
        >
          {kindTag(gk, s.bad ? c.bad : null)}
          {label}
        </text>,
        bubble(
          x1 + dir * ((loopText(r) || "").length * 3.3 + 16),
          yy + 6,
          s,
          loopText(r),
        ),
      );
      if (chip)
        items.push(
          <g key="chip" {...(r.loop && !r.closed && foldLoopProps(r.loop.id))}>
            <rect
              x={mid + lw / 2 + 6}
              y={yy - 15}
              width={chipW}
              height={16}
              rx={4}
              fill={c.code}
            />
            <text
              x={mid + lw / 2 + 6 + chipW / 2}
              y={yy - 3}
              textAnchor="middle"
              fontSize="10.5"
              fontWeight="500"
              fill={c.sub}
            >
              {chip}
            </text>
          </g>,
        );
    } else if (r.type === "back" || r.type === "none" || r.type === "took") {
      const isTook = r.type === "took";
      const x1 = X[s.to];
      const x2 = isTook ? X[seq.lanes[0].id] : X[s.from];
      const dir = x2 > x1 ? 1 : -1;
      const bad = !!s.answer.bad;
      const tag = isTook ? ` · after step ${s.n}` : ` · reply to step ${s.n}`;
      /* A reply has its row to itself, so its words may run past a short
         arrow — the "reply to step n" tag never eats them. */
      const rk = kindOf(r, s);
      const rawR = cut(
        s.answer.label,
        Math.max(
          roomOf(x1, x2) - tag.length,
          Math.floor((svgW - 24) / CH) - tag.length - 2,
          18,
        ) - Math.ceil(TAG_W(rk) / CH),
      );
      /* Quotes only for someone's actual words — "Front desk answered each
         one" is our summary of several turns, not a quote. */
      const label = s.kind === "message" && !(s.turns > 1) ? `“${rawR}”` : rawR;
      items.push(
        <line
          key="a"
          x1={x1}
          y1={yy + 6}
          x2={x2 - dir * 3}
          y2={yy + 6}
          stroke={bad ? c.bad : c.muted}
          strokeWidth="1.4"
          strokeDasharray={r.type === "none" ? "5 3" : isTook ? "2 3" : "4 3"}
          markerEnd={`url(#sq-${bad ? "bad" : "muted"})`}
        />,
        <text
          key="l"
          x={placeX(
            (x1 + x2) / 2,
            `${label}${tag}${" ".repeat(Math.ceil(TAG_W(rk) / CH))}`,
          )}
          y={yy - 3}
          textAnchor="middle"
          fontSize="11.5"
          fontWeight={r.type === "none" || on ? 600 : 400}
          fill={bad ? c.bad : c.sub}
        >
          {kindTag(rk, bad ? c.bad : null)}
          {label}
          <tspan fontWeight="400" fill={bad ? c.bad : c.muted} opacity={0.8}>
            {tag}
          </tspan>
        </text>,
      );
    } else if (r.type === "tool") {
      /* A tool call: a small loop on the lane that ran it, then one line —
         the tool's name and what came back. */
      const x = X[s.from];
      const side = toolSide(s.from);
      const mono = "ui-monospace, SFMono-Regular, Menlo, monospace";
      const slow = s.dur != null && s.dur >= SLOW_SEC;
      const tail = slow ? ` · slow · ${took(s.dur)}` : "";
      const lchip = r.loop ? loopChip({ ...r.loop, closed: r.closed }) : null;
      const lchipW = lchip ? lchip.length * 5.9 + 12 : 0;
      const nameW = 6.6;
      const tagW = TAG_W("TOOL");
      const name = cut(s.tool, Math.floor((side.room * 0.55 - tagW) / nameW));
      const resRoom = Math.floor(
        (side.room - tagW - name.length * nameW - 18 - lchipW) / 6.1,
      );
      const res = cut(`→ ${s.result || ""}${tail}`, resRoom);
      /* Red for a broken tool; amber for a call the world refused. */
      const tone = s.bad ? c.bad : s.refused ? c.warn : null;
      const textW = tagW + name.length * nameW + 8 + res.length * 6.1;
      const tx = side.left ? x - 22 - textW : x + 22;
      const loop = side.left
        ? `M${x},${yy - 5} h-12 a5,5 0 0 0 0,10 h9`
        : `M${x},${yy - 5} h12 a5,5 0 0 1 0,10 h-9`;
      items.push(
        <rect
          key="bg"
          x={tx - 4}
          y={yy - 9}
          width={textW + 8}
          height={18}
          rx={4}
          fill={on ? c.sel : c.paper}
        />,
        <path
          key="loop"
          d={loop}
          fill="none"
          stroke={tone || c.muted}
          strokeWidth="1.3"
        />,
        <path
          key="tip"
          d={
            side.left
              ? `M${x - 3},${yy + 5} l-4,-3 v6 z`
              : `M${x + 3},${yy + 5} l4,-3 v6 z`
          }
          fill={tone || c.muted}
        />,
        bubble(
          side.left
            ? x + 16 + (loopText(r) || "").length * 3.3
            : x - 16 - (loopText(r) || "").length * 3.3,
          yy,
          s,
          loopText(r),
        ),
        /* One line, anchored at the loop: left of it the line ends there, so
           wider-than-estimated words never run into the arrow. */
        <text
          key="n"
          x={side.left ? x - 22 : x + 22}
          y={yy + 4}
          textAnchor={side.left ? "end" : "start"}
          fontSize="11"
          fontWeight={wt}
          fill={c.fg}
        >
          {kindTag("TOOL", tone)}
          <tspan fontFamily={mono}>{name}</tspan>
          <tspan
            dx="8"
            fontSize="11.5"
            fontWeight={tone ? 600 : 400}
            fill={tone || c.sub}
          >
            {res}
          </tspan>
        </text>,
      );
      if (lchip)
        items.push(
          <g key="lchip" {...(!r.closed && foldLoopProps(r.loop.id))}>
            <rect
              x={tx + textW + 8}
              y={yy - 8}
              width={lchipW}
              height={16}
              rx={4}
              fill={c.code}
            />
            <text
              x={tx + textW + 8 + lchipW / 2}
              y={yy + 4}
              textAnchor="middle"
              fontSize="10.5"
              fontWeight="500"
              fill={
                r.closed && r.loop.badCount
                  ? c.bad
                  : r.closed && r.loop.outcomes?.refused
                    ? c.warn
                    : c.sub
              }
            >
              {lchip}
            </text>
          </g>,
        );
    }
    return (
      <Tooltip
        key={`${r.type}-${r.n}`}
        title={<HoverCard seq={seq} row={r} step={s} />}
        followCursor
        placement="top"
        enterDelay={250}
        enterNextDelay={150}
      >
        <g {...rowProps(s, r)}>
          {bandOf(r, svgW, on)}
          {items}
        </g>
      </Tooltip>
    );
  };

  const sel = selected != null ? byN[selected] : null;
  const svgStyle = {
    display: "block",
    fontFamily: "Inter, system-ui, sans-serif",
  };
  const followed = lane ? seq.lanes.find((l) => l.id === lane) : null;
  const followedCount = followed ? shown.filter(involves).length : 0;

  return (
    <Stack>
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="flex-end"
        useFlexGap
        flexWrap="wrap"
        sx={{ pb: 0.75 }}
      >
        <Box sx={{ flex: 1, minWidth: 240 }}>
          <Legend seq={seq} />
        </Box>
        <Stack direction="row" spacing={0.75}>
          {long && (
            <Toggle
              on={turnGroups.every((g) => !foldedTurn.has(g.turn))}
              onClick={() => {
                const allOpen = turnGroups.every(
                  (g) => !foldedTurn.has(g.turn),
                );
                setTurnOpen(
                  Object.fromEntries(turnGroups.map((g) => [g.turn, !allOpen])),
                );
              }}
            >
              All turns
            </Toggle>
          )}
          {hasTools && (
            <Toggle on={foldTools} onClick={() => setFoldTools((v) => !v)}>
              Fold tool calls
            </Toggle>
          )}
          {hasProblems && (
            <Toggle
              on={problemsOnly}
              onClick={() => setProblemsOnly((v) => !v)}
            >
              Problems only
            </Toggle>
          )}
        </Stack>
      </Stack>
      {(followed || (problemsOnly && hasProblems)) && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{
            px: 2,
            py: 0.5,
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
          >
            {[
              problemsOnly &&
                hasProblems &&
                `Problems and the steps that led to them · ${shown.length} of ${seq.steps.length} steps`,
              followed &&
                `Following ${followed.name} · ${followedCount} of ${shown.length} steps`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
          <Typography
            component="button"
            type="button"
            onClick={() => {
              setLane(null);
              setProblemsOnly(false);
            }}
            sx={{
              typography: "s3",
              fontWeight: 500,
              color: "text.primary",
              p: 0,
              border: 0,
              bgcolor: "transparent",
              cursor: "pointer",
              textDecoration: "underline",
              textUnderlineOffset: "2px",
            }}
          >
            Show all
          </Typography>
        </Stack>
      )}
      <Box
        ref={wrapRef}
        onKeyDown={onKeyDown}
        sx={{
          display: "flex",
          borderTop: "1px solid",
          borderColor: "divider",
          "& [role=button]:focus-visible rect:first-of-type": {
            stroke: c.selLine,
            strokeWidth: 1.5,
          },
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0, overflowX: "auto" }}>
          <svg
            width={svgW}
            height={H}
            viewBox={`0 0 ${svgW} ${H}`}
            role="group"
            aria-label={`Sequence of ${shown.length} steps`}
            style={svgStyle}
          >
            <defs>
              {mk("sq-fg", c.fg)}
              {mk("sq-muted", c.muted)}
              {mk("sq-bad", c.bad)}
            </defs>
            {seq.lanes.map((ln) => (
              <line
                key={`l-${ln.id}`}
                x1={X[ln.id]}
                y1={10 + headH}
                x2={X[ln.id]}
                y2={H - 4}
                stroke={lane === ln.id ? c.selLine : c.line}
                strokeWidth={lane === ln.id ? 1.5 : 1.2}
              />
            ))}
            {rows.map(laneRow)}
            {seq.lanes.map((ln, i) => {
              const on = lane === ln.id;
              return (
                <g
                  key={`h-${ln.id}`}
                  role="button"
                  tabIndex={0}
                  aria-pressed={on}
                  data-lane-head="true"
                  aria-label={
                    on ? `Stop following ${ln.name}` : `Follow ${ln.name}`
                  }
                  onClick={() => setLane(on ? null : ln.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setLane(on ? null : ln.id);
                    }
                  }}
                  style={{ cursor: "pointer", outline: "none" }}
                >
                  <title>
                    {on
                      ? `Following ${ln.name} · click to show everyone`
                      : `${ln.name} · ${ln.role} · click to follow`}
                  </title>
                  <rect
                    x={X[ln.id] - headW / 2}
                    y={10}
                    width={headW}
                    height={headH}
                    rx={6}
                    fill={on ? c.sel : c.paper}
                    stroke={on ? c.selLine : c.line}
                    strokeWidth={on ? 1.5 : 1}
                  />
                  {heads[i].map((line, j) => (
                    <text
                      key={line}
                      x={X[ln.id]}
                      y={27 + j * 14}
                      textAnchor="middle"
                      fontSize="12"
                      fontWeight="600"
                      fill={c.fg}
                    >
                      {line}
                    </text>
                  ))}
                  <text
                    x={X[ln.id]}
                    y={10 + headH - 8}
                    textAnchor="middle"
                    fontSize="10.5"
                    fill={c.muted}
                  >
                    {cut(ln.role, Math.floor((headW - 12) / 5.8))}
                  </text>
                </g>
              );
            })}
          </svg>
        </Box>
      </Box>
      {seq.notObserved.length > 0 && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            px: 2,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {C.notObserved(seq.notObserved.join(", "))}
        </Typography>
      )}
      <Box
        sx={{ px: 2, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}
      >
        {sel ? (
          <StepDetail
            seq={seq}
            step={sel}
            onOpenTrace={onOpenTrace}
            onShowInTranscript={onShowInTranscript}
            linkOf={linkOf}
          />
        ) : (
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            {C.pickHint}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}

SequenceDiagram.propTypes = {
  seq: PropTypes.object.isRequired,
  selected: PropTypes.number,
  onSelect: PropTypes.func.isRequired,
  onOpenTrace: PropTypes.func,
  onShowInTranscript: PropTypes.func,
  linkOf: PropTypes.func,
};
