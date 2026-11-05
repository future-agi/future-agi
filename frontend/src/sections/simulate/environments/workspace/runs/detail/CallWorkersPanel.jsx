import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Chip,
  CircularProgress,
  Collapse,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import CustomJsonViewer from "src/components/custom-json-viewer/CustomJsonViewer";
import {
  isHandOff,
  useCallWorkers,
} from "src/api/simulate-environments/runDetail";
import Markdown from "src/sections/simulate/environments/buildEnvironment/console/Markdown";
import { MONO_FONT as MONO } from "src/sections/simulate/environments/workspace/contract/contract.constants";
import { seriesColor } from "src/theme/data-colors";
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";
import { arrange, asShown, isLong, seconds } from "./utils/callWorkersUtils";
import { formatClock } from "src/components/VoiceDetailDrawerV2/transcriptUtils";

// One colour per level of hand-off, so a deep or wide team stays readable; names tell workers apart.
const depthColor = (t, depth) => seriesColor(t, Math.max(depth, 1) - 1);
// Levels shown open at first; deeper hand-offs start folded.
const OPEN_DEPTH = 2;
// Worker chips shown one by one; a larger team is summarised.
const MAX_CHIPS = 8;
// Past this a heading may hit its three-line clamp, so the whole of it shows on hover.
const LONG_TITLE = 80;
const NO_IMAGES = ["img"];

const DISCLOSURE_SX = {
  cursor: "pointer",
  borderRadius: "4px",
  outline: "none",
  "&:hover, &:focus-visible": { bgcolor: "action.hover" },
};
const disclosure = (open, toggle) => ({
  role: "button",
  tabIndex: 0,
  "aria-expanded": open,
  onClick: toggle,
  onKeyDown: (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  },
});

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

// A tool call in the transcript's tool style; its input and output open on click.
function ToolRow({ event, who, start }) {
  const [open, setOpen] = useState(false);
  const failed = event.status === "error";
  return (
    <Box
      sx={{
        pl: 1,
        pr: 1.75,
        py: 0.5,
        borderRadius: 0.75,
        bgcolor: "background.neutral",
      }}
    >
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        {...disclosure(open, () => setOpen((one) => !one))}
        sx={DISCLOSURE_SX}
      >
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={12}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
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

function Answer({ text, empty }) {
  const [whole, setWhole] = useState(false);
  if (!text)
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {empty}
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
          typography: "s1",
          wordBreak: "break-word",
          // Headings inside an answer read as bold lines, not page titles.
          "& h1, & h2, & h3, & h4, & h5, & h6": {
            typography: "s1",
            fontWeight: 600,
            my: 0.25,
          },
          ...(clamped && { maxHeight: "9em", overflow: "hidden" }),
        }}
      >
        {/* Worker text isn't ours: an image in it would load from any host. */}
        <Markdown
          text={shown}
          dim
          variant="s1"
          disallowedElements={NO_IMAGES}
        />
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
Answer.propTypes = { text: PropTypes.string, empty: PropTypes.string };

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
      <Answer text={turn?.text} empty={empty} />
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

// Children of one node, as a file tree draws them: a guide line down the left and an elbow into each child.
const ELBOW_TOP = 13;
function Branch({ children, sx }) {
  const items = children.filter(Boolean);
  if (!items.length) return null;
  return (
    <Box data-testid="tree-branch" sx={{ ml: 1, pl: 2.25, pt: 0.75, ...sx }}>
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
Branch.propTypes = {
  children: PropTypes.array.isRequired,
  sx: PropTypes.object,
};

// A hand-off to one worker: what it was asked, its own calls, the hand-offs it made in turn, and what it returned.
function HandOff({ block, workers, start, cutOff }) {
  const [open, setOpen] = useState(block.depth <= OPEN_DEPTH);
  const name = workers[block.worker_id]?.name || C.unknown;
  const transfer = Boolean(block.transfer);
  return (
    <Box
      data-testid="hand-off"
      data-depth={block.depth}
      data-worker={name}
      sx={{
        borderLeft: "2px solid",
        borderColor: (t) => depthColor(t, block.depth),
        bgcolor: (t) =>
          alpha(
            depthColor(t, block.depth),
            t.palette.mode === "dark" ? 0.08 : 0.05,
          ),
        px: 1.75,
        py: 1,
      }}
    >
      <Stack
        direction="row"
        spacing={0.75}
        alignItems="flex-start"
        {...disclosure(open, () => setOpen((one) => !one))}
        // Named by the hand-off alone; otherwise the whole task becomes the button's name.
        aria-label={transfer ? C.transferred(name) : C.delegated(name)}
        sx={DISCLOSURE_SX}
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
          {/* Each level reaches its parent's right edge, so times line up at any depth. */}
          <Branch sx={{ mr: -1.75 }}>
            {block.items.map((item) =>
              item.kind === "handoff" ? (
                <HandOff
                  key={item.key}
                  block={item}
                  workers={workers}
                  start={start}
                  cutOff={cutOff}
                />
              ) : (
                // Inside its worker's node a call names only the tool.
                <ToolRow key={item.key} event={item.event} start={start} />
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
              <Answer text={block.returned.result} empty={C.noResult} />
            </Box>
          ) : (
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              {cutOff ? C.returnCutOff(name) : C.noReturn(name)}
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
  start: PropTypes.string,
  cutOff: PropTypes.bool,
};

function WorkerChips({ workers, depthOf }) {
  const [shown, setShown] = useState(false);
  const chips = workers.map((one) => (
    <Chip
      key={one.worker_id}
      size="small"
      variant={one.observed ? "soft" : "outlined"}
      sx={
        one.worker_id in depthOf
          ? (t) => ({
              borderColor: depthColor(t, depthOf[one.worker_id]),
              ...(one.observed && {
                bgcolor: alpha(depthColor(t, depthOf[one.worker_id]), 0.16),
              }),
            })
          : undefined
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
        <Typography sx={{ typography: "s1" }}>
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
  const { data, isError } = useCallWorkers(callExecutionId);
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
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {C.intro}
      </Typography>
      {data.spans_received === false ? (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {C.sandboxOnly}
        </Typography>
      ) : null}
      {hasHandOffs && (
        <Turn
          title={C.request}
          turn={data.request}
          start={start}
          empty={C.noRequest}
        />
      )}
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
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {data.coordinator?.name
              ? `${C.coordinator} · ${data.coordinator.name}`
              : C.coordinator}
          </Typography>
          <WorkerChips workers={data.workers} depthOf={tree.depthOf} />
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.75 }}
          >
            {tree.depth === 0
              ? null
              : tree.depth === 1 && !tree.parallel && !tree.overlapUnknown
                ? C.seenFlat
                : C.seen(tree.depth, tree.parallel, tree.overlapUnknown)}
          </Typography>
        </Box>
        <Branch>
          {tree.items.map((item) =>
            item.kind === "handoff" ? (
              <HandOff
                key={item.key}
                block={item}
                workers={workers}
                start={start}
                cutOff={data.events_omitted > 0}
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
        <Turn
          title={C.response}
          turn={data.response}
          agent
          start={start}
          empty={C.noReply}
        />
      )}
    </Stack>
  );
}

CallWorkersPanel.propTypes = { callExecutionId: PropTypes.string };
