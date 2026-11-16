import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControlLabel,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import { useCallWorkers } from "src/api/simulate-environments/runDetail";
import Markdown from "src/sections/simulate/environments/buildEnvironment/console/Markdown";
import { MONO_FONT as MONO } from "src/sections/simulate/environments/workspace/contract/contract.constants";
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";
import {
  asMarkdown,
  callEvents,
  handOffEvents,
  handOffsOf,
  isLong,
  noMessages,
  seconds,
  stepsByKey,
  turnLabel,
} from "./utils/callWorkersUtils";
import { formatClock } from "src/components/VoiceDetailDrawerV2/transcriptUtils";

// Past this a heading may hit its three-line clamp, so the whole of it shows on hover.
const LONG_TITLE = 80;
const NO_IMAGES = ["img"];
// Indent levels in the table's narrow Worker column; deeper rows stay at the last one.
const MAX_ROW_INDENT = 3;
// A call with more tool calls than this opens with their JSON closed, so a long record stays quick to draw.
const MAX_OPEN_CALLS = 20;
const NO_TRACES = [];

const MOTION_MS = 180;
const NO_MOTION_SX = {
  "@media (prefers-reduced-motion: reduce)": { transition: "none !important" },
};

const JSON_BUTTON_SX = {
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

// Time into the call ("0:05"), as the transcript shows it, with the exact clock time on hover.
function Stamp({ at, start }) {
  const moment = seconds(at);
  if (moment === null) return null;
  const from = seconds(start);
  const label =
    from === null
      ? new Date(moment * 1000).toISOString().slice(11, 19)
      : formatClock(Math.max(0, moment - from));
  return (
    <CustomTooltip
      show
      size="small"
      arrow
      title={`${new Date(moment * 1000).toISOString().replace("T", " ").slice(0, 23)} UTC`}
    >
      <Typography
        component="span"
        sx={{
          typography: "s3",
          fontFamily: MONO,
          color: "text.subtitle",
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
    </CustomTooltip>
  );
}
Stamp.propTypes = { at: PropTypes.string, start: PropTypes.string };

// Height animates in CSS alone, by the size of a grid row, so opening costs no measuring or per-frame JS.
// Content mounts on first open and stays, so closing keeps its state; `visibility` takes it out of tab order.
function Reveal({ open, collapsedSize = "0px", children, sx }) {
  const seen = useRef(open);
  if (open) seen.current = true;
  const peeks = collapsedSize !== "0px";
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateRows: `minmax(${collapsedSize}, ${open ? "1fr" : "0fr"})`,
        transition: `grid-template-rows ${MOTION_MS}ms ease`,
        ...NO_MOTION_SX,
        ...sx,
      }}
    >
      <Box
        sx={{
          minWidth: 0,
          minHeight: 0,
          overflow: "hidden",
          ...(!peeks && {
            visibility: open ? "visible" : "hidden",
            transition: `visibility 0s linear ${open ? 0 : MOTION_MS}ms`,
            ...NO_MOTION_SX,
          }),
        }}
      >
        {peeks || seen.current ? children : null}
      </Box>
    </Box>
  );
}
Reveal.propTypes = {
  open: PropTypes.bool,
  collapsedSize: PropTypes.string,
  children: PropTypes.node,
  sx: PropTypes.object,
};

