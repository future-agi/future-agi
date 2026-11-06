import { Fragment, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  FormControlLabel,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import {
  CHECKS_SOON,
  ID_NOTE,
  LIMITS_SOON,
  NOT_DECLARED,
  NOT_RECORDED,
  NOT_TIED,
  TRACES_SOON,
  callStatusOf,
  clock,
  durationLabel,
  kvOf,
  participantsOf,
  rawEventsOf,
  resultSentenceOf,
  spanLabelOf,
  tokensOf,
  workerJsonOf,
} from "../_mock/participants";
import { participantsFromTimeline } from "../_mock/workerTimeline";
import { CONTRACT_SAMPLES } from "../_mock/workerTimelineSamples";
import SharedState from "./SharedState";

/* ?contract=<name> shows one of the API's own call records instead. */
const contractSample = () => {
  if (typeof window === "undefined") return null;
  const name = new URLSearchParams(window.location.search).get("contract");
  return name && CONTRACT_SAMPLES[name] ? name : null;
};

const mono = { fontFamily: "ui-monospace, Menlo, monospace" };

const Label = ({ children }) => (
  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
    {children}
  </Typography>
);
Label.propTypes = { children: PropTypes.node };

const Json = ({ label, value }) => (
  <Box>
    <Label>{label}</Label>
    <Box
      component="pre"
      sx={{
        ...mono,
        typography: "s3",
        m: 0,
        mt: 0.25,
        p: 1,
        overflow: "auto",
        maxHeight: 160,
        borderRadius: 0.5,
        bgcolor: "background.neutral",
        color: "text.primary",
      }}
    >
      {value == null ? "No result recorded" : JSON.stringify(value, null, 2)}
    </Box>
  </Box>
);
Json.propTypes = { label: PropTypes.string, value: PropTypes.any };

/* A short label above, the words below as a quote with a thin rule. */
function Quote({ label, text }) {
  return (
    <Stack spacing={0.5}>
      <Label>{label}</Label>
      <Typography
        sx={{
          typography: "s2",
          lineHeight: 1.6,
          color: "text.primary",
          borderLeft: "2px solid",
          borderColor: "divider",
          pl: 1.5,
        }}
      >
        {text}
      </Typography>
    </Stack>
  );
}
Quote.propTypes = {
  label: PropTypes.string.isRequired,
  text: PropTypes.string,
};

/* A compact label: value list. */
function Pairs({ title, data }) {
  const rows = kvOf(data);
  return (
    <Box>
      <Label>{title}</Label>
      {rows.length ? (
        rows.map(([k, v]) => (
          <Stack key={k} direction="row" spacing={1.5} sx={{ py: 0.125 }}>
            <Typography
              sx={{
                typography: "s2",
                color: "text.tertiary",
                width: 112,
                flexShrink: 0,
              }}
            >
              {k}
            </Typography>
            <Typography
              sx={{
                typography: "s2",
                color: "text.secondary",
                minWidth: 0,
                overflowWrap: "anywhere",
              }}
            >
              {v}
            </Typography>
          </Stack>
        ))
      ) : (
        <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
          {data == null ? "No result recorded" : "Nothing"}
        </Typography>
      )}
    </Box>
  );
}
Pairs.propTypes = { title: PropTypes.string.isRequired, data: PropTypes.any };

/* ok, or the error in red; a call that never answered reads quietly. */
function Status({ call }) {
  const st = callStatusOf(call);
  return (
    <Typography
      sx={{
        typography: "s3",
        flexShrink: 0,
        color:
          st.kind === "error"
            ? "status.danger.fg"
            : st.kind === "none"
              ? "text.tertiary"
              : "text.secondary",
      }}
    >
      {st.text}
    </Typography>
  );
}
Status.propTypes = { call: PropTypes.object.isRequired };

/* Not wired yet: the tooltip says why. The span wrapper lets a disabled
   button carry it. */
/* Small, quiet actions beside a step — they shouldn't compete with it. */
const miniBtn = {
  fontSize: 11,
  lineHeight: 1,
  fontWeight: 500,
  height: 20,
  minHeight: 20,
  minWidth: 0,
  px: 0.75,
  py: 0,
  borderRadius: "5px",
  borderColor: "divider",
  color: "text.secondary",
  "& .MuiButton-startIcon": { mr: 0.5, ml: 0 },
};

function TracesButton() {
  return (
    <Tooltip title={TRACES_SOON} arrow>
      <span>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          disabled
          startIcon={<Iconify icon="solar:square-top-down-linear" width={12} />}
          sx={miniBtn}
        >
          Open in Traces
        </Button>
      </span>
    </Tooltip>
  );
}

function Details({ p, agent, task }) {
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
          {/* Plain answers to two questions: did we see this happen, and
             could a worker step outside its own tools? */}
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "140px 1fr",
              rowGap: 1,
              columnGap: 2,
            }}
          >
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              Where this comes from
            </Typography>
            <Typography sx={{ typography: "s2" }}>
              {p.observed
                ? "Recorded in this run — every hand-off and answer above is a real event."
                : "Not recorded — the app sent no worker events, so this shows how it's set up, not what happened."}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              Supported shape
            </Typography>
            <Typography sx={{ typography: "s2" }}>{p.topology}</Typography>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              Tool limits
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              {LIMITS_SOON}
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              Checks on worker events
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              {CHECKS_SOON}
            </Typography>
          </Box>
          {p.boundaries.length > 0 && (
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
                "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
              }}
            >
              <Stack
                direction="row"
                spacing={2}
                sx={{
                  px: 1.5,
                  py: 0.75,
                  typography: "s3",
                  color: "text.tertiary",
                }}
              >
                <Box sx={{ flex: 1 }}>Tool</Box>
                <Box sx={{ flex: 1 }}>Used by</Box>
              </Stack>
              {p.boundaries.map((b, i) => (
                <Stack
                  key={`${b.tool}-${i}`}
                  direction="row"
                  spacing={2}
                  sx={{ px: 1.5, py: 0.75, typography: "s2" }}
                >
                  <Box sx={{ ...mono, typography: "s3", flex: 1, minWidth: 0 }}>
                    {b.tool}
                  </Box>
                  <Box
                    sx={{
                      flex: 1,
                      color:
                        b.worker === NOT_TIED ? "text.tertiary" : "inherit",
                    }}
                  >
                    {b.worker}
                  </Box>
                </Stack>
              ))}
            </Box>
          )}
          <SharedState agent={agent} task={task} />
        </Stack>
      )}
    </Box>
  );
}
Details.propTypes = {
  p: PropTypes.object.isRequired,
  agent: PropTypes.object,
  task: PropTypes.object,
};

