import { useLayoutEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import AgentGraph from "src/sections/projects/LLMTracing/GraphSection/AgentGraph";
import TraceTreeV2 from "src/components/traceDetail/TraceTreeV2";
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import {
  CHECKS_SOON,
  LIMITS_SOON,
  TOPOLOGY,
  TRACES_SOON,
  participantsOf,
} from "../_mock/participants";
import {
  USER_NODE,
  filterSpansToNode,
  participantsGraphOf,
} from "../_mock/participantsGraph";
import { participantsFromTimeline } from "../_mock/workerTimeline";
import { CONTRACT_SAMPLES } from "../_mock/workerTimelineSamples";
import SharedState from "./SharedState";

/* ?contract=<name> shows one of the API's own call records instead;
   ?participants=partial shows a record whose read stopped early. */
const knob = (key) => {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(key);
};
const contractSample = () => {
  const name = knob("contract");
  return name && CONTRACT_SAMPLES[name] ? name : null;
};
const PARTIAL_NOTE =
  "Partial read: the record stopped at 5,000 events, so later steps aren't shown.";

const miniBtn = { height: 24, px: 1, typography: "s3", flexShrink: 0 };

/* Open trace stays off until the app's own traces are linked to the call. */
function TracesLink() {
  return (
    <Tooltip title={TRACES_SOON} arrow>
      <Stack
        direction="row"
        spacing={0.5}
        alignItems="center"
        sx={{ color: "text.disabled", cursor: "default" }}
      >
        <Iconify icon="solar:square-top-down-linear" width={14} />
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          Open trace
        </Typography>
      </Stack>
    </Tooltip>
  );
}

const Quiet = ({ children }) => (
  <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
    {children}
  </Typography>
);
Quiet.propTypes = { children: PropTypes.node };

/* On top: the call in a sentence, then whether it was recorded (the full
   wording and the supported shape on hover) and Open trace. */
function Recorded({ p }) {
  const why = `${
    p.observed
      ? "Recorded in this run — every hand-off and answer is a real event."
      : "Not recorded — the app sent no worker events, so this shows how it's set up, not what happened."
  } Supported shape: ${p.topology || TOPOLOGY}.`;
  return (
    <Stack spacing={0.5}>
      <Typography sx={{ typography: "s2", fontWeight: 500 }}>
        {p.summary}
      </Typography>
      <Stack direction="row" spacing={1.5} alignItems="center">
        <Tooltip title={why} arrow>
          <Stack
            direction="row"
            spacing={0.5}
            alignItems="center"
            sx={{ cursor: "default" }}
          >
            <Iconify
              icon={
                p.observed
                  ? "solar:check-circle-linear"
                  : "solar:info-circle-linear"
              }
              width={14}
              sx={{ color: p.observed ? "success.main" : "text.tertiary" }}
            />
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {p.observed ? "Recorded in this run" : "Not recorded"}
            </Typography>
          </Stack>
        </Tooltip>
        {p.traces && <TracesLink />}
      </Stack>
    </Stack>
  );
}
Recorded.propTypes = { p: PropTypes.object.isRequired };

/* What isn't recorded yet, and the call's shared state. The Tool / Used by
   list is gone: the graph draws each tool under the worker that called it. */
function Details({ agent, task }) {
  const [open, setOpen] = useState(false);
  return (
    <Box>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        onClick={() => setOpen((v) => !v)}
        endIcon={
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={12}
          />
        }
      >
        Details
      </Button>
      {open && (
        <Stack spacing={2} sx={{ mt: 1.5 }}>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "160px 1fr",
              rowGap: 1,
              columnGap: 2,
              typography: "s2",
              color: "text.tertiary",
            }}
          >
            <Box>Tool limits</Box>
            <Box>{LIMITS_SOON}</Box>
            <Box>Checks on worker events</Box>
            <Box>{CHECKS_SOON}</Box>
          </Box>
          <SharedState agent={agent} task={task} />
        </Stack>
      )}
    </Box>
  );
}
Details.propTypes = { agent: PropTypes.object, task: PropTypes.object };