function Line({ title, at, start, color, children }) {
  return (
    <Stack direction="row" spacing={1} alignItems="baseline">
      {/* A long task stays a heading: three lines, the whole of it on hover. */}
      <CustomTooltip
        show={title.length > LONG_TITLE}
        size="small"
        arrow
        title={title}
      >
        <Typography
          sx={{
            typography: "s1",
            fontWeight: 600,
            color,
            flex: 1,
            minWidth: 0,
            wordBreak: "break-word",
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {title}
        </Typography>
      </CustomTooltip>
      {children}
      {/* Last in the row, so every time ends at the same right edge. */}
      <Stamp at={at} start={start} />
    </Stack>
  );
}
Line.propTypes = {
  title: PropTypes.string.isRequired,
  at: PropTypes.string,
  start: PropTypes.string,
  color: PropTypes.string,
  children: PropTypes.node,
};

function Payload({ label, value, omitted }) {
  return (
    <Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      {value === null || value === undefined ? (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {omitted ? C.payloadOmitted : C.noPayload}
        </Typography>
      ) : typeof value === "object" ? (
        <CustomJsonViewer object={value} />
      ) : (
        <Typography
          sx={{
            typography: "s3",
            fontFamily: MONO,
            color: "text.secondary",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {String(value)}
        </Typography>
      )}
    </Box>
  );
}
Payload.propTypes = {
  label: PropTypes.string,
  value: PropTypes.any,
  omitted: PropTypes.bool,
};

function Answer({ shown, empty }) {
  const [whole, setWhole] = useState(false);
  if (!shown)
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {empty}
      </Typography>
    );
  const text = asMarkdown(shown);
  const long = isLong(text);
  const body = (
    <Box
      data-testid="answer"
      data-clamped={long && !whole}
      sx={{
        typography: "s1",
        wordBreak: "break-word",
        // Headings inside an answer read as bold lines, not page titles.
        "& h1, & h2, & h3, & h4, & h5, & h6": {
          typography: "s1",
          fontWeight: 600,
          my: 0.25,
        },
      }}
    >
      {/* Worker text isn't ours: an image in it would load from any host. */}
      <Markdown text={text} dim variant="s1" disallowedElements={NO_IMAGES} />
    </Box>
  );
  return (
    <Box>
      {long ? (
        <Reveal open={whole} collapsedSize="9em" sx={{ typography: "s1" }}>
          {body}
        </Reveal>
      ) : (
        body
      )}
      {long ? (
        <Typography
          component="button"
          onClick={() => setWhole((one) => !one)}
          sx={{
            typography: "s3",
            color: "primary.main",
            border: 0,
            bgcolor: "transparent",
            cursor: "pointer",
            p: 0,
          }}
        >
          {whole ? C.showLess : C.showMore}
        </Typography>
      ) : null}
    </Box>
  );
}
Answer.propTypes = {
  shown: PropTypes.shape({ format: PropTypes.string, text: PropTypes.string }),
  empty: PropTypes.string,
};

// A turn of the caller or the coordinator, styled as the transcript styles it.
function Turn({ title, turn, agent, start, empty }) {
  return (
    <Box
      sx={{
        borderLeft: "2px solid",
        borderColor: (t) =>
          agent ? t.palette.primary.main : t.palette.text.disabled,
        bgcolor: (t) =>
          agent
            ? alpha(
                t.palette.primary.main,
                t.palette.mode === "dark" ? 0.06 : 0.04,
              )
            : "background.neutral",
        px: 1.75,
        py: 1,
      }}
    >
      <Line title={title} at={turn?.at} start={start} />
      <Answer shown={turn?.shown} empty={empty} />
    </Box>
  );
}
Turn.propTypes = {
  title: PropTypes.string,
  turn: PropTypes.object,
  agent: PropTypes.bool,
  start: PropTypes.string,
  empty: PropTypes.string,
};

// Where the record does not say which hand-off a step belongs to: never guessed from timing.
function UnknownParent({ sx }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      label={C.parentUnknown}
      data-testid="parent-unknown"
      sx={{ flexShrink: 0, ...sx }}
    />
  );
}
UnknownParent.propTypes = { sx: PropTypes.object };

// One line of the raw list: the time, who, what kind of step, then its text or tool call.
function EventRow({ event, start, openJson, onOpenTrace, traceIds }) {
  const [open, setOpen] = useState(openJson);
  const call = event.call;
  const failed = call?.status === "error";
  return (
    <Box
      data-testid="raw-event"
      data-type={event.type}
      sx={{
        display: "grid",
        gridTemplateColumns: "44px minmax(0, 1fr)",
        columnGap: 1,
        py: 0.75,
        ml: Math.min(event.depth, MAX_ROW_INDENT) * 2.5,
        pl: event.depth ? 1.5 : 0,
        borderLeft: event.depth ? "1px solid" : 0,
        borderColor: "divider",
        // The browser skips laying out steps off screen, which keeps a long record quick to open and scroll.
        contentVisibility: "auto",
        containIntrinsicSize: "auto 72px",
      }}
    >
      <Stamp at={event.at} start={start} />
      <Stack spacing={0.5} sx={{ minWidth: 0, gridColumn: 2 }}>
        <Stack
          direction="row"
          useFlexGap
          alignItems="baseline"
          sx={{ flexWrap: "wrap", columnGap: 1 }}
        >
          <Typography sx={{ typography: "s1", fontWeight: 500 }}>
            {event.actor}
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              color: event.bad ? "error.main" : "text.subtitle",
            }}
          >
            {event.type}
          </Typography>
          {event.to ? (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {`→ ${event.to}`}
            </Typography>
          ) : null}
          {event.parallel ? (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {C.inParallel}
            </Typography>
          ) : null}
          {event.unknown ? <UnknownParent /> : null}
          {/* Traces opens only a trace it lists. */}
          {onOpenTrace && traceIds.includes(event.traceId) ? (
            <Button
              size="small"
              variant="text"
              onClick={() => onOpenTrace(event.traceId, event.spanId)}
              sx={{ ...JSON_BUTTON_SX, ml: "auto" }}
            >
              {C.openInTraces}
            </Button>
          ) : null}
        </Stack>
        {call ? (
          <Stack spacing={0.75}>
            <Stack
              direction="row"
              useFlexGap
              alignItems="center"
              sx={{ flexWrap: "wrap", columnGap: 1, rowGap: 0.5 }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  flexBasis: "100%",
                  minWidth: 0,
                  overflowWrap: "anywhere",
                  color: failed ? "error.main" : "text.primary",
                }}
              >
                {call.tool}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: failed ? "error.main" : "text.secondary",
                }}
              >
                {failed ? C.failed : C.ok}
              </Typography>
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                aria-expanded={open}
                onClick={() => setOpen((one) => !one)}
                startIcon={<Iconify icon="solar:code-linear" width={12} />}
                sx={JSON_BUTTON_SX}
              >
                {open ? C.hideJson : C.showJson}
              </Button>
            </Stack>
            <Reveal open={open}>
              <Stack spacing={0.75}>
                <Payload
                  label={C.input}
                  value={call.input}
                  omitted={call.input_omitted}
                />
                <Payload
                  label={C.output}
                  value={call.output}
                  omitted={call.output_omitted}
                />
              </Stack>
            </Reveal>
          </Stack>
        ) : event.shown || event.empty ? (
          <Box
            sx={{ p: 1, borderRadius: "6px", bgcolor: "background.neutral" }}
          >
            {/* Worker text keeps the tab's folding and its block on images. */}
            <Answer shown={event.shown} empty={event.empty} />
          </Box>
        ) : event.note ? (
          <Typography
            sx={{
              typography: "s1",
              color: event.bad ? "error.main" : "text.secondary",
            }}
          >
            {event.note}
          </Typography>
        ) : null}
      </Stack>
    </Box>
  );
}
EventRow.propTypes = {
  event: PropTypes.object.isRequired,
  start: PropTypes.string,
  openJson: PropTypes.bool,
  onOpenTrace: PropTypes.func,
  traceIds: PropTypes.arrayOf(PropTypes.string).isRequired,
};

