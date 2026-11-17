import { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";

/*
  Participants as a sequence (design A, 16 Nov): one lane per participant,
  time going down. A solid arrow is a step with what it asked; a dashed arrow
  is what came back, tagged with the step it answers (reply to step 2); a red dashed
  arrow is an answer that never came. Tool calls are numbered steps drawn as
  a box on the lane of the agent that ran them. Picking a step shows the
  exact message and answer under the diagram.

  From the competitor review (16 Nov):
    · (17 Nov, Chintan: what happened, not when) no clock or took column —
      a step shows how long it took only when it was slow; tool calls read
      as one line on the lane that ran them
    · a step opens the transcript and recording at its moment (Opik / Dev UI
      linked selection; "play from here" is ours, these are voice calls)
    · click a lane to follow one participant (Opik, the old tree filter)
    · a link to a step (Datadog)
    · model, tokens and time in the step detail (LangSmith, Opik)
    · fold tool calls / problems only (Langfuse's two views)
    · ↑ ↓ (or j k) move between steps, Esc clears (Datadog, Langfuse)
*/

const GUT = 56; // step pill only — no clock, no took column (Chintan, 17 Nov)
const LANE_PAD = 64; // half a lane header, plus air
const MIN_GAP = 118; // lanes closer than this scroll sideways
const ROW_H = { go: 44, tool: 34, back: 36, none: 38, took: 38 };
const CH = 6.4; // average character width at 12px
/* A step says how long it took only when that's part of what happened. */
const SLOW_SEC = 20;

const cut = (text, chars) => {
  const s = String(text || "");
  if (chars < 4) return "";
  return s.length <= chars ? s : `${s.slice(0, chars - 1).trimEnd()}…`;
};
const KIND = { message: "Message", handoff: "Hand-off", tool: "Tool call" };

/** 0.4 s · 3.2 s · 42 s · 1 min 13 s */
const took = (sec) => {
  if (sec == null || Number.isNaN(sec)) return "—";
  if (sec < 10) return `${sec.toFixed(1)} s`;
  if (sec < 60) return `${Math.round(sec)} s`;
  const m = Math.floor(sec / 60);
  const r = Math.round(sec % 60);
  return r ? `${m} min ${r} s` : `${m} min`;
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

function Pill({ n, bad }) {
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: 20,
        height: 18,
        px: 0.625,
        borderRadius: "9px",
        typography: "s3",
        fontWeight: 600,
        fontVariantNumeric: "tabular-nums",
        flexShrink: 0,
        bgcolor: bad ? "error.main" : "text.primary",
        color: bad ? "common.white" : "background.paper",
      }}
    >
      {n}
    </Box>
  );
}
Pill.propTypes = { n: PropTypes.number, bad: PropTypes.bool };

/* A small on/off control for the view (not an action). */
function Toggle({ on, onClick, children, disabled }) {
  return (
    <Box
      component="button"
      type="button"
      aria-pressed={on}
      disabled={disabled}
      onClick={onClick}
      sx={{
        height: 24,
        px: 1,
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        bgcolor: on ? "action.selected" : "background.paper",
        color: disabled ? "text.disabled" : "text.primary",
        typography: "s3",
        fontWeight: 500,
        cursor: disabled ? "default" : "pointer",
        whiteSpace: "nowrap",
        "&:hover": disabled
          ? {}
          : { bgcolor: on ? "action.selected" : "action.hover" },
      }}
    >
      {on && <Iconify icon="solar:check-read-linear" width={14} />}
      {children}
    </Box>
  );
}
Toggle.propTypes = {
  on: PropTypes.bool,
  onClick: PropTypes.func,
  children: PropTypes.node,
  disabled: PropTypes.bool,
};

