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
import SpanDetailPane from "src/components/traceDetail/SpanDetailPane";
import {
  CHECKS_SOON,
  LIMITS_SOON,
  TOPOLOGY,
  TRACES_SOON,
  participantsOf,
} from "../_mock/participants";
import { participantsGraphOf } from "../_mock/participantsGraph";
import { sequenceOf } from "../_mock/participantsSequence";
import { placeFailures } from "../_mock/participantsSubGoals";
import { routesOf } from "../_mock/participantsTrials";
import TrialRoutes from "./TrialRoutes";
import CompareSequence from "./CompareSequence";
import { participantsFromTimeline } from "../_mock/workerTimeline";
import { CONTRACT_SAMPLES } from "../_mock/workerTimelineSamples";
import SharedState from "./SharedState";
import SequenceDiagram from "./SequenceDiagram";

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
function Recorded({ p, children }) {
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
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="center"
        useFlexGap
        flexWrap="wrap"
      >
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
        {children}
      </Stack>
    </Stack>
  );
}
Recorded.propTypes = {
  p: PropTypes.object.isRequired,
  children: PropTypes.node,
};

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

/* What went wrong, on top, each with a jump to its step (Datadog-style
   error flags on the run itself). */
const goLinkSx = {
  typography: "s3",
  fontWeight: 500,
  color: "text.primary",
  p: 0,
  border: 0,
  bgcolor: "transparent",
  cursor: "pointer",
  whiteSpace: "nowrap",
  textDecoration: "underline",
  textUnderlineOffset: "2px",
  textDecorationColor: (t) => t.palette.divider,
  "&:hover": { textDecorationColor: "currentColor" },
};

/* Each failed or unsure sub-goal, with the participant and step its
   evidence points to — or "not tied to a step" and why (plan MA-2). */
