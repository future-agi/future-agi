import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Chip,
  CircularProgress,
  Collapse,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import {
  isHandOff,
  useCallWorkers,
} from "src/api/simulate-environments/runDetail";
import { Markdown } from "src/sections/simulate/environments/buildEnvironment/console/ConsoleTurn";
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";
import { arrange, asShown, isLong, seconds } from "./utils/callWorkersUtils";

// One colour per level of hand-off, so a deep or wide team stays readable; names tell workers apart.
const DEPTH_COLORS = ["info", "success", "warning", "secondary", "error"];
const depthColor = (depth) =>
  DEPTH_COLORS[(Math.max(depth, 1) - 1) % DEPTH_COLORS.length];
// Levels shown open at first; deeper hand-offs start folded.
const OPEN_DEPTH = 2;
// Worker chips shown one by one; a larger team is summarised.
const MAX_CHIPS = 8;
const MONO = "ui-monospace, Menlo, monospace";

// "+5.9 s" from the call's start, with the exact clock time on hover.
function Stamp({ at, start }) {
  const moment = seconds(at);
  if (moment === null) return null;
  const from = seconds(start);
  const label =
    from === null
      ? new Date(moment * 1000).toISOString().slice(11, 19)
      : `+${Math.max(0, moment - from).toFixed(1)} s`;
  return (
    <Tooltip
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
    </Tooltip>
  );
}
Stamp.propTypes = { at: PropTypes.string, start: PropTypes.string };