function Legend({ seq }) {
  const theme = useTheme();
  const fg = theme.palette.text.primary;
  const sub = theme.palette.text.secondary;
  const bad = theme.palette.error.main;
  const has = (t) => seq.rows.some((r) => r.type === t);
  const item = (key, line, text) => (
    <Stack key={key} direction="row" spacing={0.75} alignItems="center">
      {line}
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {text}
      </Typography>
    </Stack>
  );
  const seg = (stroke, dash) => (
    <svg width="22" height="6" aria-hidden="true">
      <line
        x1="0"
        y1="3"
        x2="22"
        y2="3"
        stroke={stroke}
        strokeWidth="1.5"
        strokeDasharray={dash}
      />
    </svg>
  );
  return (
    <Stack
      direction="row"
      spacing={1.75}
      useFlexGap
      flexWrap="wrap"
      rowGap={0.5}
    >
      {item("go", seg(fg), "Asked")}
      {has("back") &&
        item("back", seg(sub, "4 3"), "Answer")}
      {has("none") && item("none", seg(bad, "5 3"), "No answer")}
      {has("tool") &&
        item(
          "tool",
          <svg width="16" height="12" aria-hidden="true">
            <path
              d="M1,2 h9 a4,4 0 0 1 0,8 h-6"
              fill="none"
              stroke={sub}
              strokeWidth="1.3"
            />
            <path d="M5,7 L1,10 L5,13 z" fill={sub} />
          </svg>,
          "Tool call",
        )}
    </Stack>
  );
}
Legend.propTypes = { seq: PropTypes.object.isRequired };

const json = (v) =>
  v == null ? null : typeof v === "string" ? v : JSON.stringify(v, null, 2);

function Quote({ children, code, bad }) {
  return (
    <Box
      sx={{
        mt: 0.5,
        px: 1.25,
        py: 1,
        borderRadius: "6px",
        bgcolor: (t) =>
          bad
            ? alpha(t.palette.error.main, 0.08)
            : t.palette.background.neutral,
        color: bad ? "error.main" : "text.secondary",
        typography: "s2",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        ...(code && {
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 11.5,
        }),
      }}
    >
      {children}
    </Box>
  );
}
Quote.propTypes = {
  children: PropTypes.node,
  code: PropTypes.bool,
  bad: PropTypes.bool,
};

const Cap = ({ children }) => (
  <Typography
    sx={{
      mt: 0.75,
      typography: "s3",
      color: "text.disabled",
      fontWeight: 600,
      textTransform: "uppercase",
      letterSpacing: "0.04em",
      fontSize: 10.5,
    }}
  >
    {children}
  </Typography>
);
Cap.propTypes = { children: PropTypes.node };

const Label = ({ children }) => (
  <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
    {children}
  </Typography>
);
Label.propTypes = { children: PropTypes.node };

const smallBtn = { height: 24, px: 1, typography: "s3", whiteSpace: "nowrap" };

/* Copy a link that opens the run on this call, trial and step. */
function CopyLink({ url }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* Clipboard refused: the link stays on the button's tooltip. */
    }
  };
  return (
    <Tooltip title={copied ? "Link copied" : "Copy a link to this step"} arrow>
      <IconButton
        size="small"
        onClick={copy}
        aria-label="Copy a link to this step"
        sx={{
          width: 24,
          height: 24,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
        }}
      >
        <Iconify
          icon={copied ? "solar:check-read-linear" : "solar:link-linear"}
          width={14}
        />
      </IconButton>
    </Tooltip>
  );
}
CopyLink.propTypes = { url: PropTypes.string.isRequired };

/* The picked step: what was asked and what came back, in full, with what
   it cost and links out. */
