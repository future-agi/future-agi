import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { SEQUENCE_COPY as C } from "./sequence.constants";
import {
  LONG_STEPS,
  SVG_TYPE,
  coloursOf,
  layoutOf,
  useWidth,
} from "./sequenceLayout";
import { Legend, Toggle } from "./SequenceParts";
import { LaneHeads, LaneLines } from "./SequenceLanes";
import { StepRow, TurnRow } from "./SequenceRow";
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
  canOpenTrace,
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
  const c = coloursOf(theme);
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
  const layout = layoutOf(seq.lanes, width, ordered);
  const { svgW, H } = layout;
  const pick = (n) => onSelect(selected === n ? null : n);
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

  const sel = selected != null ? byN[selected] : null;
  const svgStyle = {
    display: "block",
    fontFamily: SVG_TYPE.family,
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
              {C.allTurns}
            </Toggle>
          )}
          {hasTools && (
            <Toggle on={foldTools} onClick={() => setFoldTools((v) => !v)}>
              {C.foldTools}
            </Toggle>
          )}
          {hasProblems && (
            <Toggle
              on={problemsOnly}
              onClick={() => setProblemsOnly((v) => !v)}
            >
              {C.problemsOnly}
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
                C.problemsShown(shown.length, seq.steps.length),
              followed &&
                C.following(followed.name, followedCount, shown.length),
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
            {C.showAll}
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
            aria-label={C.sequenceOf(shown.length)}
            style={svgStyle}
          >
            <defs>
              {mk("sq-fg", c.fg)}
              {mk("sq-muted", c.muted)}
              {mk("sq-bad", c.bad)}
            </defs>
            <LaneLines
              lanes={seq.lanes}
              layout={layout}
              followed={lane}
              c={c}
            />
            {layout.rows.map((r) =>
              r.type === "turn" ? (
                <TurnRow
                  key={`turn-${r.group.turn}`}
                  row={r}
                  seq={seq}
                  byN={byN}
                  svgW={svgW}
                  c={c}
                  open={!foldedTurn.has(r.group.turn)}
                  onToggle={() =>
                    setTurnOpen((was) => ({
                      ...was,
                      [r.group.turn]: foldedTurn.has(r.group.turn),
                    }))
                  }
                />
              ) : (
                <StepRow
                  key={`${r.type}-${r.n}`}
                  row={r}
                  seq={seq}
                  byN={byN}
                  layout={layout}
                  c={c}
                  selected={selected}
                  hover={hover}
                  foldTools={foldTools}
                  faded={!involves(byN[r.n])}
                  onPick={pick}
                  onHover={setHover}
                  onToggleLoop={toggleLoop}
                />
              ),
            )}
            <LaneHeads
              lanes={seq.lanes}
              layout={layout}
              followed={lane}
              onFollow={setLane}
              c={c}
            />
          </svg>
        </Box>
      </Box>
      <Box
        sx={{ px: 2, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}
      >
        {sel ? (
          <StepDetail
            seq={seq}
            step={sel}
            onOpenTrace={onOpenTrace}
            canOpenTrace={canOpenTrace}
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
  canOpenTrace: PropTypes.func,
  onShowInTranscript: PropTypes.func,
  linkOf: PropTypes.func,
};