const cell = {
  verticalAlign: "top",
  py: 1.25,
  px: 1.5,
  height: "auto",
  typography: "s2",
  lineHeight: 1.55,
  overflowWrap: "anywhere",
};

/* Model and tokens come from the app's traces; the time is always ours. */
function Facts({ w, traces }) {
  const tokens = traces ? tokensOf(w) : null;
  const span = spanLabelOf(w);
  const items = [
    traces && w.model,
    tokens != null && `${tokens.toLocaleString("en-US")} tokens`,
    span,
  ].filter(Boolean);
  return (
    <Stack
      direction="row"
      useFlexGap
      spacing={2}
      sx={{ flexWrap: "wrap", typography: "s3", color: "text.secondary" }}
    >
      {items.map((t, i) => (
        <Box
          key={t}
          sx={{
            ...(i === 0 && traces ? mono : {}),
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {t}
        </Box>
      ))}
    </Stack>
  );
}
Facts.propTypes = { w: PropTypes.object.isRequired, traces: PropTypes.bool };

/* The worker's own steps, its sub-workers, and the engineer footer. */
function WorkerDetail({ w, events, subs = [], traces, onOpen }) {
  const { enqueueSnackbar } = useSnackbar();
  const copy = () => {
    navigator.clipboard
      ?.writeText(JSON.stringify(workerJsonOf(w), null, 2))
      .catch(() => {});
    enqueueSnackbar(`Copied ${w.name} as JSON`, { variant: "success" });
  };
  return (
    <Stack spacing={1}>
      {events.length ? (
        <Box
          sx={{
            "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {events.map((e) => (
            <EventRow
              key={e.key}
              e={{
                ...e,
                depth: e.type === "Hand-off" && e.to === w.name ? 0 : 1,
              }}
              traces={traces}
              folded
            />
          ))}
        </Box>
      ) : (
        <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
          No events from this worker.
        </Typography>
      )}
      {subs.length > 0 && (
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          useFlexGap
          sx={{ flexWrap: "wrap", pt: 0.5 }}
        >
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Handed work to
          </Typography>
          {subs.map((c) => (
            <Button
              key={c.id}
              size="small"
              variant="outlined"
              color="inherit"
              onClick={() => onOpen(c.id)}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={12} />}
              sx={miniBtn}
            >
              {c.name}
            </Button>
          ))}
        </Stack>
      )}
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="center"
        sx={{ pt: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Facts w={w} traces={traces} />
          <Tooltip arrow title={ID_NOTE}>
            <Typography
              component="span"
              sx={{ ...mono, typography: "s3", color: "text.tertiary" }}
            >
              {w.wid}
            </Typography>
          </Tooltip>
        </Box>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={copy}
          startIcon={<Iconify icon="solar:copy-linear" width={12} />}
          sx={{ ...miniBtn, flexShrink: 0 }}
        >
          Copy as JSON
        </Button>
      </Stack>
    </Stack>
  );
}
WorkerDetail.propTypes = {
  w: PropTypes.object.isRequired,
  events: PropTypes.array.isRequired,
  subs: PropTypes.array,
  traces: PropTypes.bool,
  onOpen: PropTypes.func,
};

/* Tool names as recorded; a failed one in red. */
function Tools({ calls }) {
  return (
    <Stack spacing={0.25}>
      {calls.map((c) => (
        <Typography
          key={c.id}
          sx={{
            ...mono,
            fontSize: 11,
            lineHeight: 1.7,
            wordBreak: "break-word",
            color:
              c.failed || c.blocked ? "status.danger.fg" : "text.secondary",
          }}
        >
          {c.name}
          {c.failed ? " · failed" : c.blocked ? " · blocked" : ""}
        </Typography>
      ))}
    </Stack>
  );
}
Tools.propTypes = { calls: PropTypes.array.isRequired };

/* One worker: who handed it work, the tools it called, what came back. */
function WorkerRow({ w, last, onOpen }) {
  const timeout = w.state === "timeout";
  const unseen = w.state === "unobserved";
  const edge = last ? { borderBottom: 0 } : {};
  const notes = [
    w.transfer && {
      text: `Control passed by ${w.parentName || "the coordinator"}`,
    },
    w.parentName && !w.transfer && { text: `Handed by ${w.parentName}` },
    w.parallelWith && { text: `In parallel with ${w.parallelWith}` },
    w.undeclared && { text: NOT_DECLARED, tone: "status.warning.fg" },
    unseen && { text: "Not observed" },
  ].filter(Boolean);
  return (
    <TableRow
      hover
      tabIndex={0}
      aria-label={`Open ${w.name}'s steps`}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      sx={{
        cursor: "pointer",
        height: "auto",
        "&:hover": { bgcolor: "action.hover" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: -2,
        },
      }}
    >
      {/* Names stay aligned; "Handed by" says who the work came from. */}
      <TableCell sx={{ ...cell, ...edge }}>
        <Typography
          sx={{ typography: "s2", fontWeight: 500, color: "text.primary" }}
        >
          {w.name}
        </Typography>
        {notes.map((n) => (
          <Typography
            key={n.text}
            sx={{ typography: "s3", color: n.tone || "text.tertiary" }}
          >
            {n.text}
          </Typography>
        ))}
      </TableCell>
      <TableCell sx={{ ...cell, ...edge }}>
        {unseen ? (
          <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
            {NOT_RECORDED}
          </Typography>
        ) : w.calls.length ? (
          <Tools calls={w.calls} />
        ) : (
          <Typography
            sx={{
              typography: "s2",
              color: w.task ? "text.secondary" : "text.tertiary",
            }}
          >
            {w.task || "No task or tool calls recorded"}
          </Typography>
        )}
      </TableCell>
      <TableCell
        sx={{
          ...cell,
          ...edge,
          color: timeout
            ? "status.danger.fg"
            : unseen
              ? "text.tertiary"
              : "text.secondary",
        }}
      >
        {resultSentenceOf(w)}
      </TableCell>
    </TableRow>
  );
}
WorkerRow.propTypes = {
  w: PropTypes.object.isRequired,
  last: PropTypes.bool,
  onOpen: PropTypes.func.isRequired,
};

/* A tool call no worker claims: shown, never credited to anyone. */
function UntiedRow({ c, last, label = NOT_TIED }) {
  const edge = last ? { borderBottom: 0 } : {};
  const st = callStatusOf(c);
  return (
    <TableRow sx={{ height: "auto" }}>
      <TableCell sx={{ ...cell, ...edge, color: "text.tertiary" }}>
        {label}
      </TableCell>
      <TableCell sx={{ ...cell, ...edge }}>
        <Tools calls={[c]} />
      </TableCell>
      <TableCell
        sx={{
          ...cell,
          ...edge,
          color: st.kind === "error" ? "status.danger.fg" : "text.secondary",
        }}
      >
        {st.text === "ok" ? "Returned ok." : st.text}
      </TableCell>
    </TableRow>
  );
}
UntiedRow.propTypes = {
  c: PropTypes.object.isRequired,
  last: PropTypes.bool,
  label: PropTypes.string,
};

/* One line of the raw list: time, who, what kind, then the payload. */
function EventRow({ e, folded = false, traces = true }) {
  const [open, setOpen] = useState(!folded);
  const json = e.type === "Tool call";
  const bad = e.type === "No answer";
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "44px minmax(0, 1fr)",
        columnGap: 1,
        py: 0.75,
        ml: (e.depth || 0) * 2.5,
        pl: e.depth ? 1.5 : 0,
        borderLeft: e.depth ? "1px solid" : 0,
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {clock(e.at)}
      </Typography>
      <Stack spacing={0.5} sx={{ minWidth: 0 }}>
        <Stack direction="row" spacing={1} alignItems="baseline">
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 500,
              color: e.actor === NOT_TIED ? "text.tertiary" : "text.primary",
            }}
          >
            {e.actor}
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              color: bad ? "status.danger.fg" : "text.tertiary",
            }}
          >
            {e.type}
          </Typography>
          {e.to && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`→ ${e.to}`}
            </Typography>
          )}
        </Stack>
        {json ? (
          <Stack spacing={0.75}>
            <Stack
              direction="row"
              alignItems="center"
              useFlexGap
              sx={{ flexWrap: "wrap", columnGap: 1, rowGap: 0.5 }}
            >
              <Typography
                sx={{
                  ...mono,
                  typography: "s3",
                  flexBasis: "100%",
                  minWidth: 0,
                  overflowWrap: "anywhere",
                  color:
                    e.status.kind === "error"
                      ? "status.danger.fg"
                      : "text.primary",
                }}
              >
                {e.tool}
              </Typography>
              {e.duration != null && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {durationLabel(e.duration)}
                </Typography>
              )}
              <Typography
                sx={{
                  typography: "s3",
                  color:
                    e.status.kind === "error"
                      ? "status.danger.fg"
                      : "text.secondary",
                }}
              >
                {e.status.text}
              </Typography>
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                aria-expanded={open}
                onClick={() => setOpen((v) => !v)}
                startIcon={<Iconify icon="solar:code-linear" width={12} />}
                sx={miniBtn}
              >
                {open ? "Hide JSON" : "Show JSON"}
              </Button>
              {/* Only an app that sends traces has a span to open. */}
              {traces && <TracesButton />}
            </Stack>
            {open && (
              <Stack spacing={0.75}>
                <Json label="Input" value={e.input} />
                <Json label="Output" value={e.output} />
              </Stack>
            )}
          </Stack>
        ) : (
          e.text != null && (
            <Box
              sx={{
                p: 1,
                borderRadius: "6px",
                bgcolor: "background.neutral",
                typography: "s2",
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                color: bad ? "status.danger.fg" : "text.primary",
              }}
            >
              {e.text}
            </Box>
          )
        )}
      </Stack>
    </Box>
  );
}
EventRow.propTypes = {
  e: PropTypes.object.isRequired,
  folded: PropTypes.bool,
  traces: PropTypes.bool,
};