function StepDetail({
  seq,
  step,
  onOpenTrace,
  onShowInTranscript,
  linkOf,
  failures = [],
}) {
  const tool = step.kind === "tool";
  const to = tool ? step.tool : seq.nameOf(step.to);
  const a = step.answer;
  const waited = a?.kind === "none" && step.dur != null;
  const meta = [
    step.dur != null &&
      (waited
        ? `Waited ${took(step.dur)}, no answer`
        : `Took ${took(step.dur)}`),
    step.turns > 1 && `${step.turns} turns`,
    step.model && `Model ${step.model}`,
    step.tokens != null && `${step.tokens.toLocaleString()} tokens`,
    step.kind === "handoff" &&
      step.tools > 0 &&
      `${step.tools} tool ${step.tools === 1 ? "call" : "calls"}`,
  ].filter(Boolean);
  const link = linkOf ? linkOf(step.n) : null;

  const asked = tool ? (
    <>
      <Label>Input</Label>
      <Box>
        {step.input != null ? (
          <Quote code>{json(step.input)}</Quote>
        ) : (
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            Not in the record
          </Typography>
        )}
      </Box>
    </>
  ) : (
    <>
      <Label>
        {step.from === seq.lanes[1]?.id && step.to === seq.lanes[0]?.id
          ? "Said"
          : "Asked"}
      </Label>
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 500 }}>
          {step.label}
        </Typography>
        <Cap>Exact message</Cap>
        {step.full ? (
          <Quote>{step.full}</Quote>
        ) : (
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            Not in the record
          </Typography>
        )}
      </Box>
    </>
  );
  const answered = tool ? (
    <>
      <Label>Result</Label>
      <Box>
        <Typography
          sx={{
            typography: "s2",
            fontWeight: 500,
            color: step.bad ? "error.main" : "text.primary",
          }}
        >
          {step.result}
        </Typography>
        {step.output != null && (
          <Quote code bad={step.bad}>
            {json(step.output)}
          </Quote>
        )}
      </Box>
    </>
  ) : a ? (
    <>
      <Label>Answered</Label>
      <Box>
        {a.kind === "none" ? (
          <Quote bad={a.bad}>{a.full}</Quote>
        ) : (
          <>
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 500,
                color: a.bad ? "error.main" : "text.primary",
              }}
            >
              {a.label}
            </Typography>
            {a.full && (
              <>
                <Cap>Exact message</Cap>
                <Quote>{a.full}</Quote>
              </>
            )}
          </>
        )}
      </Box>
    </>
  ) : null;

  return (
    <Stack spacing={1.25}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        useFlexGap
        flexWrap="wrap"
      >
        <Pill n={step.n} bad={step.bad} />
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {`${seq.nameOf(step.from)} → ${to}`}
        </Typography>
        <Box
          component="span"
          sx={{
            typography: "s3",
            px: 0.75,
            borderRadius: "4px",
            bgcolor: "background.neutral",
            color: "text.secondary",
          }}
        >
          {KIND[step.kind]}
        </Box>
        <Box sx={{ flex: 1 }} />
        {onShowInTranscript && step.at != null && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => onShowInTranscript(step.at)}
            startIcon={<Iconify icon="solar:play-circle-linear" width={14} />}
            sx={smallBtn}
          >
            Show in transcript
          </Button>
        )}
        {onOpenTrace && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => onOpenTrace(step.spanId)}
            startIcon={
              <Iconify icon="solar:square-top-down-linear" width={14} />
            }
            sx={smallBtn}
          >
            Open in trace
          </Button>
        )}
        {link && <CopyLink url={link} />}
      </Stack>
      {meta.length > 0 && (
        <Typography
          sx={{
            typography: "s3",
            color: waited ? "error.main" : "text.tertiary",
            fontVariantNumeric: "tabular-nums",
            mt: "-4px !important",
          }}
        >
          {meta.join(" · ")}
        </Typography>
      )}
      {failures.map((f) => {
        const unsure = f.status === "inconclusive";
        const tone = unsure ? "warning" : "error";
        return (
          <Box
            key={f.key || f.text}
            sx={{
              border: "1px solid",
              borderColor: (t) => alpha(t.palette[tone].main, 0.35),
              bgcolor: (t) => alpha(t.palette[tone].main, 0.05),
              borderRadius: "6px",
              px: 1.25,
              py: 1,
            }}
          >
            <Typography
              sx={{ typography: "s2", fontWeight: 600, color: `${tone}.main` }}
            >
              {`${unsure ? "Unsure" : f.mark === "critical" ? "Failed" : "Missed"} ${f.mark === "critical" ? "critical" : "supporting"} sub-goal: ${f.text}`}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {`${seq.nameOf(f.lane)}${f.row === "answer" ? ` · its reply to step ${step.n}` : ""} · ${f.basis === "the conversation" ? "judged from the conversation" : "checked against end state and tool calls"}`}
            </Typography>
            {f.said && (
              <>
                <Cap>Where it broke</Cap>
                <Quote>{f.said}</Quote>
              </>
            )}
          </Box>
        );
      })}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "88px minmax(0, 1fr)",
          rowGap: 1.25,
          columnGap: 1.5,
        }}
      >
        {asked}
        {answered}
      </Box>
    </Stack>
  );
}
StepDetail.propTypes = {
  failures: PropTypes.array,
  seq: PropTypes.object.isRequired,
  step: PropTypes.object.isRequired,
  onOpenTrace: PropTypes.func,
  onShowInTranscript: PropTypes.func,
  linkOf: PropTypes.func,
};

