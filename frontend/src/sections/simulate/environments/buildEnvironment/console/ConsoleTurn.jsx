import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { Box, Stack, Typography, Collapse } from "@mui/material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import Iconify from "src/components/iconify";

import { CONSOLE_COPY } from "../build.constants";
import AskUserQuestionCard from "./AskUserQuestionCard";
import NudgeCard from "./NudgeCard";

const MONO = "ui-monospace, Menlo, monospace";

// Module-local (not exported) so it can be shared by Step and Turn without
// tripping react-refresh/only-export-components.
const STEP_SHAPE = PropTypes.shape({
  kind: PropTypes.oneOf([
    "think",
    "note",
    "tool",
    "file",
    "json",
    "ask",
    "group",
    "error",
    "cancelled",
    "completed",
    "heartbeat",
    "nudge-line",
  ]),
  text: PropTypes.string,
  markdown: PropTypes.bool,
  label: PropTypes.string,
  result: PropTypes.string,
  state: PropTypes.oneOf(["running", "completed", "failed", "interrupted"]),
  path: PropTypes.string,
  note: PropTypes.string,
  value: PropTypes.string,
  count: PropTypes.number,
  lines: PropTypes.arrayOf(PropTypes.string),
  since: PropTypes.string,
  question: PropTypes.object,
  resolved: PropTypes.bool,
  answerText: PropTypes.string,
  onSubmit: PropTypes.func,
  onSkip: PropTypes.func,
  onRetry: PropTypes.func,
});

/* ── one turn ────────────────────────────────────────────────────────────── */

export function Turn({ turn }) {
  if (!turn) return null;

  if (turn.role === "user") {
    return (
      <Stack alignItems="flex-end" spacing={0.25}>
        <Box
          sx={{
            maxWidth: "82%", px: 2, py: 1, borderRadius: "8px",
            bgcolor: "action.selected",
            // Waiting in the server's queue; the builder hasn't picked it up yet.
            opacity: turn.queued ? 0.6 : 1,
          }}
        >
          {/* pre-wrap keeps the line breaks the user typed. */}
          <Typography sx={{ typography: "m3", color: "text.primary", whiteSpace: "pre-wrap" }}>
            {turn.text}
          </Typography>
        </Box>
        {turn.queued && (
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {CONSOLE_COPY.queued}
          </Typography>
        )}
      </Stack>
    );
  }

  if (turn.role === "system") return <NudgeCard turn={turn} />;

  return (
    <Stack spacing={1.5}>
      {turn.title && (
        <Typography
          sx={{ typography: "s1", fontWeight: 500, color: "text.primary", pt: 0.5 }}
        >
          {turn.title}
        </Typography>
      )}
      {(turn.steps || []).map((step, i) => <Step key={step.id ?? i} step={step} />)}
    </Stack>
  );
}
Turn.propTypes = {
  turn: PropTypes.shape({
    id: PropTypes.string,
    role: PropTypes.oneOf(["builder", "user", "system"]),
    title: PropTypes.string,
    text: PropTypes.string,
    steps: PropTypes.arrayOf(STEP_SHAPE),
    // A user line still waiting for the builder.
    queued: PropTypes.bool,
  }),
};

/* ── one step ────────────────────────────────────────────────────────────── */

// Status roles (theme.palette.status): running is info, completed success,
// failed danger.
const TOOL_ROLE = {
  running: "info",
  completed: "success",
  failed: "danger",
  // A tool that never finished because the run stopped — a muted, non-pulsing dot.
  interrupted: "neutral",
};