const TurnLabel = ({ t }) => (
  <Typography
    sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}
  >
    {`Turn ${t.n}`}
    <Box component="span" sx={{ color: "text.tertiary", fontWeight: 400 }}>
      {` · ${clock(t.at)}`}
    </Box>
  </Typography>
);
TurnLabel.propTypes = { t: PropTypes.object.isRequired };

/* Every event in time order, turn by turn, for debugging. */
function RawEvents({ p }) {
  const events = rawEventsOf(p);
  if (!events.length)
    return (
      <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
        No events recorded — the app sent no worker events.
      </Typography>
    );
  const multi = p.turns.length > 1;
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        px: 1.5,
        py: 0.5,
        "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      {events.map((e, i) => (
        <Fragment key={e.key}>
          {multi && e.turn !== events[i - 1]?.turn && (
            <Box sx={{ py: 0.75 }}>
              <TurnLabel t={p.turns.find((t) => t.n === e.turn)} />
            </Box>
          )}
          <EventRow e={e} traces={p.traces} />
        </Fragment>
      ))}
    </Box>
  );
}
RawEvents.propTypes = { p: PropTypes.object.isRequired };

const head = {
  typography: "s3",
  color: "text.tertiary",
  fontWeight: 400,
  py: 0.75,
  px: 1.5,
  height: "auto",
};