/* Lane header: the name on one line, or two when it doesn't fit. */
const nameLines = (name, chars) => {
  const s = String(name || "");
  if (s.length <= chars) return [s];
  const words = s.split(" ");
  let first = "";
  while (words.length && `${first} ${words[0]}`.trim().length <= chars)
    first = `${first} ${words.shift()}`.trim();
  if (!first) return [cut(s, chars)];
  return [first, cut(words.join(" "), chars)];
};

/**
 * The diagram. `selected` is a step number; picking a step (its arrow, its
 * answer, its pill) calls onSelect(n). The step column stays put while the
 * lanes scroll sideways in a narrow drawer.
 */
export default function SequenceDiagram({
  seq,
  selected,
  onSelect,
  onOpenTrace,
  onShowInTranscript,
  linkOf,
  failures = [],
}) {
  const theme = useTheme();
  const wrapRef = useRef(null);
  const width = useWidth(wrapRef);
  const [hover, setHover] = useState(null);
  const [lane, setLane] = useState(null); // followed participant
  const [foldTools, setFoldTools] = useState(false);
  const [problemsOnly, setProblemsOnly] = useState(false);
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
  /* Problems: the record's (no answer, failed calls) and the failed
     sub-goals placed on a step — each with the steps that led to it. */
  const chain = new Set(seq.chain);
  failures
    .filter((f) => f.n != null)
    .forEach((f) => {
      let cur = byN[f.n];
      while (cur && !chain.has(cur.n)) {
        chain.add(cur.n);
        cur = cur.parent != null ? byN[cur.parent] : null;
      }
    });
  const hasProblems = chain.size > 0;

  /* What's on show: tool calls folded into their hand-off, or only the
     problems and the steps that led to them. Numbers never change. */
  const shown = seq.steps.filter(
    (s) =>
      !(foldTools && s.kind === "tool") &&
      !(problemsOnly && hasProblems && !chain.has(s.n)),
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
    return right >= Math.min(260, left) ? { left: false, room: right } : { left: true, room: left };
  };

  let y = top;
  const rows = seq.rows
    .filter((r) => shownSet.has(r.n))
    .map((r) => {
      const out = { ...r, y };
      y += ROW_H[r.type];
      return out;
    });
  const H = y + 6;

  const pick = (n) => onSelect(selected === n ? null : n);
  const keyPick = (n) => (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(n);
    }
  };
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
    else if (e.key === "Escape" && selected != null) {
      /* Clears the step; a second Esc closes the drawer as usual. */
      e.preventDefault();
      e.stopPropagation();
      onSelect(null);
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
    "aria-label": `Step ${s.n}: ${s.label}`,
    onClick: () => pick(s.n),
    onKeyDown: keyPick(s.n),
    onMouseEnter: () => setHover(s.n),
    onMouseLeave: () => setHover(null),
    style: {
      cursor: "pointer",
      outline: "none",
      opacity: involves(s) ? 1 : 0.22,
      transition: "opacity 120ms",
    },
  });

  /* The fixed column: the step pill. A reply has no number of its own —
     only steps are numbered (17 Nov); the reply says which step it answers. */
  const gutterRow = (r) => {
    const s = byN[r.n];
    const yy = r.y;
    const on = selected === s.n;
    const step = r.type === "go" || r.type === "tool";
    const w = s.n > 9 ? 26 : 20;
    const bad = step && s.bad;
    return (
      <g key={`${r.type}-${r.n}`} {...rowProps(s, r)}>
        {bandOf(r, GUT, on)}
        {step ? (
          <>
            <rect
              x={14}
              y={yy - 9}
              width={w}
              height={18}
              rx={9}
              fill={bad ? c.bad : c.fg}
            />
            <text
              x={14 + w / 2}
              y={yy + 4}
              textAnchor="middle"
              fontSize="11"
              fontWeight="600"
              fill={bad ? "#fff" : c.paper}
            >
              {s.n}
            </text>
          </>
        ) : null}
      </g>
    );
  };

  /* The lanes part of a row: the arrow and its words, or the tool box. */
  const laneRow = (r) => {
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
          s.turns > 1 && `×${s.turns} turns`,
          foldTools &&
            s.tools > 0 &&
            `+${s.tools} tool ${s.tools === 1 ? "call" : "calls"}`,
          slow && `slow · ${took(s.dur)}`,
        ]
          .filter(Boolean)
          .join(" · ") || null;
      const chipW = chip ? chip.length * 5.9 + 12 : 0;
      const label = cut(s.label, roomOf(x1, x2) - Math.ceil(chipW / CH));
      const lw = label.length * CH;
      const mid =
        placeX((x1 + x2) / 2, `${label}${" ".repeat(Math.ceil(chipW / CH))}`) -
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
          <title>{s.full || s.label}</title>
          {label}
        </text>,
      );
      if (chip)
        items.push(
          <g key="chip">
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
      const label = cut(
        s.answer.label,
        Math.max(roomOf(x1, x2) - tag.length, Math.floor((svgW - 24) / CH) - tag.length - 2, 18),
      );
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
          x={placeX((x1 + x2) / 2, `${label}${tag}`)}
          y={yy - 3}
          textAnchor="middle"
          fontSize="11.5"
          fontWeight={r.type === "none" || on ? 600 : 400}
          fill={bad ? c.bad : c.sub}
        >
          <title>{s.answer.full || s.answer.label}</title>
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
      const nameW = 6.6;
      const name = cut(s.tool, Math.floor((side.room * 0.55) / nameW));
      const resRoom = Math.floor(
        (side.room - name.length * nameW - 18) / 6.1,
      );
      const res = cut(`→ ${s.result || ""}${tail}`, resRoom);
      const textW = name.length * nameW + 8 + res.length * 6.1;
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
          stroke={s.bad ? c.bad : c.muted}
          strokeWidth="1.3"
        />,
        <path
          key="tip"
          d={
            side.left
              ? `M${x - 3},${yy + 5} l-4,-3 v6 z`
              : `M${x + 3},${yy + 5} l4,-3 v6 z`
          }
          fill={s.bad ? c.bad : c.muted}
        />,
        <text
          key="n"
          x={tx}
          y={yy + 4}
          fontSize="11"
          fontWeight={wt}
          fill={c.fg}
          fontFamily={mono}
        >
          <title>{s.tool}</title>
          {name}
        </text>,
        <text
          key="r"
          x={tx + name.length * nameW + 8}
          y={yy + 4}
          fontSize="11.5"
          fontWeight={s.bad ? 600 : 400}
          fill={s.bad ? c.bad : c.sub}
        >
          <title>{s.result}</title>
          {res}
        </text>,
      );
    }
    /* A failed sub-goal tied to this row: a tag beside the lane of the
       participant it came from. */
    const rowKind =
      r.type === "back" ? "answer" : r.type === "tool" ? "tool" : r.type;
    failures
      .filter((f) => f.n === s.n && f.row === rowKind)
      .forEach((f, k) => {
        const unsure = f.status === "inconclusive";
        const col = unsure ? c.warn : c.bad;
        const text = `${unsure ? "?" : "✕"} ${unsure ? "Unsure" : "Failed"} sub-goal`;
        const w = text.length * 6 + 14;
        const lx = X[f.lane] ?? X[s.from];
        /* A tool box sits right of its lane, so the tag goes left of it;
           an arrow's words sit above it, so the tag goes beside its end. */
        const tool = r.type === "tool" && !toolSide(s.from).left;
        const right = !tool && lx + 8 + w <= svgW - 4;
        const bx = right ? lx + 8 : Math.max(2, lx - 8 - w);
        const by = tool ? yy : yy + 7;
        items.push(
          <g key={`f-${k}`}>
            <title>{`${f.text}${f.said ? ` · where it broke: "${f.said}"` : ""}`}</title>
            <rect
              x={bx}
              y={by - 8}
              width={w}
              height={17}
              rx={8.5}
              fill={c.paper}
              stroke={col}
            />
            <text
              x={bx + w / 2}
              y={by + 4}
              textAnchor="middle"
              fontSize="10.5"
              fontWeight="600"
              fill={col}
            >
              {text}
            </text>
          </g>,
        );
      });
    return (
      <g key={`${r.type}-${r.n}`} {...rowProps(s, r)}>
        {bandOf(r, svgW, on)}
        {items}
      </g>
    );
  };

  const sel = selected != null ? byN[selected] : null;
  const svgStyle = {
    display: "block",
    fontFamily: "Inter, system-ui, sans-serif",
  };
  const labelY = 10 + headH + 18;
  const followed = lane ? seq.lanes.find((l) => l.id === lane) : null;
  const followedCount = followed ? shown.filter(involves).length : 0;

  return (
    <Stack>
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="center"
        useFlexGap
        flexWrap="wrap"
        sx={{ px: 2, pb: 0.75 }}
      >
        <Box sx={{ flex: 1, minWidth: 240 }}>
          <Legend seq={seq} />
        </Box>
        <Stack direction="row" spacing={0.75}>
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
        <svg
          width={GUT}
          height={H}
          viewBox={`0 0 ${GUT} ${H}`}
          style={{ ...svgStyle, flexShrink: 0 }}
        >
          <text
            x={14}
            y={labelY}
            fontSize="10.5"
            fontWeight="600"
            letterSpacing="0.05em"
            fill={c.faint}
          >
            STEP
          </text>
          {rows.map(gutterRow)}
        </svg>
        <Box sx={{ flex: 1, minWidth: 0, overflowX: "auto" }}>
          <svg
            width={svgW}
            height={H}
            viewBox={`0 0 ${svgW} ${H}`}
            role="img"
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
                  {failures.some((f) => f.n != null && f.lane === ln.id) && (
                    <circle
                      cx={X[ln.id] + headW / 2 - 8}
                      cy={18}
                      r={3.5}
                      fill={c.bad}
                    >
                      <title>A failed sub-goal came from here</title>
                    </circle>
                  )}
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
      {(seq.notRun.length > 0 || seq.notObserved.length > 0) && (
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
          {[
            seq.notRun.length &&
              `Declared, didn't run: ${seq.notRun.join(", ")}`,
            seq.notObserved.length &&
              `Declared, no events sent: ${seq.notObserved.join(", ")}`,
          ]
            .filter(Boolean)
            .join(" · ")}
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
            failures={failures.filter((f) => f.n === sel.n)}
          />
        ) : (
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            Pick a step to see the exact message and what came back. ↑ ↓ move
            between steps; click a name to follow that participant.
          </Typography>
        )}
      </Box>
    </Stack>
  );
}

SequenceDiagram.propTypes = {
  /* Failed sub-goals placed on steps (placeFailures). */
  failures: PropTypes.array,
  seq: PropTypes.object.isRequired,
  selected: PropTypes.number,
  onSelect: PropTypes.func.isRequired,
  onOpenTrace: PropTypes.func,
  onShowInTranscript: PropTypes.func,
  linkOf: PropTypes.func,
};