export function Step({ step }) {
  const [open, setOpen] = useState(false);

  if (!step) return null;

  if (step.kind === "think" || step.kind === "note") {
    if (step.markdown) {
      return <Markdown text={step.text} dim={step.kind === "think"} />;
    }
    return (
      <Typography
        sx={{
          typography: "m3",
          color: step.kind === "think" ? "text.secondary" : "text.primary",
        }}
      >
        {step.text}
      </Typography>
    );
  }

  if (step.kind === "ask") {
    return (
      <AskUserQuestionCard
        question={step.question}
        onSubmit={step.onSubmit}
        onSkip={step.onSkip}
        resolved={step.resolved}
        answerText={step.answerText}
      />
    );
  }

  if (step.kind === "file") {
    return (
      <QuietRow role="success">
        <Typography sx={{ typography: "s1", fontFamily: MONO, flexShrink: 0, maxWidth: "60%", color: "text.primary" }}>
          {step.path}
        </Typography>
        <Typography noWrap sx={{ typography: "s1", color: "text.secondary", flex: 1, minWidth: 0 }}>
          {step.note}
        </Typography>
      </QuietRow>
    );
  }

  if (step.kind === "json") {
    return (
      <Box>
        <Stack
          direction="row" alignItems="center" spacing={1}
          onClick={() => setOpen((o) => !o)}
          sx={{ py: 0.5, cursor: "pointer", borderRadius: "4px", px: 1, "&:hover": { bgcolor: "action.hover" } }}
        >
          <Iconify
            icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
            width={12}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s1", fontFamily: MONO, color: "text.secondary" }}>
            {step.label}
          </Typography>
        </Stack>
        <Collapse in={open} unmountOnExit>
          <Typography
            sx={{
              ml: 3, mt: 0.5, px: 1.5, py: 1, borderRadius: "4px",
              typography: "s1", color: "text.secondary", whiteSpace: "pre-wrap",
              fontFamily: MONO, bgcolor: "background.neutral",
            }}
          >
            {step.value}
          </Typography>
        </Collapse>
      </Box>
    );
  }

  // "Run activity · N updates" — a collapsible fold of consecutive reading/stage
  // events, so the tool chatter stays quiet until the reader opens it.
  if (step.kind === "group") {
    return (
      <Box>
        <Stack
          direction="row" alignItems="center" spacing={1}
          onClick={() => setOpen((o) => !o)}
          sx={{ py: 0.5, cursor: "pointer", borderRadius: "4px", px: 1, "&:hover": { bgcolor: "action.hover" } }}
        >
          <Iconify
            icon={open ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"}
            width={12}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            Run activity · {step.count} update{step.count === 1 ? "" : "s"}
          </Typography>
        </Stack>
        <Collapse in={open} unmountOnExit>
          <Stack spacing={0.25} sx={{ ml: 3, mt: 0.5 }}>
            {(step.lines || []).map((line, i) => (
              <Typography key={i} sx={{ typography: "s1", color: "text.secondary", fontFamily: MONO }}>
                {line}
              </Typography>
            ))}
          </Stack>
        </Collapse>
      </Box>
    );
  }

  if (step.kind === "error") {
    return (
      <QuietRow role="danger">
        <Typography sx={{ typography: "s1", color: "text.primary", flex: 1, minWidth: 0 }}>
          {step.text}
        </Typography>
        {step.onRetry && (
          <Typography
            component="button"
            onClick={step.onRetry}
            sx={{
              flexShrink: 0, border: "none", background: "none", cursor: "pointer",
              typography: "s1", fontWeight: 500, color: "text.primary",
              textDecoration: "underline",
              "&:hover": { color: "text.secondary" },
            }}
          >
            Retry
          </Typography>
        )}
      </QuietRow>
    );
  }

  if (step.kind === "cancelled") {
    return (
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 0.5, py: 0.25 }}>
        <Iconify icon="solar:close-circle-linear" width={14} sx={{ color: "text.secondary", flexShrink: 0 }} />
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>{step.text || "Stopped."}</Typography>
      </Stack>
    );
  }

  if (step.kind === "completed") {
    return (
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 0.5, py: 0.25 }}>
        <Iconify icon="solar:check-circle-bold" width={14} sx={{ color: "status.success.solid", flexShrink: 0 }} />
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>{step.text || "Done."}</Typography>
      </Stack>
    );
  }

  if (step.kind === "heartbeat") {
    return <HeartbeatRow since={step.since} />;
  }

  if (step.kind === "nudge-line") {
    return (
      <Typography
        sx={{ typography: "s1", color: "text.secondary", px: 0.5, py: 0.25 }}
      >
        {step.text}
      </Typography>
    );
  }

  return <ToolStep step={step} />;
}
Step.propTypes = { step: STEP_SHAPE };

// Pretty-print a tool result that is JSON; anything else is shown as written.
function formatToolOutput(text) {
  try {
    const parsed = JSON.parse(text);
    return parsed !== null && typeof parsed === "object" ? JSON.stringify(parsed, null, 2) : text;
  } catch {
    return text;
  }
}

// A tool call — a quiet reading row that goes amber while running, green on
// success, red on failure; the running dot pulses. Like Claude's tool rows, a
// finished call is collapsed to a one-line preview and opens to its full output
// on click (or Enter/Space). There are no inputs to show: the conversation
// events carry only the tool name and its result.
function ToolStep({ step }) {
  const [open, setOpen] = useState(false);
  const role = TOOL_ROLE[step.state] || "success";
  const running = step.state === "running";
  const expandable = !running && !!step.result;
  // Formatted once per result: every turn re-renders on each keystroke and poll.
  const output = useMemo(
    () => (expandable ? formatToolOutput(step.result) : null),
    [expandable, step.result],
  );

  const row = (
    <QuietRow role={role} pulse={running}>
      <Typography sx={{ typography: "s1", fontFamily: MONO, flexShrink: 0, maxWidth: "60%", color: "text.primary" }}>
        {step.label}
      </Typography>
      <Typography noWrap sx={{ typography: "s1", color: "text.secondary", flex: 1, minWidth: 0 }}>
        {running ? "…" : step.result}
      </Typography>
      {expandable && (
        <Iconify
          className="tool-step-chevron"
          icon="solar:alt-arrow-down-linear"
          width={12}
          sx={{
            flexShrink: 0,
            color: "text.secondary",
            opacity: open ? 1 : 0,
            transform: open ? "rotate(180deg)" : "none",
            transition: (t) => t.transitions.create(["opacity", "transform"], { duration: t.transitions.duration.shorter }),
          }}
        />
      )}
    </QuietRow>
  );

  if (!expandable) return row;

  const toggle = () => setOpen((o) => !o);
  return (
    <Box>
      <Box
        role="button"
        tabIndex={0}
        aria-expanded={open}
        // Named by the tool alone; otherwise the whole result preview becomes
        // the button's name.
        aria-label={step.label}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        sx={{
          cursor: "pointer",
          borderRadius: "4px",
          outline: "none",
          "&:hover, &:focus-visible": { bgcolor: "action.hover" },
          "&:hover .tool-step-chevron, &:focus-visible .tool-step-chevron": { opacity: 1 },
        }}
      >
        {row}
      </Box>
      <Collapse in={open} unmountOnExit>
        <Typography
          data-testid="tool-step-output"
          sx={{
            ml: 3, mt: 0.5, mb: 0.5, px: 1.5, py: 1, borderRadius: "4px",
            typography: "s1", fontFamily: MONO, color: "text.secondary",
            whiteSpace: "pre-wrap", overflowWrap: "anywhere",
            maxHeight: 320, overflowY: "auto",
            bgcolor: "background.neutral", border: "1px solid", borderColor: "divider",
          }}
        >
          {output}
        </Typography>
      </Collapse>
    </Box>
  );
}
ToolStep.propTypes = { step: STEP_SHAPE };

