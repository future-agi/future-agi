import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ChatMarkdown from "src/sections/simulate/environments/buildEnvironment/console/ChatMarkdown";
import {
  CALL_WORKERS_COPY as C,
  MAX_ROW_INDENT,
  MOTION_MS,
  NO_MOTION_SX,
} from "./callTraces.constants";
import { EventList } from "./CallWorkersEventList";
import { Reveal, UnknownParent } from "./CallWorkersParts";
import { asMarkdown, handOffEvents } from "./utils/callWorkersUtils";

// A cell's text takes the cell's size and colour, so a failed answer reads in red.
const CELL_TEXT_SX = { typography: "s1", color: "inherit" };

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
    step.first_of_worker && (worker?.model || worker?.tokens)
      ? C.modelTokens(worker.model, worker.tokens)
      : "";
  const end = step.end;
  const failed = step.did_not_finish;
  const task = step.handoff?.task_shown;
  const answer = step.returned?.result_shown;
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
          </Box>
        </TableCell>
        <TableCell>
          <Box sx={{ ...CLAMPED_CELL_SX, color: "text.secondary" }}>
            {step.transfer ? (
              C.transferred(name)
            ) : task ? (
              <ChatMarkdown text={asMarkdown(task)} sx={CELL_TEXT_SX} />
            ) : (
              "—"
            )}
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
              <ChatMarkdown text={asMarkdown(answer)} sx={CELL_TEXT_SX} />
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

export function WorkersTable({
  rows,
  byKey,
  names,
  start,
  onOpenTrace,
  traceIds,
}) {
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
