import { Fragment, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  ID_NOTE,
  NOT_RECORDED,
  clock,
  didLineOf,
  kvOf,
  participantsOf,
  resultSentenceOf,
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

/* One tool call: its name and time, what it was sent, what came back. */
function ToolCall({ call }) {
  const [raw, setRaw] = useState(false);
  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="baseline">
        <Typography
          sx={{
            ...mono,
            typography: "s3",
            flex: 1,
            minWidth: 0,
            overflowWrap: "anywhere",
            color: call.blocked ? "status.danger.fg" : "text.primary",
          }}
        >
          {call.name}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
            flexShrink: 0,
          }}
        >
          {clock(call.at)}
        </Typography>
      </Stack>
      <Stack spacing={0.75} sx={{ pl: 2, pt: 0.5 }}>
        <Pairs title="Sent" data={call.input} />
        <Pairs title="Got back" data={call.output} />
        <Box>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            aria-expanded={raw}
            onClick={() => setRaw((v) => !v)}
            sx={{ typography: "s3", minHeight: 24, py: 0, px: 1 }}
          >
            {raw ? "Hide JSON" : "Show JSON"}
          </Button>
        </Box>
        {raw && (
          <Stack spacing={0.75}>
            <Json label="Input" value={call.input} />
            <Json label="Output" value={call.output} />
          </Stack>
        )}
      </Stack>
    </Box>
  );
}
ToolCall.propTypes = { call: PropTypes.object.isRequired };

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

/* The task and tool calls under an opened worker. */
function WorkerDetail({ w }) {
  return (
    <Stack spacing={1.5}>
      <Box>
        <Label>Task</Label>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {w.task}
        </Typography>
      </Box>
      {w.calls.map((c) => (
        <ToolCall key={c.id} call={c} />
      ))}
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        <Box component="span" sx={mono}>
          {w.wid}
        </Box>
        {`  ·  ${ID_NOTE}`}
      </Typography>
    </Stack>
  );
}
WorkerDetail.propTypes = { w: PropTypes.object.isRequired };

/* One worker: name, what it did, what came back. The whole row opens. */
function WorkerRow({ w, last }) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((v) => !v);
  const timeout = w.state === "timeout";
  const unseen = w.state === "unobserved";
  const edge = last && !open ? { borderBottom: 0 } : {};
  return (
    <Fragment>
      <TableRow
        hover
        tabIndex={0}
        aria-expanded={open}
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
          ...(open ? { "& > td": { borderBottom: 0 } } : {}),
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
      {open && (
        <TableRow sx={{ height: "auto" }}>
          <TableCell
            colSpan={3}
            sx={{
              ...cell,
              pt: 0,
              pb: 1.75,
              ...(last ? { borderBottom: 0 } : {}),
            }}
          >
            <WorkerDetail w={w} />
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  );
}
WorkerRow.propTypes = {
  w: PropTypes.object.isRequired,
  last: PropTypes.bool,
};

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
  const p = participantsOf({ agent, task });
  if (!p) return null;
  return (
    <Stack spacing={2} sx={{ p: 2 }}>
      <Typography sx={{ typography: "s2", fontWeight: 600, lineHeight: 1.6 }}>
        {p.summary}
      </Typography>
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
                <WorkerRow key={w.id} w={w} last={i === p.workers.length - 1} />
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
      {p.reply && (
        <Quote label={`${p.coordinator.name} replied`} text={p.reply} />
      )}
      <Details p={p} agent={agent} task={task} />
    </Stack>
  );
}

HandoffTimeline.propTypes = {
  agent: PropTypes.object,
  task: PropTypes.object.isRequired,
};