/* ── markdown prose ──────────────────────────────────────────────────────── */

function Markdown({ text, dim }) {
  return (
    <Box
      sx={{
        typography: "m3",
        color: dim ? "text.secondary" : "text.primary",
        "& p": { m: 0, mb: 1 },
        "& p:last-of-type": { mb: 0 },
        "& ul, & ol": { m: 0, mb: 1, pl: 3 },
        "& code": { fontFamily: MONO, fontSize: "0.85em" },
        "& a": { color: "text.primary", textDecoration: "underline" },
        // Code keeps its lines and tables keep whole words, so both scroll
        // sideways inside their own box rather than widening the chat.
        "& pre": {
          m: 0, mb: 1, px: 1.5, py: 1, borderRadius: "4px",
          bgcolor: "background.neutral", overflowX: "auto", maxWidth: "100%",
        },
        "& table": {
          display: "block", maxWidth: "100%", overflowX: "auto",
          borderCollapse: "collapse", mb: 1,
        },
        "& th, & td": {
          px: 1, py: 0.5, border: "1px solid", borderColor: "divider",
          textAlign: "left", overflowWrap: "normal",
        },
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
        {text || ""}
      </ReactMarkdown>
    </Box>
  );
}
Markdown.propTypes = { text: PropTypes.string, dim: PropTypes.bool };

/* ── step-row primitive ──────────────────────────────────────────────────── */

// A status dot on the left, whatever the caller passes on the right. No
// background fill, no border — repeated in a row it reads as a list of results,
// not a stack of chips. `role` is a status role (theme.palette.status);
// `pulse` animates the dot for an in-flight tool call.
export function QuietRow({ role, pulse, children }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 0.5, py: 0.25 }}>
      <Box
        sx={{
          width: 8, height: 8, borderRadius: "50%", flexShrink: 0, bgcolor: `status.${role}.solid`,
          ...(pulse && {
            animation: "quietPulse 1.1s ease-in-out infinite",
            "@keyframes quietPulse": { "0%,100%": { opacity: 0.35 }, "50%": { opacity: 1 } },
            "@media (prefers-reduced-motion: reduce)": { animation: "none" },
          }),
        }}
      />
      {children}
    </Stack>
  );
}
QuietRow.propTypes = {
  role: PropTypes.oneOf(["success", "warning", "danger", "info", "neutral", "discovery"]),
  pulse: PropTypes.bool,
  children: PropTypes.node,
};

/* ── working indicator ───────────────────────────────────────────────────── */

export function Working({ label }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Box
        sx={{
          width: 8, height: 8, borderRadius: "50%", bgcolor: "text.secondary",
          animation: "pulse 1.2s ease-in-out infinite",
          "@keyframes pulse": { "0%,100%": { opacity: 0.3 }, "50%": { opacity: 1 } },
        }}
      />
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>{label}</Typography>
    </Stack>
  );
}
Working.propTypes = { label: PropTypes.string };

/* ── autonomous heartbeat ────────────────────────────────────────────────── */

function elapsedLabel(sinceIso) {
  if (!sinceIso) return null;
  const ms = Date.now() - Date.parse(sinceIso);
  if (Number.isNaN(ms) || ms < 0) return null;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function HeartbeatRow({ since }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = elapsedLabel(since);
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 0.5, py: 0.25 }}>
      <Box
        sx={{
          width: 8, height: 8, borderRadius: "50%", bgcolor: "status.info.solid",
          animation: "pulse 1.2s ease-in-out infinite",
          "@keyframes pulse": { "0%,100%": { opacity: 0.3 }, "50%": { opacity: 1 } },
        }}
      />
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        ALK is working autonomously{elapsed ? ` · ${elapsed}` : ""}
      </Typography>
    </Stack>
  );
}
HeartbeatRow.propTypes = { since: PropTypes.string };
