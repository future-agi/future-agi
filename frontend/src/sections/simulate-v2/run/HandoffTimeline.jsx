import { Fragment, useLayoutEffect, useMemo, useRef, useState } from "react";
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

/* The path the work took, as numbers in order (1 → 2 → 3 → 1 is a
   loop); the same numbers name the graph's nodes. Who each is, on hover. */
/* The order the work moved ("Hand-off order", not "path": the Expected
   steps tab's path is the scenario's). Hovering a stop lights up its box and the arrow
   into it on the graph; clicking a number filters the tree, as a box does. */
function PathLine({ path, onHover, onPick }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      useFlexGap
      flexWrap="wrap"
      sx={{ px: 2, pt: 1.25, columnGap: 0.75, rowGap: 0.5 }}
      onMouseLeave={() => onHover(null)}
    >
      <Typography sx={{ typography: "s3", color: "text.tertiary", mr: 0.5 }}>
        Hand-off order
      </Typography>
      {path.map((stop, i) => (
        <Fragment key={i}>
          {i > 0 && (
            <Typography sx={{ typography: "s3", color: "text.disabled" }}>
              {"→"}
            </Typography>
          )}
          <Tooltip
            arrow
            title={`${stop.who.map((w) => `${w.n} · ${w.name}`).join(" and ")}${stop.together ? " — handed work at the same time" : ""}`}
          >
            <Box
              component="span"
              onMouseEnter={() => onHover(i)}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                typography: "s2",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
                borderRadius: "4px",
                bgcolor: "background.neutral",
                "&:hover": { bgcolor: "action.selected" },
              }}
            >
              {stop.who.map((w, k) => (
                <Fragment key={w.id}>
                  {k > 0 && <Box component="span">{"+"}</Box>}
                  <Box
                    component="span"
                    role="button"
                    tabIndex={0}
                    aria-label={`Show ${w.name} only`}
                    onClick={() => onPick(w.id)}
                    onKeyDown={(e) => e.key === "Enter" && onPick(w.id)}
                    sx={{ px: 0.75, cursor: "pointer" }}
                  >
                    {w.n}
                  </Box>
                </Fragment>
              ))}
            </Box>
          </Tooltip>
          {stop.repeat > 1 && (
            <Tooltip
              arrow
              title={`This back-and-forth happened ${stop.repeat} times in a row`}
            >
              <Typography
                component="span"
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {`×${stop.repeat}`}
              </Typography>
            </Tooltip>
          )}
        </Fragment>
      ))}
    </Stack>
  );
}
PathLine.propTypes = {
  path: PropTypes.array.isRequired,
  onHover: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
};

/* CSS that lights up one path stop on the graph: its boxes, and the arrows
   into them from the stop before. Edge ids follow AgentGraph's own naming. */
const stopHighlightSx = (path, edges, i) => {
  if (i == null || !path[i]) return {};
  const ids = path[i].who.map((w) => w.id);
  const prev = i > 0 ? path[i - 1].who.map((w) => w.id) : [];
  const edgeIds = edges
    .map((e, idx) =>
      prev.includes(e.source) && ids.includes(e.target)
        ? e._return
          ? `e-ret-${idx}`
          : `e-${idx}`
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
    edges.some((e) => e._return) && "Dotted: answer came back",
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
      {g.path?.length > 1 && (
        <PathLine
          path={g.path}
          onHover={setHoverStop}
          onPick={(id) => setFocusNode((cur) => (cur === id ? null : id))}
        />
      )}
      <EdgeKey edges={graph.edges} />
      <Box
        sx={{ height: 520, ...stopHighlightSx(g.path, graph.edges, hoverStop) }}
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