const Quiet = ({ children }) => (
  <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
    {children}
  </Typography>
);
Quiet.propTypes = { children: PropTypes.node };

/* One turn: what the caller said, the workers it set off, the reply. */
function Turn({ t, p, multi, last, onOpen }) {
  const rows = [
    ...t.workers.map((w) => ({ key: w.id, w })),
    ...(t.untied || []).map((c) => ({ key: c.id, c })),
    ...(t.own || []).map((c) => ({
      key: c.id,
      c,
      label: `${p.coordinator.name} itself`,
    })),
  ];
  return (
    <Stack spacing={1.5}>
      {multi && <TurnLabel t={t} />}
      {t.request ? (
        <Quote label="The caller asked" text={t.request} />
      ) : (
        <Quiet>This turn started without a caller message.</Quiet>
      )}
      {rows.length > 0 ? (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            overflow: "hidden",
          }}
        >
          <Table size="small" sx={{ tableLayout: "fixed", width: "100%" }}>
            <TableHead>
              <TableRow sx={{ height: "auto" }}>
                <TableCell sx={{ ...head, width: "28%" }}>Worker</TableCell>
                <TableCell sx={{ ...head, width: "36%" }}>
                  What it did
                </TableCell>
                <TableCell sx={{ ...head, width: "36%" }}>Result</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r, i) =>
                r.w ? (
                  <WorkerRow
                    key={r.key}
                    w={r.w}
                    last={i === rows.length - 1}
                    onOpen={() => onOpen(r.w.id)}
                  />
                ) : (
                  <UntiedRow
                    key={r.key}
                    c={r.c}
                    label={r.label}
                    last={i === rows.length - 1}
                  />
                ),
              )}
            </TableBody>
          </Table>
        </Box>
      ) : (
        <Quiet>{`${p.coordinator.name} handled this turn itself.`}</Quiet>
      )}
      {/* multiagent §3: after a worker gives no answer, what the coordinator
          did next — from the recorded events, never an invented return. */}
      {(() => {
        const i = t.workers.findIndex((w) => w.state === "timeout");
        if (i < 0 || !p.observed) return null;
        const w = t.workers[i];
        const nextW = t.workers[i + 1];
        return (
          <Quiet>
            {`After no answer from ${w.name}, ${p.coordinator.name} `}
            {nextW
              ? `handed the next task to ${nextW.name}.`
              : t.reply
                ? `replied without ${w.name}'s answer.`
                : "did nothing more before the call ended."}
          </Quiet>
        );
      })()}
      {t.reply ? (
        <Quote label={`${p.coordinator.name} replied`} text={t.reply} />
      ) : (
        p.observed && (
          <Quiet>
            {last && p.outcome === "timeout"
              ? "No reply — the call ended first."
              : "This turn ended without a reply."}
          </Quiet>
        )
      )}
    </Stack>
  );
}
Turn.propTypes = {
  t: PropTypes.object.isRequired,
  p: PropTypes.object.isRequired,
  multi: PropTypes.bool,
  last: PropTypes.bool,
  onOpen: PropTypes.func.isRequired,
};