function EventList({
  events,
  start,
  openJson,
  bordered,
  onOpenTrace,
  traceIds,
}) {
  return (
    <Box
      data-testid="raw-events"
      sx={{
        ...(bordered && {
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          px: 1.5,
          py: 0.5,
        }),
        "& > * + *": { borderTop: "1px solid", borderTopColor: "divider" },
      }}
    >
      {events.map((event) => (
        <EventRow
          key={event.key}
          event={event}
          start={start}
          openJson={openJson(event)}
          onOpenTrace={onOpenTrace}
          traceIds={traceIds}
        />
      ))}
    </Box>
  );
}
EventList.propTypes = {
  events: PropTypes.array.isRequired,
  start: PropTypes.string,
  openJson: PropTypes.func.isRequired,
  bordered: PropTypes.bool,
  onOpenTrace: PropTypes.func,
  traceIds: PropTypes.arrayOf(PropTypes.string).isRequired,
};

// Answers come as markdown; in a cell they run on as one clamped passage, with the whole of it in the row's raw data.
const CLAMPED_CELL_SX = {
  typography: "s1",
  wordBreak: "break-word",
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
  "&& *": {
    display: "inline",
    color: "inherit",
    fontSize: "inherit",
    bgcolor: "transparent",
    m: 0,
    p: 0,
  },
};

