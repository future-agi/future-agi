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
  LIMITS_SOON,
  TOPOLOGY,
  participantsOf,
  workerChecksOf,
} from "../_mock/participants";
import { participantsGraphOf } from "../_mock/participantsGraph";
import { sequenceOf } from "../_mock/participantsSequence";
import { placeFailures } from "../_mock/participantsSubGoals";
import { LONG_CALL } from "../_mock/participantsLong";
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

const Quiet = ({ children }) => (
  <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
    {children}
  </Typography>
);
Quiet.propTypes = { children: PropTypes.node };

/* On top: the call in one sentence. Recorded is the normal case, so it's a
   quiet icon with the wording on hover; Not recorded is said in words. */
function Recorded({ p }) {
  const why = `${
    p.observed
      ? "Recorded in this run — every hand-off and answer is a real event."
      : "Not recorded — the app sent no worker events, so this shows how it's set up, not what happened."
  } Supported shape: ${p.topology || TOPOLOGY}.`;
  return (
    <Stack direction="row" spacing={0.75} alignItems="center">
      <Typography sx={{ typography: "s2", fontWeight: 500 }}>
        {p.summary}
      </Typography>
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
          {!p.observed && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Not recorded
            </Typography>
          )}
        </Stack>
      </Tooltip>
    </Stack>
  );
}
Recorded.propTypes = { p: PropTypes.object.isRequired };

/* Each worker's own checks, one row per worker — beside the verdict, never
   changing it. Only a problem takes colour. */
function WorkerChecks({ p }) {
  const rows = workerChecksOf(p);
  if (!rows.length) return null;
  const cols = "160px 70px 160px 1fr";
  const cell = { typography: "s3", minWidth: 0 };
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: cols,
        columnGap: 2,
        rowGap: 0.75,
      }}
    >
      {["Worker", "Retries", "Duplicate write", "What the caller was told"].map(
        (h) => (
          <Box
            key={h}
            sx={{ ...cell, fontWeight: 600, color: "text.secondary" }}
          >
            {h}
          </Box>
        ),
      )}
      {rows.map((r) => (
        <Box key={r.id} sx={{ display: "contents" }}>
          <Box sx={{ ...cell, color: "text.primary" }}>{r.name}</Box>
          <Box
            sx={{
              ...cell,
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {r.retries}
          </Box>
          <Box
            sx={{
              ...cell,
              color: r.duplicate.bad ? "error.main" : "text.secondary",
            }}
          >
            {r.duplicate.text}
          </Box>
          <Box
            sx={{
              ...cell,
              color: r.told.bad ? "error.main" : "text.secondary",
            }}
          >
            {r.told.text}
          </Box>
        </Box>
      ))}
    </Box>
  );
}
WorkerChecks.propTypes = { p: PropTypes.object };

/* Each worker's checks, what isn't recorded yet, and the call's shared
   state. The Tool / Used by list is gone: the graph draws each tool under
   the worker that called it. */
function Details({ agent, task, p }) {
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
          </Box>
          <WorkerChecks p={p} />
          <SharedState agent={agent} task={task} />
        </Stack>
      )}
    </Box>
  );
}
Details.propTypes = {
  agent: PropTypes.object,
  task: PropTypes.object,
  p: PropTypes.object,
};

/* What went wrong, one line each: where (Step 6, Reply to step 8) and what. The line
   itself opens that step — no separate "Go to" (17 Nov, "too verbose"). */