/**
 * A multi-agent trial's Participants: a summary sentence, then the call turn
 * by turn — what the caller asked, a row per worker (who handed it work, the
 * tools it called, what came back) that opens to its own events, and what the
 * front desk replied. Unobserved workers stay as declared. Evidence, the tool
 * table and shared state are folded under Details.
 */
export default function HandoffTimeline({ agent, task }) {
  const [rawOn, setRawOn] = useState(false);
  /* The workers opened, in the order they were opened: back returns to the
     one before (or the table), wherever the reader came from. */
  const [trail, setTrail] = useState([]);
  const focusId = trail.at(-1) ?? null;
  const open = (id) => setTrail((t) => [...t, id]);
  const back = () => setTrail((t) => t.slice(0, -1));
  const sample = contractSample();
  const p = sample
    ? participantsFromTimeline(CONTRACT_SAMPLES[sample])
    : participantsOf({ agent, task });
  if (!p) return null;
  const everyone = [...p.workers, ...p.undeclared];
  const focus = everyone.find((w) => w.id === focusId) || null;
  /* A worker's own events, with the hand-offs it made to its sub-workers. */
  const stepsOf = (w) =>
    rawEventsOf(p)
      .filter(
        (e) =>
          e.key === `handoff-${w.id}` ||
          e.key === `end-${w.id}` ||
          w.calls.some((c) => c.id === e.key) ||
          everyone.some(
            (x) => x.parentId === w.id && e.key === `handoff-${x.id}`,
          ),
      )
      .sort((a, b) => a.at - b.at);
  if (focus) {
    const before = everyone.find((w) => w.id === trail.at(-2));
    return (
      <Stack spacing={2} sx={{ p: 2 }}>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={back}
            startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
          >
            {before ? before.name : "All workers"}
          </Button>
          <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
            {focus.name}
          </Typography>
        </Stack>
        <WorkerDetail
          w={focus}
          events={stepsOf(focus)}
          subs={everyone.filter((w) => w.parentId === focus.id)}
          traces={p.traces}
          onOpen={open}
        />
      </Stack>
    );
  }
  const multi = p.turns.length > 1;
  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      {sample && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`API record · "${sample}" sample from the platform contract`}
        </Typography>
      )}
      <Stack direction="row" spacing={2} alignItems="flex-start">
        <Typography
          sx={{ typography: "s2", fontWeight: 600, lineHeight: 1.6, flex: 1 }}
        >
          {p.summary}
        </Typography>
        <FormControlLabel
          sx={{ m: 0, flexShrink: 0 }}
          control={
            <Switch
              size="small"
              checked={rawOn}
              onChange={(e) => setRawOn(e.target.checked)}
            />
          }
          label={
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              Raw events
            </Typography>
          }
        />
      </Stack>
      {rawOn ? (
        <RawEvents p={p} />
      ) : (
        <Stack
          spacing={2}
          sx={
            multi
              ? {
                  "& > * + *": {
                    pt: 2,
                    borderTop: "1px solid",
                    borderColor: "divider",
                  },
                }
              : undefined
          }
        >
          {p.turns.map((t, i) => (
            <Turn
              key={t.n}
              t={t}
              p={p}
              multi={multi}
              last={i === p.turns.length - 1}
              onOpen={open}
            />
          ))}
        </Stack>
      )}
      {p.notRun.length > 0 && !rawOn && (
        <Quiet>{`Declared, didn't run: ${p.notRun.join(", ")}.`}</Quiet>
      )}
      {p.notObserved?.length > 0 && !rawOn && (
        <Quiet>{`Declared, sent no events: ${p.notObserved.join(", ")}.`}</Quiet>
      )}
      <Details p={p} agent={agent} task={task} />
    </Stack>
  );
}

HandoffTimeline.propTypes = {
  agent: PropTypes.object,
  task: PropTypes.object.isRequired,
};
