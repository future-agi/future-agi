import { useMemo, useState } from "react";
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
export default function HandoffTimeline({ agent, task }) {
  const sample = contractSample();
  const partial = knob("participants") === "partial";
  const p = useMemo(() => {
    const rec = sample
      ? participantsFromTimeline(CONTRACT_SAMPLES[sample])
      : participantsOf({ agent, task });
    return rec && partial ? { ...rec, partial: PARTIAL_NOTE } : rec;
  }, [agent, task, sample, partial]);
  const g = useMemo(() => participantsGraphOf(p), [p]);
  const [focusNode, setFocusNode] = useState(null);
  const [spanId, setSpanId] = useState(null);
  if (!p || !g) return null;

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
          sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
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
        sx={{
          maxHeight: 560,
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
      <Divider />
      <Box sx={{ height: 520 }}>
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
};