// One hand-off as a table row. Its raw data opens below it, as wide as the table, since JSON won't fit in a cell.
function WorkerRow({ step, byKey, names, start, onOpenTrace, traceIds }) {
  const [open, setOpen] = useState(false);
  const name = names.ofStep(step);
  const worker = names.workerOf(step.worker_id);
  // The worker's own model and the tokens its model calls spent, when the app sent traces. They are its total, so
  // they show once, on its first hand-off.
  const usage =
    names.firstOf(step) && (worker?.model || worker?.tokens)
      ? C.modelTokens(worker.model, worker.tokens)
      : "";
  const end = step.end;
  const failed = end === "failed" || end === "no_return";
  const task = step.handoff?.task;
  const answer = step.returned?.result;
  const emptyResult = {
    passed: "—",
    finished: C.noResult,
    failed: C.noResult,
    no_return: C.noReturn(name),
    cut_off: C.returnCutOff(name),
  }[end];
  return (
    <>
      <TableRow
        hover
        data-testid="worker-row"
        data-worker={name}
        data-end={end}
        onClick={(e) => {
          // Dragging to copy text ends in a click; only a keyboard press has no detail.
          if (e.detail && window.getSelection()?.toString()) return;
          setOpen((one) => !one);
        }}
        sx={{
          cursor: "pointer",
          "& > td": { verticalAlign: "top", ...(open && { borderBottom: 0 }) },
        }}
      >
        <TableCell>
          <Box
            sx={{
              pl: Math.min(Math.max(step.depth - 1, 0), MAX_ROW_INDENT) * 1.5,
            }}
          >
            {/* A click here bubbles to the row, so the keyboard and the mouse share one toggle. */}
            <Box
              component="button"
              type="button"
              aria-expanded={open}
              aria-label={`${open ? C.hideRow(name) : C.showRow(name)}${failed ? `, ${C.failed}` : ""}`}
              sx={{
                display: "flex",
                alignItems: "flex-start",
                gap: 0.5,
                p: 0,
                border: 0,
                bgcolor: "transparent",
                color: "text.primary",
                cursor: "pointer",
                textAlign: "left",
                typography: "s1",
                wordBreak: "break-word",
              }}
            >
              <Iconify
                icon="solar:alt-arrow-right-linear"
                width={14}
                sx={{
                  mt: "3px",
                  color: "text.subtitle",
                  flexShrink: 0,
                  transform: open ? "rotate(90deg)" : "none",
                  transition: `transform ${MOTION_MS}ms ease`,
                  ...NO_MOTION_SX,
                }}
              />
              {failed ? (
                <Iconify
                  icon="solar:close-circle-bold"
                  width={14}
                  sx={{ mt: "3px", color: "error.main", flexShrink: 0 }}
                />
              ) : null}
              {name}
            </Box>
            {usage ? (
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", ml: 2.25 }}
              >
                {usage}
              </Typography>
            ) : null}
            {step.parallel ? (
              <Chip
                size="small"
                variant="outlined"
                label={C.inParallel}
                sx={{ mt: 0.5, ml: 2.25 }}
              />
            ) : null}
            {step.relationship === "unknown" ? (
              <UnknownParent sx={{ mt: 0.5, ml: 2.25 }} />
            ) : null}
            {step.end_state ? (
              <Chip
                size="small"
                variant="outlined"
                color="warning"
                label={C.cutShort[step.end_state]}
                data-testid="worker-cut-short"
                sx={{ mt: 0.5, ml: 2.25 }}
              />
            ) : null}
          </Box>
        </TableCell>
        <TableCell>
          <Box sx={{ ...CLAMPED_CELL_SX, color: "text.secondary" }}>
            {step.transfer ? C.transferred(name) : task || "—"}
          </Box>
        </TableCell>
        <TableCell>
          <Box
            sx={{
              ...CLAMPED_CELL_SX,
              color: failed ? "error.main" : "text.secondary",
            }}
          >
            {answer ? (
              <Markdown
                text={answer}
                variant="s1"
                disallowedElements={NO_IMAGES}
              />
            ) : (
              emptyResult
            )}
          </Box>
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell
          colSpan={3}
          sx={{ py: 0, ...(!open && { borderBottom: 0 }) }}
        >
          <Reveal open={open}>
            <Box data-testid="worker-row-raw" sx={{ pb: 1 }}>
              <EventList
                events={handOffEvents(step, byKey, names)}
                start={start}
                openJson={(event) => event.call?.status === "error"}
                onOpenTrace={onOpenTrace}
                traceIds={traceIds}
              />
            </Box>
          </Reveal>
        </TableCell>
      </TableRow>
    </>
  );
}
WorkerRow.propTypes = {
  step: PropTypes.object.isRequired,
  byKey: PropTypes.object.isRequired,
  names: PropTypes.object.isRequired,
  start: PropTypes.string,
  onOpenTrace: PropTypes.func,
  traceIds: PropTypes.arrayOf(PropTypes.string).isRequired,
};

