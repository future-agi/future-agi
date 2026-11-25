import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import { SEQUENCE_COPY as C } from "./sequence.constants";
import {
  CH,
  ROW_H,
  SLOW_SEC,
  SVG_TYPE as type,
  TAG_W,
  cut,
  kindOf,
  took,
} from "./sequenceLayout";
import HoverCard, { HoverWords } from "./SequenceHoverCard";

const onEnter = (act) => (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    e.stopPropagation();
    act();
  }
};

/* A step's number, on its arrow (or beside its tool loop). */
const bubble = (c, cx, cy, s, text = String(s.n)) => {
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
        fontSize={type.sm}
        fontWeight="600"
        fill={s.bad || s.refused ? c.onColour : c.paper}
      >
        {text}
      </text>
    </g>
  );
};

/* What a loop row says: how many times, and how many went wrong. */
const loopChip = (seq, l) =>
  l.closed
    ? [
        C.loopTimes(l.times),
        l.with?.length && C.loopWith(l.with.map(seq.nameOf).join(", ")),
        l.outcomes?.failed && C.nFailed(l.outcomes.failed),
        l.outcomes?.refused && C.nRefused(l.outcomes.refused),
        l.outcomes?.noAnswer && C.nNoAnswer(l.outcomes.noAnswer),
      ]
        .filter(Boolean)
        .join(" · ")
    : C.loopOpen(l.times);
const loopText = (r) =>
  r.loop && r.closed ? `${r.loop.first}–${r.loop.last}` : undefined;

/* A small kind tag in front of an arrow's words. */
const kindTag = (c, k, color) =>
  k ? (
    <tspan
      fontSize={type.sm}
      fontWeight="600"
      letterSpacing="0.06em"
      fill={color || c.muted}
    >
      {`${k}  `}
    </tspan>
  ) : null;

/* A long call's turn: what the caller said, how many steps, the workers it
   went to; click to open or fold it. */
export function TurnRow({ row: r, seq, byN, svgW, c, open, onToggle }) {
  const g = r.group;
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
        ? C.turnsRange(g.turn, g.turn + merged - 1)
        : C.turn(g.turn);
  const words = `${name} · ${g.label ? `“${cut(g.label, 46)}”` : C.followsUp(seq.nameOf(seq.lanes[1]?.id))} · ${C.stepCount(g.steps.length)}${who.length ? ` · ${who.join(", ")}` : ""}`;
  return (
    <Tooltip
      followCursor
      placement="top"
      enterDelay={250}
      title={
        <Box sx={{ maxWidth: 340 }}>
          <Typography
            sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}
          >
            {`${name} · ${C.stepCount(g.steps.length)}`}
          </Typography>
          {g.text && <HoverWords text={g.text} shown={g.textShown} quoted />}
          <Typography
            sx={{ typography: "s3", color: "inherit", opacity: 0.6, mt: 0.5 }}
          >
            {open ? C.clickToFold : C.clickToOpen}
          </Typography>
        </Box>
      }
    >
      <g
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={C.foldTurn(
          open,
          g.turn == null ? C.noTurn : C.turn(g.turn),
        )}
        onClick={onToggle}
        onKeyDown={onEnter(onToggle)}
        style={{ cursor: "pointer", outline: "none" }}
      >
        <rect
          x={0}
          y={yy - ROW_H.turn / 2 + 2}
          width={svgW}
          height={ROW_H.turn - 4}
          fill={c.code}
        />
        <text x={10} y={yy + 4} fontSize={type.sm} fill={c.sub}>
          {open ? "▾" : "▸"}
        </text>
        <text x={24} y={yy + 4} fontSize={type.sm} fontWeight="600" fill={c.fg}>
          {cut(words, Math.floor((svgW - 140) / 6.2))}
        </text>
        {probs > 0 && (
          <text
            x={svgW - 10}
            y={yy + 4}
            textAnchor="end"
            fontSize={type.sm}
            fontWeight="600"
            fill={c.bad}
          >
            {C.problemCount(probs)}
          </text>
        )}
      </g>
    </Tooltip>
  );
}

TurnRow.propTypes = {
  row: PropTypes.object.isRequired,
  seq: PropTypes.object.isRequired,
  byN: PropTypes.object.isRequired,
  svgW: PropTypes.number.isRequired,
  c: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
};