function SubGoalLines({ placed, nameOf, onGo }) {
  if (!placed.length) return null;
  return (
    <Stack spacing={0.25}>
      {placed.map((f) => {
        const unsure = f.status === "inconclusive";
        const kind = `${unsure ? "Unsure" : f.mark === "critical" ? "Failed" : "Missed"} ${f.mark === "critical" ? "critical" : "supporting"} sub-goal`;
        const where =
          f.n == null
            ? `not tied to a step · ${f.untied.toLowerCase()}`
            : f.row === "answer"
              ? `${nameOf(f.lane)}, in its reply ↩ ${f.n}`
              : `${nameOf(f.lane)}, at step ${f.n}`;
        return (
          <Stack
            key={f.key || f.text}
            direction="row"
            spacing={0.75}
            alignItems="center"
            useFlexGap
            flexWrap="wrap"
          >
            <Iconify
              icon="solar:close-circle-linear"
              width={14}
              sx={{
                color: unsure
                  ? "warning.main"
                  : f.mark === "critical"
                    ? "error.main"
                    : "text.tertiary",
              }}
            />
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 500,
                color: unsure
                  ? "warning.main"
                  : f.mark === "critical"
                    ? "error.main"
                    : "text.secondary",
              }}
            >
              {`${kind}: ${f.text}`}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`· ${where}`}
            </Typography>
            {f.n != null && (
              <Typography
                component="button"
                type="button"
                onClick={() => onGo(f.n)}
                sx={goLinkSx}
              >
                {f.row === "answer" ? `Go to ↩ ${f.n}` : `Go to step ${f.n}`}
              </Typography>
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}
SubGoalLines.propTypes = {
  placed: PropTypes.array.isRequired,
  nameOf: PropTypes.func.isRequired,
  onGo: PropTypes.func.isRequired,
};

function Problems({ seq, onGo }) {
  if (!seq.problems.length) return null;
  const many = seq.problems.length > 1;
  return (
    <Stack
      direction="row"
      spacing={0.75}
      alignItems="center"
      useFlexGap
      flexWrap="wrap"
    >
      <Iconify
        icon="solar:danger-circle-linear"
        width={14}
        sx={{ color: "error.main" }}
      />
      <Typography
        sx={{ typography: "s3", color: "error.main", fontWeight: 500 }}
      >
        {`${seq.problems.length} ${many ? "problems" : "problem"}: ${seq.problems.map((x) => x.text).join("; ")}`}
      </Typography>
      {seq.problems.map((x) => (
        <Typography
          key={x.n}
          component="button"
          type="button"
          onClick={() => onGo(x.n)}
          sx={goLinkSx}
        >
          {`Go to step ${x.n}`}
        </Typography>
      ))}
    </Stack>
  );
}
Problems.propTypes = {
  seq: PropTypes.object.isRequired,
  onGo: PropTypes.func.isRequired,
};

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
export default function HandoffTimeline({
  agent,
  task,
  participants,
  initialStep = null,
  failures,
  linkOf,
  trials,
  trialIdx = 0,
  onPickTrial,
  onShowInTranscript,
}) {
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
  const seq = useMemo(
    () => sequenceOf(p, { callerName: task?.persona?.name || null }),
    [p, task?.persona?.name],
  );
  /* Failed sub-goals placed on the step their evidence points to. */
  const placed = useMemo(
    () => placeFailures(seq, sample ? [] : failures || []),
    [seq, failures, sample],
  );
  /* Every trial of this scenario (proposal 8): its sequence and failures. */
  const trialSeqs = useMemo(
    () =>
      (trials || []).map((t) => {
        const sq = sequenceOf(t.participants, {
          callerName: task?.persona?.name || null,
        });
        return {
          idx: t.idx,
          status: t.status,
          seq: sq,
          placed: placeFailures(sq, t.failures || []),
        };
      }),
    [trials, task?.persona?.name],
  );
  const [showTools, setShowTools] = useState(false);
  const [compareWith, setCompareWith] = useState(null);
  const routes = useMemo(
    () =>
      trialSeqs.length > 1 && trialSeqs.every((t) => t.seq)
        ? routesOf(trialSeqs, { showTools })
        : null,
    [trialSeqs, showTools],
  );
  const [spanId, setSpanId] = useState(null);
  /* Opens on the failed critical sub-goal's step, else the first problem;
     undefined = not picked yet. */
  const [picked, setPicked] = useState(initialStep ?? undefined);
  if (!p || !g || !seq) return null;
  const step =
    picked === undefined
      ? placed.find((f) => f.n != null && f.mark === "critical")?.n ??
        seq.problems[0]?.n ??
        null
      : picked;

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
      <Stack spacing={0.5} sx={{ px: 2, pt: 1.25, pb: 1 }}>
        {sample && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`API record · "${sample}" sample from the platform contract`}
          </Typography>
        )}
        <Recorded p={p}>
          <Problems seq={seq} onGo={setPicked} />
        </Recorded>
        <SubGoalLines placed={placed} nameOf={seq.nameOf} onGo={setPicked} />
        {p.partial && <Quiet>{p.partial}</Quiet>}
      </Stack>
      {routes && !sample && (
        <TrialRoutes
          routes={routes}
          current={trialIdx}
          showTools={showTools}
          onToggleTools={() => setShowTools((v) => !v)}
          onOpen={(idx, n) => {
            setCompareWith(null);
            if (idx !== trialIdx) onPickTrial?.(idx);
            setPicked(n ?? undefined);
          }}
          onCompare={setCompareWith}
        />
      )}
      {compareWith != null &&
      compareWith !== trialIdx &&
      trialSeqs[compareWith]?.seq ? (
        <CompareSequence
          seqA={seq}
          seqB={trialSeqs[compareWith].seq}
          a={trialIdx}
          b={compareWith}
          onStop={() => setCompareWith(null)}
          onPick={(n) => {
            setCompareWith(null);
            setPicked(n);
          }}
        />
      ) : (
        <SequenceDiagram
          seq={seq}
          failures={placed}
          selected={step}
          onSelect={setPicked}
          onOpenTrace={(id) => id && findEntry(g.spans, id) && setSpanId(id)}
          onShowInTranscript={onShowInTranscript}
          linkOf={linkOf}
        />
      )}
      <Divider />
      <Box sx={{ px: 2, py: 1.5 }}>
        <Details agent={agent} task={task} />
      </Box>
    </Stack>
  );
}

HandoffTimeline.propTypes = {
  /* The trial's failed sub-goals with the judge's evidence
     (subGoalFailuresOf); each is put on the step it points to. */
  failures: PropTypes.array,
  /* A step to open on (a shared link to a step). */
  initialStep: PropTypes.number,
  /* n → a link that opens the run on this call, trial and step. */
  linkOf: PropTypes.func,
  /* seconds → the drawer shows the transcript and recording at that moment
     (voice drawer only). */
  onShowInTranscript: PropTypes.func,
  /* Every trial of this scenario: { idx, status, participants, failures }
     (proposal 8: routes strip and side-by-side compare). */
  trials: PropTypes.array,
  trialIdx: PropTypes.number,
  onPickTrial: PropTypes.func,
  agent: PropTypes.object,
  task: PropTypes.object.isRequired,
  /* The call record's participants (taskToVoiceData), lined up with the
     transcript; without it the record is built here on its own. */
  participants: PropTypes.object,
};