function WorkersTable({ rows, byKey, names, start, onOpenTrace, traceIds }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Table size="small" sx={{ tableLayout: "fixed" }}>
        <TableHead>
          <TableRow>
            <TableCell sx={{ width: "30%" }}>{C.workerColumn}</TableCell>
            <TableCell sx={{ width: "30%" }}>{C.taskColumn}</TableCell>
            <TableCell>{C.resultColumn}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((step) => (
            <WorkerRow
              key={step.key}
              step={step}
              byKey={byKey}
              names={names}
              start={start}
              onOpenTrace={onOpenTrace}
              traceIds={traceIds}
            />
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}
WorkersTable.propTypes = {
  rows: PropTypes.array.isRequired,
  byKey: PropTypes.object.isRequired,
  names: PropTypes.object.isRequired,
  start: PropTypes.string,
  onOpenTrace: PropTypes.func,
  traceIds: PropTypes.arrayOf(PropTypes.string).isRequired,
};

/** One call of a multi-agent app: a table of its hand-offs first, every step in order on request. */
function PlacedChecks({ placements, names }) {
  return (
    <Stack spacing={0.5} data-testid="placed-checks">
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {C.placedChecks}
      </Typography>
      {placements.map((one, index) => {
        // A missing worker is Unknown, never guessed.
        const worker = one.worker_id
          ? names.ofWorker(one.worker_id)
          : C.unknown;
        return (
          <Typography
            key={`${one.name}-${index}`}
            data-testid="placed-check"
            data-step={one.step ?? ""}
            sx={{ typography: "s3", color: "text.secondary" }}
          >
            {C.checkStatus[one.status]} · {one.name} ·{" "}
            {one.untied
              ? C.untied[one.untied](one.tool)
              : C.placedOn(worker, one.tool)}
          </Typography>
        );
      })}
    </Stack>
  );
}
PlacedChecks.propTypes = {
  placements: PropTypes.arrayOf(PropTypes.object).isRequired,
  names: PropTypes.object.isRequired,
};

export default function CallWorkersPanel({
  callExecutionId,
  callStatus = null,
  onOpenTrace,
  traceIds = NO_TRACES,
}) {
  const { data, isError } = useCallWorkers(callExecutionId, callStatus);
  const [rawEvents, setRawEvents] = useState(false);
  // A failed refetch keeps the tree it has. A call known to have no record never gets here: the drawers drop the tab.
  if (!data)
    return isError ? (
      <Typography sx={{ p: 2, typography: "s1", color: "error.main" }}>
        {C.error}
      </Typography>
    ) : (
      <CircularProgress size={20} sx={{ m: 2 }} />
    );

  const workers = Object.fromEntries(
    data.workers.map((one) => [one.worker_id, one]),
  );
  const coordinator = data.coordinator?.name || C.coordinator;
  const names = {
    coordinator,
    ofStep: (step) => workers[step.worker_id]?.name || C.unknown,
    ofWorker: (id) => (id ? workers[id]?.name || id : coordinator),
    workerOf: (id) => workers[id],
    firstOf: (step) => firstRow[step.worker_id] === step.key,
  };
  const start = data.started_at;
  const byKey = stepsByKey(data);
  const rows = data.steps.filter((step) => step.kind === "handoff");
  const firstRow = {};
  for (const row of rows) firstRow[row.worker_id] ??= row.key;
  // With no hand-off there is nothing to tabulate, so the steps are the whole story.
  const showSteps = rawEvents || !rows.length;
  const events = showSteps ? callEvents(data, byKey, names) : [];
  const openAll =
    data.steps.filter((one) => one.kind === "tool_call").length <=
    MAX_OPEN_CALLS;
  const named = (test) =>
    data.workers
      .filter(test)
      .map((one) => one.name)
      .join(", ");
  const notObserved = named((one) => !one.observed);
  const pastDeadline = named((one) => one.state === "deadline_passed");
  const cutShort = data.workers
    .filter((one) => one.cut_short)
    .map((one) => `${one.name} (${C.cutShort[one.state]})`)
    .join(", ");
  const notDeclared = named((one) => one.undeclared);

  return (
    <Stack spacing={1.25} sx={{ p: 1.5 }}>
      {rows.length ? (
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <Typography sx={{ typography: "s1", fontWeight: 600, flex: 1 }}>
            {C.handOffSummary(data.counts)}
          </Typography>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={rawEvents}
                onChange={(e) => setRawEvents(e.target.checked)}
              />
            }
            label={C.rawEvents}
            slotProps={{ typography: { typography: "s1" } }}
            sx={{ m: 0, flexShrink: 0, gap: 0.5 }}
          />
        </Stack>
      ) : null}
      {data.collection === "no_evidence" ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.noEvidence}
        </Typography>
      ) : data.collection === "partial" || data.collection === "unavailable" ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {data.collection === "partial"
            ? C.partialCollection
            : C.unavailableCollection}
        </Typography>
      ) : data.spans_received === false ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.sandboxOnly}
        </Typography>
      ) : null}
      {data.workers.length ? (
        <Typography
          data-testid="supported-boundary"
          sx={{ typography: "s3", color: "text.subtitle" }}
        >
          {C.supportedBoundary}
          {data.beyond_boundary ? (
            <span data-testid="beyond-boundary"> {C.beyondBoundary}</span>
          ) : null}
        </Typography>
      ) : null}
      {showSteps ? (
        events.length ? (
          <EventList
            bordered
            events={events}
            start={start}
            openJson={() => openAll}
            onOpenTrace={onOpenTrace}
            traceIds={traceIds}
          />
        ) : null
      ) : (
        data.turns.map((round) => {
          const label = turnLabel(round, data.turns);
          const roundRows = handOffsOf(data, round.turn);
          return (
            <Stack key={round.turn} spacing={1.25} data-testid="turn">
              {noMessages(round, data.turns) && !round.request ? null : (
                <Turn
                  title={`${label}${C.request}`}
                  turn={round.request}
                  start={start}
                  empty={C.noRequest}
                />
              )}
              {roundRows.length ? (
                <WorkersTable
                  rows={roundRows}
                  byKey={byKey}
                  names={names}
                  start={start}
                  onOpenTrace={onOpenTrace}
                  traceIds={traceIds}
                />
              ) : null}
              {round.calls_outside ? (
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {C.callsOutsideTable(round.calls_outside)}
                </Typography>
              ) : null}
              {noMessages(round, data.turns) && !round.response ? null : (
                <Turn
                  title={`${label}${C.response}`}
                  turn={round.response}
                  agent
                  start={start}
                  empty={C.noReply}
                />
              )}
            </Stack>
          );
        })
      )}
      {data.placements.length ? (
        <PlacedChecks placements={data.placements} names={names} />
      ) : null}
      {notObserved ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersNotObserved(notObserved)}
        </Typography>
      ) : null}
      {cutShort ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersCutShort(cutShort)}
        </Typography>
      ) : null}
      {pastDeadline ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersPastDeadline(pastDeadline)}
        </Typography>
      ) : null}
      {notDeclared ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.workersNotDeclared(notDeclared)}
        </Typography>
      ) : null}
      {data.events_omitted ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.eventsOmitted(data.events_omitted)}
        </Typography>
      ) : null}
    </Stack>
  );
}

CallWorkersPanel.propTypes = {
  callExecutionId: PropTypes.string,
  // The call's status, so a call that ends while open is read again.
  callStatus: PropTypes.string,
  // Opens one of the call's traces in its Traces tab, at the step's span when it has one.
  onOpenTrace: PropTypes.func,
  // The traces that tab lists: a step offers to open only one of these.
  traceIds: PropTypes.arrayOf(PropTypes.string),
};