function IssueLine({ at, text, tone, title, icon, onClick }) {
  const color =
    tone === "warning"
      ? "warning.main"
      : tone === "muted"
        ? "text.secondary"
        : "error.main";
  return (
    <Box
      component={onClick ? "button" : "div"}
      type={onClick ? "button" : undefined}
      onClick={onClick}
      title={title}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        p: 0,
        border: 0,
        bgcolor: "transparent",
        textAlign: "left",
        cursor: onClick ? "pointer" : "default",
        color,
        "&:hover .issue-text": onClick ? { textDecoration: "underline" } : {},
      }}
    >
      <Iconify
        icon={icon || "solar:danger-circle-linear"}
        width={14}
        sx={{ flexShrink: 0 }}
      />
      <Typography
        component="span"
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "inherit",
          whiteSpace: "nowrap",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {at}
      </Typography>
      <Typography
        component="span"
        className="issue-text"
        sx={{
          typography: "s3",
          color: "text.primary",
          textUnderlineOffset: "2px",
        }}
      >
        {text}
      </Typography>
    </Box>
  );
}
IssueLine.propTypes = {
  at: PropTypes.string,
  text: PropTypes.string,
  tone: PropTypes.string,
  title: PropTypes.string,
  icon: PropTypes.string,
  onClick: PropTypes.func,
};

function Issues({ seq, placed, onGo }) {
  const lines = [
    ...seq.problems.map((x) => ({
      key: `p-${x.n}`,
      at: x.range ? `Steps ${x.range[0]}–${x.range[1]}` : `Step ${x.n}`,
      text: x.text.replace(/\s+(at |to )?step \d+$/i, ""),
      n: x.n,
    })),
    /* Refused by the world (Karthik, 17 Nov): amber, with the world's reason
       — its own line, never counted as a failure. */
    ...(seq.refusals || []).map((x) => ({
      key: `r-${x.group}`,
      at: x.range ? `Steps ${x.range[0]}–${x.range[1]}` : `Step ${x.n}`,
      text: `${x.tool} refused${x.times > 1 ? ` ${x.times} times` : ""}${x.reason ? ` · ${x.reason}` : ""}`,
      tone: "warning",
      icon: "solar:forbidden-circle-linear",
      n: x.n,
    })),
    ...placed.map((f) => {
      const unsure = f.status === "inconclusive";
      const critical = f.mark === "critical";
      return {
        key: f.key || f.text,
        at:
          f.n == null
            ? "No step"
            : f.row === "answer"
              ? `Reply to step ${f.n}`
              : `Step ${f.n}`,
        text: `${unsure ? "Unsure" : critical ? "Critical sub-goal failed" : "Sub-goal missed"}: ${f.text}`,
        tone: unsure ? "warning" : critical ? "error" : "muted",
        title: f.n == null ? `Not tied to a step · ${f.untied}` : undefined,
        n: f.n,
      };
    }),
  ];
  if (!lines.length) return null;
  return (
    <Stack spacing={0.25}>
      {lines.map((l) => (
        <IssueLine
          key={l.key}
          at={l.at}
          text={l.text}
          tone={l.tone}
          title={l.title}
          icon={l.icon}
          onClick={l.n != null ? () => onGo(l.n) : undefined}
        />
      ))}
    </Stack>
  );
}
Issues.propTypes = {
  seq: PropTypes.object.isRequired,
  placed: PropTypes.array.isRequired,
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
  /* ?participants=long: a 26-step call with loops (Chintan, 17 Nov). */
  const long = knob("participants") === "long";
  const p = useMemo(() => {
    const rec = sample
      ? participantsFromTimeline(CONTRACT_SAMPLES[sample])
      : long
        ? LONG_CALL
        : participants || participantsOf({ agent, task });
    return rec && partial ? { ...rec, partial: PARTIAL_NOTE } : rec;
  }, [agent, task, participants, sample, partial, long]);
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
    () => placeFailures(seq, sample || long ? [] : failures || []),
    [seq, failures, sample, long],
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
        <Recorded p={p} />
        <Issues seq={seq} placed={placed} onGo={setPicked} />
        {p.partial && <Quiet>{p.partial}</Quiet>}
      </Stack>
      {routes && !sample && !long && (
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
          hasTraces={!!p?.traces}
        />
      )}
      <Divider />
      <Box sx={{ px: 2, py: 1.5 }}>
        <Details agent={agent} task={task} p={p} />
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
