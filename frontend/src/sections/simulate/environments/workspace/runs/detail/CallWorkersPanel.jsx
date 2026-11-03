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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import Iconify from "src/components/iconify";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import { useCallWorkers } from "src/api/simulate-environments/runDetail";
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";

// One colour per worker, kept on its chip and its block.
const WORKER_COLORS = ["info", "success", "warning", "secondary", "error"];
const MONO = "ui-monospace, Menlo, monospace";

const seconds = (iso) => {
  const value = iso ? Date.parse(iso.endsWith("Z") ? iso : `${iso}Z`) : NaN;
  return Number.isNaN(value) ? null : value / 1000;
};

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
      <Typography
        sx={{
          typography: "s2",
          fontWeight: 600,
          color,
          wordBreak: "break-word",
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

function Payload({ label, value }) {
  return (
    <Box>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      {value === null || value === undefined ? (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {C.unknown}
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
Payload.propTypes = { label: PropTypes.string, value: PropTypes.any };

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
          {`${who} · ${event.tool}`}
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
          <Payload label={C.input} value={event.input} />
          <Payload label={C.output} value={event.output} />
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
  if (!text)
    return (
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {C.unknown}
      </Typography>
    );
  return (
    <Box
      sx={{
        typography: "s2",
        color: "text.secondary",
        wordBreak: "break-word",
        "& p, & ul, & ol": { my: 0.25 },
        "& ul, & ol": { pl: 2.5 },
      }}
    >
      {/* A worker's answer is often markdown; show it as the app's other agent text is shown. */}
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
      >
        {text}
      </ReactMarkdown>
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

// A hand-off to one worker: what it was asked, its own calls, and what it returned.
function HandOff({ block, worker, color, nameOf, start }) {
  const [open, setOpen] = useState(true);
  const name = worker?.name || C.unknown;
  return (
    <Box
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
            title={`${C.delegated(name)}${block.delegated?.task ? `: ${block.delegated.task}` : ""}`}
            at={block.delegated?.at}
            start={start}
          />
        </Box>
      </Stack>
      <Collapse in={open}>
        <Stack spacing={0.75} sx={{ pt: 0.75, pl: 2.5 }}>
          {block.calls.map((event) => (
            <ToolRow
              key={event.seq}
              event={event}
              who={nameOf(event)}
              start={start}
            />
          ))}
          {block.returned ? (
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
              {`${C.returned(name)}: ${C.unknown}`}
            </Typography>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}
HandOff.propTypes = {
  block: PropTypes.object.isRequired,
  worker: PropTypes.object,
  color: PropTypes.string,
  nameOf: PropTypes.func,
  start: PropTypes.string,
};

// Events in order, each hand-off gathering its worker's calls and its return; anything else stays on its own.
const arrange = (events) => {
  const items = [];
  const open = {};
  for (const event of events) {
    if (event.type === "delegated") {
      const block = {
        kind: "handoff",
        key: event.seq,
        worker_id: event.worker_id,
        delegated: event,
        calls: [],
      };
      open[event.worker_id] = block;
      items.push(block);
    } else if (event.type === "returned") {
      const block = open[event.worker_id];
      if (block) {
        block.returned = event;
        delete open[event.worker_id];
      } else
        items.push({
          kind: "handoff",
          key: event.seq,
          worker_id: event.worker_id,
          calls: [],
          returned: event,
        });
    } else if (event.worker_id && open[event.worker_id])
      open[event.worker_id].calls.push(event);
    else items.push({ kind: "call", key: event.seq, event });
  }
  return items;
};

/**
 * One call of a multi-agent app as a hand-off timeline: the caller's request, each hand-off to a
 * worker with the worker's tool calls (input and output) and what it returned, then the
 * coordinator's reply. Read from the spans the app exported and the sandbox's record of each
 * tool call; anything not seen reads Unknown, and a declared worker that never ran shows as
 * not observed.
 */
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
  const colorOf = Object.fromEntries(
    data.workers.map((one, index) => [
      one.worker_id,
      WORKER_COLORS[index % WORKER_COLORS.length],
    ]),
  );
  const nameOf = (event) =>
    event.attribution === "unattributed"
      ? C.unknown
      : event.worker_id
        ? workers[event.worker_id]?.name || event.worker_id
        : C.coordinator;
  const start = data.started_at;
  const hasHandOffs = data.events.some((one) => one.type === "delegated");

  return (
    <Stack spacing={1.25} sx={{ p: 1.5 }}>
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
        <Stack
          direction="row"
          spacing={0.75}
          flexWrap="wrap"
          rowGap={0.75}
          sx={{ mt: 0.75 }}
        >
          {data.workers.map((one) => (
            <Chip
              key={one.worker_id}
              size="small"
              variant={one.observed ? "soft" : "outlined"}
              color={colorOf[one.worker_id]}
              label={[
                one.name,
                !one.observed && C.notObserved,
                one.undeclared && C.undeclared,
              ]
                .filter(Boolean)
                .join(" · ")}
            />
          ))}
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}>
          {C.topology}
        </Typography>
      </Box>
      {hasHandOffs && (
        <Turn title={C.request} turn={data.request} start={start} />
      )}
      {arrange(data.events).map((item) =>
        item.kind === "handoff" ? (
          <HandOff
            key={item.key}
            block={item}
            worker={workers[item.worker_id]}
            color={colorOf[item.worker_id] || "info"}
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
      {hasHandOffs && (
        <Turn title={C.response} turn={data.response} agent start={start} />
      )}
    </Stack>
  );
}

CallWorkersPanel.propTypes = { callExecutionId: PropTypes.string };