const BAD = new Set(["no answer came back", "failed", "no reply"]);

/* The way back (Chintan, 13 Nov: both directions): one line per outcome,
   "Front desk ← Refunds specialist + Escalations · both answered". Turns
   read "replied". */
const backLinesOf = (h) => {
  if (h.turn) {
    const o = h.outcomes[0];
    const each = h.turns > 1 ? " each time" : "";
    return [
      {
        names: h.toNames,
        text:
          o === "no reply"
            ? "no reply"
            : `replied${each}${h.alone ? ", on its own" : ""}${
                h.after ? ` after ${h.after}` : ""
              }`,
      },
    ];
  }
  const groups = [];
  h.outcomes.forEach((o, i) => {
    const g = groups.find((x) => x.o === o);
    if (g) g.names.push(h.toNames[i]);
    else groups.push({ o, names: [h.toNames[i]] });
  });
  return groups.map(({ o, names }) => ({
    names,
    text:
      names.length === 1
        ? o
        : o === "answered"
          ? `${names.length === 2 ? "both" : `all ${names.length}`} answered`
          : o === "no answer came back"
            ? "no answer from either"
            : `${names.length === 2 ? "both" : `all ${names.length}`} ${o}`,
  }));
};

/* One direction of a hand-off: "From → To" or "From ← To", with a quiet
   note after it. */
function Leg({ from, to, back, note, bad }) {
  return (
    <Typography
      sx={{
        typography: "s2",
        fontWeight: back ? 400 : 500,
        color: back ? "text.secondary" : "text.primary",
      }}
    >
      {from}
      <Box
        component="span"
        sx={{
          color: bad ? "error.main" : "text.disabled",
          fontWeight: 400,
          mx: 0.75,
        }}
      >
        {back ? "←" : "→"}
      </Box>
      {to}
      {note && (
        <Box
          component="span"
          sx={{
            typography: "s3",
            color: bad ? "error.main" : "text.tertiary",
            fontWeight: bad ? 500 : 400,
          }}
        >
          {` · ${note}`}
        </Box>
      )}
    </Typography>
  );
}
Leg.propTypes = {
  from: PropTypes.string,
  to: PropTypes.string,
  back: PropTypes.bool,
  note: PropTypes.string,
  bad: PropTypes.bool,
};

/* Hand-offs (Nosang, 12 Nov): a short numbered list, one row per time work
   was handed on, with the same numbers on the graph's arrows; each row
   shows the way out and the way back (Chintan, 13 Nov). Hovering a
   row lights up its arrows and boxes; clicking it shows who got the work in
   the tree, as a box does. */