function Line({ title, at, start, color, children }) {
  return (
    <Stack
      direction="row"
      spacing={1}
      alignItems="baseline"
      justifyContent="space-between"
    >
      {/* A long task stays a heading: three lines, the whole of it on hover. */}
      <Typography
        title={typeof title === "string" ? title : undefined}
        sx={{
          typography: "s2",
          fontWeight: 600,
          color,
          wordBreak: "break-word",
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {title}
      </Typography>
      <Stamp at={at} start={start} />
      {children}
    </Stack>
  );
}
Line.propTypes = {
  title: PropTypes.node,
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
          {omitted ? C.payloadOmitted : C.unknown}
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

// A tool call in the transcript's tool style; its input and output open on click.
function ToolRow({ event, who, start }) {
  const [open, setOpen] = useState(false);
  const failed = event.status === "error";
  return (
    <Box
      sx={{ px: 1, py: 0.5, borderRadius: 0.75, bgcolor: "background.neutral" }}
    >
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        onClick={() => setOpen((one) => !one)}
        sx={{ cursor: "pointer" }}
      >
        <Iconify
          icon="solar:code-linear"
          width={13}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
        <Typography
          sx={{
            typography: "s3",
            fontFamily: MONO,
            color: failed ? "error.main" : "text.secondary",
            wordBreak: "break-word",
            flex: 1,
          }}
        >
          {who ? `${who} · ${event.tool}` : event.tool}
          {failed ? ` (${C.failed})` : ""}
        </Typography>
        <Stamp at={event.at} start={start} />
        <Iconify
          icon={
            open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
          }
          width={12}
          sx={{ color: "text.subtitle" }}
          aria-label={C.showDetail}
        />
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Stack spacing={0.75} sx={{ pt: 0.75, pl: 2.5 }}>
          <Payload
            label={C.input}
            value={event.input}
            omitted={event.payload_omitted}
          />
          <Payload
            label={C.output}
            value={event.output}
            omitted={event.payload_omitted}
          />
        </Stack>
      </Collapse>
    </Box>
  );
}
ToolRow.propTypes = {
  event: PropTypes.object.isRequired,
  who: PropTypes.string,
  start: PropTypes.string,
};

function Answer({ text }) {
  const [whole, setWhole] = useState(false);
  if (!text)
    return (
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {C.unknown}
      </Typography>
    );
  const shown = asShown(text);
  const clamped = isLong(shown) && !whole;
  return (
    <Box>
      <Box
        data-testid="answer"
        data-clamped={clamped}
        sx={{
          wordBreak: "break-word",
          // Headings inside an answer read as bold lines, not page titles.
          "& h1, & h2, & h3, & h4, & h5, & h6": {
            typography: "s2",
            fontWeight: 600,
            my: 0.25,
          },
          ...(clamped && { maxHeight: "8.4em", overflow: "hidden" }),
        }}
      >
        <Markdown text={shown} dim />
      </Box>
      {isLong(shown) ? (
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
Answer.propTypes = { text: PropTypes.string };

// A turn of the caller or the coordinator, styled as the transcript styles it.
function Turn({ title, turn, agent, start }) {
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
      <Answer text={turn?.text} />
    </Box>
  );
}
Turn.propTypes = {
  title: PropTypes.string,
  turn: PropTypes.object,
  agent: PropTypes.bool,
  start: PropTypes.string,
};

// Children of one node, as a file tree draws them: a guide line down the left and an elbow into each child.
const ELBOW_TOP = 13;
function Branch({ children }) {
  const items = children.filter(Boolean);
  if (!items.length) return null;
  return (
    <Box data-testid="tree-branch" sx={{ ml: 1, pl: 2.25, pt: 0.75 }}>
      {items.map((child, index) => (
        <Box
          key={child.key ?? index}
          data-testid="tree-child"
          sx={{
            position: "relative",
            pb: index === items.length - 1 ? 0 : 0.75,
            "&::before": {
              content: '""',
              position: "absolute",
              left: -18,
              top: 0,
              height: index === items.length - 1 ? ELBOW_TOP : "100%",
              borderLeft: "1px solid",
              borderColor: "divider",
            },
            "&::after": {
              content: '""',
              position: "absolute",
              left: -18,
              top: ELBOW_TOP,
              width: 14,
              borderTop: "1px solid",
              borderColor: "divider",
            },
          }}
        >
          {child}
        </Box>
      ))}
    </Box>
  );
}
Branch.propTypes = { children: PropTypes.array.isRequired };

// A hand-off to one worker: what it was asked, its own calls, the hand-offs it made in turn, and what it returned.
function HandOff({ block, workers, nameOf, start }) {
  const [open, setOpen] = useState(block.depth <= OPEN_DEPTH);
  const name = workers[block.worker_id]?.name || C.unknown;
  const color = depthColor(block.depth);
  const transfer = Boolean(block.transfer);
  return (
    <Box
      data-testid="hand-off"
      data-depth={block.depth}
      data-worker={name}
      sx={{
        borderLeft: "2px solid",
        borderColor: `${color}.main`,
        bgcolor: (t) =>
          alpha(t.palette[color].main, t.palette.mode === "dark" ? 0.08 : 0.05),
        px: 1.75,
        py: 1,
      }}
    >
      <Stack
        direction="row"
        spacing={0.75}
        alignItems="flex-start"
        onClick={() => setOpen((one) => !one)}
        sx={{ cursor: "pointer" }}
      >
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={14}
          sx={{ mt: "3px", color: "text.subtitle", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Line
            title={
              transfer
                ? C.transferred(name)
                : `${C.delegated(name)}${block.delegated?.task ? `: ${block.delegated.task}` : ""}`
            }
            at={block.delegated?.at}
            start={start}
          >
            {block.parallel ? (
              <Chip
                size="small"
                variant="outlined"
                label={C.inParallel}
                sx={{ flexShrink: 0 }}
              />
            ) : null}
          </Line>
        </Box>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Stack spacing={0.75}>
          <Branch>
            {block.items.map((item) =>
              item.kind === "handoff" ? (
                <HandOff
                  key={item.key}
                  block={item}
                  workers={workers}
                  nameOf={nameOf}
                  start={start}
                />
              ) : (
                <ToolRow
                  key={item.key}
                  event={item.event}
                  // Inside its worker's node a call names only the tool; one made by another agent still says whose.
                  who={
                    item.event.worker_id === block.worker_id &&
                    item.event.attribution !== "unattributed"
                      ? null
                      : nameOf(item.event)
                  }
                  start={start}
                />
              ),
            )}
          </Branch>
          {transfer ? null : block.returned ? (
            <Box>
              <Line
                title={`${C.returned(name)}${block.returned.status === "error" ? ` (${C.failed})` : ""}`}
                at={block.returned.at}
                start={start}
                color={
                  block.returned.status === "error" ? "error.main" : undefined
                }
              />
              <Answer text={block.returned.result} />
            </Box>
          ) : (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {C.noReturn(name)}
            </Typography>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}
HandOff.propTypes = {
  block: PropTypes.object.isRequired,
  workers: PropTypes.object.isRequired,
  nameOf: PropTypes.func,
  start: PropTypes.string,
};

function WorkerChips({ workers, depthOf }) {
  const [shown, setShown] = useState(false);
  const chips = workers.map((one) => (
    <Chip
      key={one.worker_id}
      size="small"
      variant={one.observed ? "soft" : "outlined"}
      color={
        one.worker_id in depthOf
          ? depthColor(depthOf[one.worker_id])
          : "default"
      }
      label={[
        one.name,
        !one.observed && C.notObserved,
        one.undeclared && C.undeclared,
      ]
        .filter(Boolean)
        .join(" · ")}
    />
  ));
  const row = (items) => (
    <Stack
      direction="row"
      spacing={0.75}
      flexWrap="wrap"
      rowGap={0.75}
      sx={{ mt: 0.75 }}
    >
      {items}
    </Stack>
  );
  if (workers.length <= MAX_CHIPS) return row(chips);
  const observed = workers.filter((one) => one.observed).length;
  return (
    <Box sx={{ mt: 0.75 }}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography sx={{ typography: "s2" }}>
          {C.workersSummary(
            workers.length,
            observed,
            workers.length - observed,
          )}
        </Typography>
        <Typography
          component="button"
          onClick={() => setShown((one) => !one)}
          sx={{
            typography: "s3",
            color: "primary.main",
            border: 0,
            bgcolor: "transparent",
            cursor: "pointer",
            p: 0,
          }}
        >
          {shown ? C.hideWorkers : C.showWorkers}
        </Typography>
      </Stack>
      <Collapse in={shown} unmountOnExit>
        {row(chips)}
      </Collapse>
    </Box>
  );
}
WorkerChips.propTypes = {
  workers: PropTypes.array.isRequired,
  depthOf: PropTypes.object.isRequired,
};

/** One call of a multi-agent app as a hand-off tree under the coordinator, framed by the request and the reply. */
export default function CallWorkersPanel({ callExecutionId }) {
  const { data, isLoading, isError } = useCallWorkers(callExecutionId);
  if (isLoading) return <CircularProgress size={20} sx={{ m: 2 }} />;
  if (isError)
    return (
      <Typography sx={{ p: 2, typography: "s2", color: "error.main" }}>
        {C.error}
      </Typography>
    );
  if (!data)
    return (
      <Typography sx={{ p: 2, typography: "s2", color: "text.secondary" }}>
        {C.none}
      </Typography>
    );

  const workers = Object.fromEntries(
    data.workers.map((one) => [one.worker_id, one]),
  );
  const nameOf = (event) =>
    event.attribution === "unattributed"
      ? C.unknown
      : event.worker_id
        ? workers[event.worker_id]?.name || event.worker_id
        : C.coordinator;
  const start = data.started_at;
  const hasHandOffs = data.events.some(isHandOff);
  const tree = arrange(data.events);

  return (
    <Stack spacing={1.25} sx={{ p: 1.5 }}>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {C.intro}
      </Typography>
      {data.spans_received === false ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.sandboxOnly}
        </Typography>
      ) : null}
      {hasHandOffs ? (
        <Turn title={C.request} turn={data.request} start={start} />
      ) : !data.workers.length ? (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {C.noHandOffs}
        </Typography>
      ) : null}
      {/* The coordinator is the tree's root: every hand-off hangs under the agent that made it. */}
      <Box data-testid="tree-root">
        <Box
          sx={{
            borderLeft: "2px solid",
            borderColor: "primary.main",
            bgcolor: (t) =>
              alpha(
                t.palette.primary.main,
                t.palette.mode === "dark" ? 0.06 : 0.04,
              ),
            px: 1.75,
            py: 1,
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`${C.coordinator} · ${data.coordinator?.name || C.unknown}`}
          </Typography>
          <WorkerChips workers={data.workers} depthOf={tree.depthOf} />
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
          >
            {tree.depth === 0
              ? null
              : tree.depth === 1 && !tree.parallel
                ? C.seenFlat
                : C.seen(tree.depth, tree.parallel)}
          </Typography>
          {tree.depth > 1 || tree.parallel ? (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {C.scope}
            </Typography>
          ) : null}
        </Box>
        <Branch>
          {tree.items.map((item) =>
            item.kind === "handoff" ? (
              <HandOff
                key={item.key}
                block={item}
                workers={workers}
                nameOf={nameOf}
                start={start}
              />
            ) : (
              <ToolRow
                key={item.key}
                event={item.event}
                who={nameOf(item.event)}
                start={start}
              />
            ),
          )}
        </Branch>
      </Box>
      {data.events_omitted ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.eventsOmitted(data.events_omitted)}
        </Typography>
      ) : null}
      {hasHandOffs && (
        <Turn title={C.response} turn={data.response} agent start={start} />
      )}
    </Stack>
  );
}

CallWorkersPanel.propTypes = { callExecutionId: PropTypes.string };