/* One step's row: its arrow and words, the dashed answer, or a tool loop. */
export function StepRow({
  row: r,
  seq,
  byN,
  layout,
  c,
  selected,
  hover,
  foldTools,
  faded,
  onPick,
  onHover,
  onToggleLoop,
}) {
  const { X, svgW, toolSide, roomOf, placeX } = layout;
  const s = byN[r.n];
  const yy = r.y;
  const on = selected === s.n;
  const wt = on ? 600 : 500;
  // The chip on an open loop folds it back, by mouse or keyboard.
  const foldLoopProps = (id) => ({
    role: "button",
    tabIndex: 0,
    "aria-label": C.foldLoop,
    style: { cursor: "pointer" },
    onClick: (e) => {
      e.stopPropagation();
      onToggleLoop(id);
    },
    onKeyDown: onEnter(() => onToggleLoop(id)),
  });
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
    const badTools = foldTools
      ? seq.steps.filter((x) => x.kind === "tool" && x.parent === s.n && x.bad)
          .length
      : 0;
    const chip =
      [
        r.loop && loopChip(seq, { ...r.loop, closed: r.closed }),
        s.turns > 1 && C.turnsTimes(s.turns),
        foldTools &&
          s.tools > 0 &&
          [C.plusToolCalls(s.tools), badTools && C.nFailed(badTools)]
            .filter(Boolean)
            .join(" · "),
        slow && C.slowChip(took(s.dur)),
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
        fontSize={type.md}
        fontWeight={wt}
        fill={c.fg}
      >
        {kindTag(c, gk, s.bad ? c.bad : null)}
        {label}
      </text>,
      bubble(
        c,
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
            fontSize={type.sm}
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
    const tag = ` · ${isTook ? C.afterStep(s.n) : C.replyToStep(s.n)}`;
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
        fontSize={type.sm}
        fontWeight={r.type === "none" || on ? 600 : 400}
        fill={bad ? c.bad : c.sub}
      >
        {kindTag(c, rk, bad ? c.bad : null)}
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
    const slow = s.dur != null && s.dur >= SLOW_SEC;
    const tail = slow ? ` · ${C.slowChip(took(s.dur))}` : "";
    const lchip = r.loop
      ? loopChip(seq, { ...r.loop, closed: r.closed })
      : null;
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
        c,
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
        fontSize={type.sm}
        fontWeight={wt}
        fill={c.fg}
      >
        {kindTag(c, "TOOL", tone)}
        <tspan fontFamily={type.mono}>{name}</tspan>
        <tspan
          dx="8"
          fontSize={type.sm}
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
            fontSize={type.sm}
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
  const act = () =>
    r.loop && r.closed ? onToggleLoop(r.loop.id) : onPick(s.n);
  const isStep = r.type === "go" || r.type === "tool";
  return (
    <Tooltip
      title={<HoverCard seq={seq} row={r} step={s} />}
      followCursor
      placement="top"
      enterDelay={250}
      enterNextDelay={150}
    >
      <g
        role="button"
        tabIndex={0}
        {...(isStep ? { "data-row": s.n } : {})}
        aria-label={
          isStep
            ? C.stepLabel(s.n, s.label)
            : C.replyLabel(s.n, s.answer?.label ?? "")
        }
        onClick={act}
        onKeyDown={onEnter(act)}
        onMouseEnter={() => onHover(s.n)}
        onMouseLeave={() => onHover(null)}
        style={{
          cursor: "pointer",
          outline: "none",
          opacity: faded ? 0.22 : 1,
          transition: "opacity 120ms",
        }}
      >
        <rect
          x={0}
          y={r.y - ROW_H[r.type] / 2 - 2}
          width={svgW}
          height={ROW_H[r.type]}
          fill={on ? c.sel : hover === r.n ? c.hover : "transparent"}
        />
        {items}
      </g>
    </Tooltip>
  );
}

StepRow.propTypes = {
  row: PropTypes.object.isRequired,
  seq: PropTypes.object.isRequired,
  byN: PropTypes.object.isRequired,
  // From layoutOf: lane x positions, the drawing's width and label room.
  layout: PropTypes.object.isRequired,
  c: PropTypes.object.isRequired,
  selected: PropTypes.number,
  hover: PropTypes.number,
  foldTools: PropTypes.bool.isRequired,
  // Not part of the followed participant's steps.
  faded: PropTypes.bool.isRequired,
  onPick: PropTypes.func.isRequired,
  onHover: PropTypes.func.isRequired,
  onToggleLoop: PropTypes.func.isRequired,
};