function HandoffList({ handoffs, onHover, onPick }) {
  return (
    <Box sx={{ px: 2, pt: 1.5 }} onMouseLeave={() => onHover(null)}>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 0.75 }}>
        {`Hand-offs · ${handoffs.length}`}
      </Typography>
      <Box
        sx={{
          maxHeight: 200,
          overflow: "auto",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
        }}
      >
        {handoffs.map((h, i) => {
          const pick = () => onPick(h.to.length === 1 ? h.to[0] : h.from);
          return (
            <Box
              key={h.n}
              role="button"
              tabIndex={0}
              onMouseEnter={() => onHover(i)}
              onFocus={() => onHover(i)}
              onClick={pick}
              onKeyDown={(e) => e.key === "Enter" && pick()}
              sx={{
                display: "grid",
                gridTemplateColumns: "24px minmax(0, 1fr)",
                columnGap: 1.25,
                alignItems: "start",
                px: 1.25,
                py: 0.875,
                cursor: "pointer",
                "&:not(:first-of-type)": {
                  borderTop: "1px solid",
                  borderColor: "divider",
                },
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Box
                component="span"
                sx={{
                  mt: "1px",
                  height: 20,
                  lineHeight: "20px",
                  textAlign: "center",
                  typography: "s3",
                  fontWeight: 600,
                  fontFamily: "ui-monospace, SFMono-Regular, monospace",
                  borderRadius: "4px",
                  bgcolor: "background.neutral",
                }}
              >
                {h.n}
              </Box>
              <Box sx={{ minWidth: 0 }}>
                <Leg
                  from={h.fromName}
                  to={h.toNames.join(" + ")}
                  note={
                    h.together
                      ? "at the same time"
                      : h.turns > 1
                        ? `${h.turns} turns in a row`
                        : null
                  }
                />
                {backLinesOf(h).map((b) => (
                  <Leg
                    key={b.names.join()}
                    back
                    from={h.fromName}
                    to={b.names.join(" + ")}
                    note={b.text}
                    bad={BAD.has(b.text) || b.text === "no answer from either"}
                  />
                ))}
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
HandoffList.propTypes = {
  handoffs: PropTypes.array.isRequired,
  onHover: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
};

/* CSS that lights up one hand-off on the graph: its arrows and the boxes at
   both ends. Edge ids follow AgentGraph's own naming. */
const handoffHighlightSx = (handoffs, edges, i) => {
  const h = i == null ? null : handoffs[i];
  if (!h) return {};
  const ids = [h.from, ...h.to];
  const edgeIds = edges
    .map((e, idx) =>
      !e._return && e.source === h.from && h.to.includes(e.target)
        ? `e-${idx}`
        : e._return && e.target === h.from && h.to.includes(e.source)
          ? `e-ret-${idx}`
          : null,
    )
    .filter(Boolean);
  const nodeSel = ids.map((id) => `[data-id="${CSS.escape(id)}"]`);
  const edgeSel = edgeIds.map((id) => `[data-id="${id}"]`);
  return {
    "& .react-flow__node": { opacity: 0.3, transition: "opacity 120ms" },
    "& .react-flow__edge": { opacity: 0.25 },
    ...Object.fromEntries(
      nodeSel.map((sel) => [`& .react-flow__node${sel}`, { opacity: 1 }]),
    ),
    ...Object.fromEntries(
      edgeSel.map((sel) => [
        `& .react-flow__edge${sel}`,
        {
          opacity: 1,
          "& .react-flow__edge-path": {
            // The edge's own inline stroke wins otherwise.
            stroke: (t) => `${t.palette.primary.main} !important`,
            strokeWidth: "2px !important",
          },
        },
      ]),
    ),
  };
};

/* Tall enough for the graph's levels and no more, so there's no empty band
   between the hand-off list and the graph. */
const graphHeightOf = (graph) => {
  const kids = {};
  graph.edges
    .filter((e) => !e._return)
    .forEach((e) => (kids[e.source] ||= []).push(e.target));
  const targets = new Set(
    graph.edges.filter((e) => !e._return).map((e) => e.target),
  );
  const roots = graph.nodes.filter((nd) => !targets.has(nd.id));
  let depth = 0;
  const seen = new Set();
  const walk = (id, d) => {
    if (seen.has(id)) return;
    seen.add(id);
    depth = Math.max(depth, d);
    (kids[id] || []).forEach((k) => walk(k, d + 1));
  };
  (roots.length ? roots : graph.nodes.slice(0, 1)).forEach((r) =>
    walk(r.id, 1),
  );
  return Math.min(520, Math.max(260, depth * 120 + 40));
};

const dashedKey = (edges) => {
  const control = edges.some((e) => e._control);
  const ghost = edges.some((e) => e._ghost);
  if (control && ghost) return "Dashed: took over the call, or never ran";
  if (control) return "Dashed: took over the call";
  return ghost && "Dashed: never ran";
};

/* What each line in the graph means — only the kinds this call has. */
function EdgeKey({ edges }) {
  const parts = [
    edges.some((e) => !e._return && !e._control && !e._ghost) &&
      "Solid: handed work",
    edges.some((e) => e._return && !e._missing) &&
      "Dotted: answer back, same number",
    edges.some((e) => e._missing) && "Red: no answer",
    dashedKey(edges),
  ].filter(Boolean);
  if (parts.length < 2) return null;
  return (
    <Typography
      sx={{ typography: "s3", color: "text.tertiary", px: 2, pt: 0.75 }}
    >
      {parts.join(" · ")}
    </Typography>
  );
}
EdgeKey.propTypes = { edges: PropTypes.array.isRequired };

/* The names above a span in the tree, short: "Call", "Turn 1", "Front desk". */
const crumbsTo = (entries, id, trail = []) => {
  for (const e of entries || []) {
    const sp = e.observation_span || e.observationSpan || {};
    if (sp.id === id) return trail;
    const name = String(sp.name || "")
      .replace(/^Call \(whole session\)$/, "Call")
      .replace(/^(Turn \d+).*/, "$1");
    const found = crumbsTo(e.children, id, [...trail, name]);
    if (found) return found;
  }
  return null;
};

const findEntry = (entries, id) => {
  for (const e of entries || []) {
    if (e.observation_span?.id === id) return e;
    const hit = findEntry(e.children, id);
    if (hit) return hit;
  }
  return null;
};

/**
 * A multi-agent call's Participants (proposal: internal-docs
 * participants-view-proposal.md): the record drawn with Observe's own Trace
 * Tree (grouped by turn, each named by the caller's message) and Agent Graph,
 * with step numbers on edges, "Not run" ghosts and recorded end states.
 * Clicking a graph node filters the tree to that worker; clicking a row opens
 * the existing span detail.
 */
export default function HandoffTimeline({ agent, task, participants }) {
  const sample = contractSample();
  const partial = knob("participants") === "partial";
  const p = useMemo(() => {
    const rec = sample
      ? participantsFromTimeline(CONTRACT_SAMPLES[sample])
      : participants || participantsOf({ agent, task });
    return rec && partial ? { ...rec, partial: PARTIAL_NOTE } : rec;
  }, [agent, task, participants, sample, partial]);
  /* The actor on the call names the caller's node (concept audit, 12 Nov). */
  const g = useMemo(
    () => participantsGraphOf(p, { callerName: task?.persona?.name || null }),
    [p, task?.persona?.name],
  );
  const [focusNode, setFocusNode] = useState(null);
  const [spanId, setSpanId] = useState(null);
  // Tree height: set once so the bar below it starts in the middle of the
  // visible tab; a drag on the bar changes it.
  const [treeH, setTreeH] = useState(null);
  const [hoverStop, setHoverStop] = useState(null);
  const treeRef = useRef(null);
  useLayoutEffect(() => {
    const tree = treeRef.current;
    if (treeH != null || !tree) return;
    let pane = tree.parentElement;
    while (pane && !/(auto|scroll)/.test(getComputedStyle(pane).overflowY))
      pane = pane.parentElement;
    if (!pane) return;
    const top =
      tree.getBoundingClientRect().top - pane.getBoundingClientRect().top;
    setTreeH(Math.max(120, Math.round(pane.clientHeight / 2 - top)));
  }, [treeH, spanId]);
  if (!p || !g) return null;

  // Same bar as Observe's trace view (tree above, Agent Graph below).
  const startTreeDrag = (e) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = treeRef.current?.offsetHeight || 0;
    const onMove = (m) =>
      setTreeH(Math.min(1200, Math.max(120, startH + m.clientY - startY)));
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
    };
    document.body.style.cursor = "row-resize";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  const nodeById = Object.fromEntries(g.graph.nodes.map((n) => [n.id, n]));
  const focus = focusNode ? nodeById[focusNode] : null;
  const focusIsGhost = !!focus?._notRunNames;
  const spans =
    focus && !focusIsGhost && focusNode !== USER_NODE
      ? filterSpansToNode(g.spans, focusNode)
      : g.spans;
  const graph = focus
    ? {
        ...g.graph,
        nodes: g.graph.nodes.map((n) =>
          n.type === "start" || n.type === "end"
            ? n
            : { ...n, _hasMatch: n.id === focusNode },
        ),
      }
    : g.graph;

  const selected = spanId ? findEntry(g.spans, spanId) : null;
  if (selected) {
    return (
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          spacing={1.5}
          alignItems="center"
          sx={{
            px: 2,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => setSpanId(null)}
            startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
          >
            All participants
          </Button>
        </Stack>
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          <SpanDetailPane
            entry={selected}
            allSpans={g.spans}
            traceStartTime={g.spans[0]?.observation_span?.start_time}
            isRootSpan={spanId === "call"}
            onClose={() => setSpanId(null)}
            onSelectSpan={setSpanId}
            previewOnly
            breadcrumb={crumbsTo(g.spans, spanId)}
          />
        </Box>
      </Stack>
    );
  }

  return (
    <Stack>
      <Stack spacing={1} sx={{ px: 2, py: 1.5 }}>
        {sample && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`API record · "${sample}" sample from the platform contract`}
          </Typography>
        )}
        <Recorded p={p} />
        {p.partial && <Quiet>{p.partial}</Quiet>}
        {focus && (
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography sx={{ typography: "s2", flex: 1 }}>
              {focusIsGhost
                ? `${focus._notRunNames.join(", ")} ${focus._notRunNames.length > 1 ? "were" : "was"} declared and didn't run in this call.`
                : `Showing ${focus.name} only`}
            </Typography>
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={() => setFocusNode(null)}
              sx={miniBtn}
            >
              Show all
            </Button>
          </Stack>
        )}
      </Stack>
      <Divider />
      <Box
        ref={treeRef}
        sx={{
          ...(treeH ? { height: treeH } : { maxHeight: 560 }),
          overflow: "auto",
          /* Participants reads at 12px; Observe's own tree keeps 13px. */
          "& .trace-tree-span-name": { fontSize: 12 },
          "& .trace-tree-toolbar": { borderBottom: "none" },
        }}
      >
        <TraceTreeV2
          key={focusNode || "all"}
          spans={spans}
          selectedSpanId={spanId}
          onSelectSpan={setSpanId}
          visibleMetrics={{ latency: true, tokens: false, cost: false }}
          showMetrics
        />
      </Box>
      <Box
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the trace tree"
        onMouseDown={startTreeDrag}
        sx={{
          height: 6,
          cursor: "row-resize",
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderTop: "1px solid",
          borderBottom: "1px solid",
          borderColor: "divider",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Iconify
          icon="solar:menu-dots-linear"
          width={16}
          color="text.disabled"
        />
      </Box>
      {g.handoffs?.length > 0 && (
        <HandoffList
          handoffs={g.handoffs}
          onHover={setHoverStop}
          onPick={(id) => setFocusNode((cur) => (cur === id ? null : id))}
        />
      )}
      <EdgeKey edges={graph.edges} />
      <Box
        sx={{
          height: graphHeightOf(graph),
          ...handoffHighlightSx(g.handoffs || [], graph.edges, hoverStop),
        }}
      >
        <AgentGraph
          data={graph}
          isLoading={false}
          direction="TB"
          onNodeClick={(node) => {
            const id = typeof node === "string" ? node : node?.id;
            if (!id || id === "__start__" || id === "__end__") return;
            setFocusNode((cur) => (cur === id ? null : id));
          }}
        />
      </Box>
      <Divider />
      <Box sx={{ px: 2, py: 1.5 }}>
        <Details agent={agent} task={task} />
      </Box>
    </Stack>
  );
}

HandoffTimeline.propTypes = {
  agent: PropTypes.object,
  task: PropTypes.object.isRequired,
  /* The call record's participants (taskToVoiceData), lined up with the
     transcript; without it the record is built here on its own. */
  participants: PropTypes.object,
};
