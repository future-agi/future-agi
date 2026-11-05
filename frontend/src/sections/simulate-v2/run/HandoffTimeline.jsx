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
  ID_NOTE,
  NOT_RECORDED,
  TRACES_SOON,
  callStatusOf,
  clock,
  didLineOf,
  durationLabel,
  kvOf,
  participantsOf,
  rawEventsOf,
  resultSentenceOf,
  spanLabelOf,
  tokensOf,
  workerJsonOf,
} from "../_mock/participants";
import SharedState from "./SharedState";

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
              Tool limits
            </Typography>
            <Typography sx={{ typography: "s2" }}>
              {p.enforced
                ? "Enforced — each worker can only use its own tools."
                : "Not enforced — the app lists which tools each worker uses, but any worker could call any of them."}
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
                {p.enforced && (
                  <Box sx={{ width: 72, textAlign: "right" }}>Limit</Box>
                )}
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
                  <Box sx={{ flex: 1 }}>{b.worker}</Box>
                  {p.enforced && (
                    <Box
                      sx={{
                        width: 72,
                        textAlign: "right",
                        color:
                          b.boundary === "Blocked"
                            ? "status.danger.fg"
                            : "text.secondary",
                      }}
                    >
                      {b.boundary}
                    </Box>
                  )}
                </Stack>
              ))}
            </Box>
          )}
          {p.checks.length > 0 && (
            <Box>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Checks that need worker events
              </Typography>
              {p.checks.map((c) => (
                <Stack
                  key={c.label}
                  direction="row"
                  sx={{ typography: "s3", py: 0.25 }}
                >
                  <Box sx={{ width: 200, color: "text.secondary" }}>
                    {c.label}
                  </Box>
                  <Box
                    sx={{
                      color:
                        c.value === "Unmeasured"
                          ? "text.tertiary"
                          : "text.primary",
                    }}
                  >
                    {c.value}
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

/* Model, tokens and time on one line. */
function Facts({ w }) {
  const tokens = tokensOf(w);
  const span = spanLabelOf(w);
  const items = [
    w.model || "Model not set",
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
            ...(i === 0 ? mono : {}),
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {t}
        </Box>
      ))}
    </Stack>
  );
}
Facts.propTypes = { w: PropTypes.object.isRequired };

/* The task, engineer detail and tool calls under an opened worker. */
function WorkerDetail({ w, events }) {
  const { enqueueSnackbar } = useSnackbar();
  const copy = () => {
    navigator.clipboard
      ?.writeText(JSON.stringify(workerJsonOf(w), null, 2))
      .catch(() => {});
    enqueueSnackbar(`Copied ${w.name} as JSON`, { variant: "success" });
  };
  return (
    <Stack spacing={1}>
      {/* The worker's own steps, read top to bottom: the hand-off it got,
          each tool call, then its answer — the Raw events layout. */}
      {events.length ? (
        <Box
          sx={{
            "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {events.map((e) => (
            <EventRow
              key={e.key}
              e={{ ...e, depth: e.type === "Hand-off" ? 0 : 1 }}
              folded
            />
          ))}
        </Box>
      ) : (
        <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
          No events from this worker.
        </Typography>
      )}
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="center"
        sx={{ pt: 1, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Facts w={w} />
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
};

/* One worker: name, what it did, what came back. The whole row opens. */
function WorkerRow({ w, last, onOpen }) {
  const toggle = onOpen;
  const timeout = w.state === "timeout";
  const unseen = w.state === "unobserved";
  const edge = last ? { borderBottom: 0 } : {};
  return (
    <Fragment>
      <TableRow
        hover
        tabIndex={0}
        aria-label={`Open ${w.name}'s steps`}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
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
        <TableCell sx={{ ...cell, ...edge }}>
          <Typography
            sx={{ typography: "s2", fontWeight: 500, color: "text.primary" }}
          >
            {w.name}
          </Typography>
          {(timeout || unseen) && (
            <Typography
              sx={{
                typography: "s3",
                color: timeout ? "status.danger.fg" : "text.tertiary",
              }}
            >
              {timeout ? `Timed out at ${clock(w.endedAt)}` : "Not observed"}
            </Typography>
          )}
        </TableCell>
        <TableCell
          sx={{
            ...cell,
            ...edge,
            color: unseen ? "text.tertiary" : "text.secondary",
          }}
        >
          {unseen ? NOT_RECORDED : didLineOf(w)}
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
    </Fragment>
  );
}
WorkerRow.propTypes = {
  w: PropTypes.object.isRequired,
  last: PropTypes.bool,
  onOpen: PropTypes.func.isRequired,
};

/* One line of the raw list: time, who, what kind, then the payload. */
function EventRow({ e, folded = false }) {
  const [open, setOpen] = useState(!folded);
  const json = e.type === "Tool call";
  const bad = e.type === "Timed out";
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "44px minmax(0, 1fr)",
        columnGap: 1,
        py: 0.75,
        ml: e.depth ? 2.5 : 0,
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
          <Typography sx={{ typography: "s2", fontWeight: 500 }}>
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
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {durationLabel(e.duration)}
              </Typography>
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
              <TracesButton />
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
EventRow.propTypes = { e: PropTypes.object.isRequired, folded: PropTypes.bool };

/* Every event in time order, for debugging. */
function RawEvents({ p }) {
  const events = rawEventsOf(p);
  if (!events.length)
    return (
      <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
        No events recorded — the app sent no worker events.
      </Typography>
    );
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
      {events.map((e) => (
        <EventRow key={e.key} e={e} />
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

/**
 * A multi-agent trial's Participants: a summary sentence, what the caller
 * asked, a table with a row per worker (what it did, the result) that opens to
 * the task and tool calls, then what the front desk replied. Unobserved
 * workers stay as declared. Evidence, tool boundaries and shared state are
 * folded under Details.
 */
export default function HandoffTimeline({ agent, task }) {
  const [rawOn, setRawOn] = useState(false);
  /* The worker being read, opened from its table row; the table stays the
     overview and never grows. */
  const [focusId, setFocusId] = useState(null);
  const p = participantsOf({ agent, task });
  if (!p) return null;
  const focus = p.workers.find((w) => w.id === focusId) || null;
  const stepsOf = (w) =>
    rawEventsOf(p).filter(
      (e) =>
        e.key === `handoff-${w.id}` ||
        e.key === `end-${w.id}` ||
        w.calls.some((c) => c.id === e.key),
    );
  if (focus)
    return (
      <Stack spacing={2} sx={{ p: 2 }}>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => setFocusId(null)}
            startIcon={<Iconify icon="solar:arrow-left-linear" width={14} />}
          >
            All workers
          </Button>
          <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
            {focus.name}
          </Typography>
        </Stack>
        <WorkerDetail w={focus} events={stepsOf(focus)} />
      </Stack>
    );
  return (
    <Stack spacing={2} sx={{ p: 2 }}>
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
        <Fragment>
          <Quote label="The caller asked" text={p.request} />
          {p.workers.length > 0 && (
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
                    <TableCell sx={{ ...head, width: "32%" }}>
                      What it did
                    </TableCell>
                    <TableCell sx={{ ...head, width: "40%" }}>Result</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {p.workers.map((w, i) => (
                    <WorkerRow
                      key={w.id}
                      w={w}
                      last={i === p.workers.length - 1}
                      onOpen={() => setFocusId(w.id)}
                    />
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
          {p.reply && (
            <Quote label={`${p.coordinator.name} replied`} text={p.reply} />
          )}
        </Fragment>
      )}
      <Details p={p} agent={agent} task={task} />
    </Stack>
  );
}

HandoffTimeline.propTypes = {
  agent: PropTypes.object,
  task: PropTypes.object.isRequired,
};
