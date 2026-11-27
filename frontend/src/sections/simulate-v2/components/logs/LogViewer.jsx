import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  MenuItem,
  Select,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import CopyIdButton from "../CopyIdButton";
import { StatusDot } from "../primitives";
import { firstErrorOf, logText } from "../../_mock/sandboxLogs";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const SOURCES = [
  { id: "all", label: "All" },
  { id: "agent", label: "Your agent" },
  { id: "system", label: "System" },
  { id: "world", label: "World" },
];
const LEVELS = [
  { id: "all", label: "All levels" },
  { id: "warn", label: "Warnings and errors" },
  { id: "error", label: "Errors only" },
];
/* Matches the 32px selects beside it; neutral outline per the design system. */
const ICON_BTN = {
  width: 32,
  height: 32,
  flexShrink: 0,
  borderRadius: 0.75,
  border: "1px solid",
  borderColor: "divider",
  color: "text.secondary",
};
const shareOf = (ms, total) => {
  const pct = (ms / total) * 100;
  return pct < 1 ? "<1%" : `${Math.round(pct)}%`;
};
const LEVEL_RANK = { debug: 0, info: 1, warn: 2, error: 3 };
const SOURCE_WORD = { agent: "agent", system: "system", world: "world" };

const clock = (ms) =>
  new Date(ms).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
const span = (ms) => {
  if (ms == null) return "";
  const s = Math.round(ms / 1000);
  return s < 60
    ? `${s}s`
    : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
};

/* A step in one line: what its first error came to (the last line of that
   error, e.g. "KeyError: 'amount'"), else its last ordinary line. */
function summaryOf(step) {
  const lines = step.lines || [];
  const first = lines.findIndex((l) => l.level === "error");
  if (first >= 0) {
    let end = first;
    while (lines[end + 1]?.level === "error") end += 1;
    return lines[end].text.trim();
  }
  const last =
    [...lines].reverse().find((l) => l.level === "info") ||
    [...lines].reverse().find((l) => l.level !== "debug");
  return last ? last.text.replace(/^\$\s*/, "").trim() : "";
}

function StepIcon({ status }) {
  if (status === "running") return <StatusDot status="running" size={8} live />;
  const map = {
    done: ["solar:check-circle-bold", "status.success.fg"],
    failed: ["solar:close-circle-bold", "status.danger.fg"],
  };
  if (status === "pending")
    return (
      <Box
        sx={{
          width: 12,
          height: 12,
          borderRadius: "50%",
          border: "1.5px solid",
          borderColor: "divider",
        }}
      />
    );
  const [icon, color] = map[status] || map.done;
  return <Iconify icon={icon} width={14} sx={{ color }} />;
}
StepIcon.propTypes = { status: PropTypes.string };

/* The sandbox in one line, outcome first: how it ended, how long, when, how
   big, when it was removed; its ID to copy at the end. */
function SandboxLine({ sandbox: sb, live, duration }) {
  const failed = !live && sb.exitCode != null && sb.exitCode !== 0;
  const outcome = live
    ? "Running"
    : sb.exitCode == null
      ? "Ended"
      : failed
        ? `Exit ${sb.exitCode}`
        : `Exited ${sb.exitCode}`;
  const facts = [
    span(duration),
    `${clock(sb.startedAt)} → ${sb.endedAt ? clock(sb.endedAt) : "now"}`,
    `${sb.cpu} CPU · ${sb.memoryGb} GB`,
    sb.cleanedAt
      ? `cleaned up ${clock(sb.cleanedAt)}`
      : live
        ? "cleaned up when it ends"
        : null,
  ].filter(Boolean);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      flexWrap="wrap"
      useFlexGap
      sx={{ minWidth: 0 }}
    >
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <StatusDot
          status={live ? "running" : failed ? "failed" : "passed"}
          size={8}
          live={live}
        />
        <Typography
          sx={{
            typography: "s1",
            fontWeight: 600,
            color: failed ? "status.danger.fg" : "text.primary",
          }}
        >
          {outcome}
        </Typography>
      </Stack>
      <Typography
        sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}
      >
        {facts.join("  ·  ")}
      </Typography>
      <Stack direction="row" alignItems="center" spacing={0.25}>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Sandbox
        </Typography>
        <Box
          component="span"
          sx={{
            fontFamily: MONO,
            fontSize: 12,
            color: "text.secondary",
            pl: 0.5,
          }}
        >
          {sb.id}
        </Box>
        <CopyIdButton id={sb.id} what="sandbox ID" />
      </Stack>
    </Stack>
  );
}
SandboxLine.propTypes = {
  sandbox: PropTypes.object.isRequired,
  live: PropTypes.bool,
  duration: PropTypes.number,
};

function LogLine({ line, at, highlight, lineRef }) {
  const tone =
    line.level === "error"
      ? "status.danger.fg"
      : line.level === "warn"
        ? "status.warning.fg"
        : line.level === "debug"
          ? "text.disabled"
          : "text.primary";
  return (
    <Box
      ref={lineRef}
      sx={{
        display: "grid",
        gridTemplateColumns: "64px 52px minmax(0, 1fr)",
        columnGap: 1.25,
        px: 1.5,
        py: "1px",
        fontFamily: MONO,
        fontSize: 12,
        lineHeight: "18px",
        bgcolor: highlight
          ? "action.selected"
          : line.level === "error"
            ? (t) =>
                t.palette.mode === "dark"
                  ? "rgba(239,68,68,0.08)"
                  : "rgba(239,68,68,0.06)"
            : "transparent",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Box component="span" sx={{ color: "text.disabled" }}>
        {clock(at)}
      </Box>
      <Box component="span" sx={{ color: "text.tertiary" }}>
        {SOURCE_WORD[line.source] || line.source}
      </Box>
      <Box
        component="span"
        sx={{ color: tone, whiteSpace: "pre-wrap", wordBreak: "break-word" }}
      >
        {line.world && (
          <Box component="span" sx={{ color: "text.disabled", mr: 1 }}>
            {line.world}
          </Box>
        )}
        {line.text}
      </Box>
    </Box>
  );
}
LogLine.propTypes = {
  line: PropTypes.object.isRequired,
  at: PropTypes.number,
  highlight: PropTypes.bool,
  lineRef: PropTypes.func,
};

/**
 * One sandbox's log: its facts, then its steps with their lines. The failed
 * step opens on its own; a search opens every step it matches in. Live logs
 * say so and follow the newest line.
 *
 * `focusLineId` scrolls to and lights one line (a trial turn's error).
 * `hideFacts` drops the facts strip where the page above already says them.
 * `compact` fits the toolbar on one row in a narrow pane: selects for the
 * filters, icons for First error and Download.
 */
export default function LogViewer({
  log,
  focusLineId = null,
  hideFacts = false,
  compact = false,
  maxHeight = null,
  emptyText = null,
}) {
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("all");
  const [source, setSource] = useState("all");
  const [open, setOpen] = useState(() => new Set());
  const [lit, setLit] = useState(focusLineId);
  const lineRefs = useRef({});
  const boxRef = useRef(null);

  const startedAt = log?.sandbox?.startedAt || 0;
  const q = query.trim().toLowerCase();
  const showSource = (log?.steps || []).some((s) =>
    s.lines.some((l) => l.source === "agent"),
  );

  const steps = useMemo(
    () =>
      (log?.steps || []).map((s) => {
        const lines = s.lines.filter(
          (l) =>
            (level === "all" || LEVEL_RANK[l.level] >= LEVEL_RANK[level]) &&
            (source === "all" || l.source === source) &&
            (!q || l.text.toLowerCase().includes(q)),
        );
        return {
          ...s,
          shown: lines,
          /* One error per run of error lines: a traceback is one error. */
          errors: s.lines.filter(
            (l, i) => l.level === "error" && s.lines[i - 1]?.level !== "error",
          ).length,
          warns: s.lines.filter((l) => l.level === "warn").length,
          summary: summaryOf(s),
          /* A running step's bar reaches its newest line. */
          shownMs:
            s.status === "running"
              ? Math.max(0, (s.lines.at(-1)?.t ?? s.startMs) - s.startMs)
              : s.ms,
        };
      }),
    [log, level, source, q],
  );
  const totalMs = Math.max(
    1,
    ...(log?.steps || []).map((s) => s.startMs + s.ms),
  );
  /* The slowest step and its share say something only with steps to compare. */
  const showShare = (log?.steps || []).length > 2;
  /* The slowest finished step reads first: its time is bold. Only when one
     step stands out; a tie bolds nothing. */
  const finished = (log?.steps || []).filter(
    (s) => s.status === "done" || s.status === "failed",
  );
  const maxMs = Math.max(0, ...finished.map((s) => s.ms));
  const atMax = finished.filter((s) => span(s.ms) === span(maxMs));
  const slowestId = atMax.length === 1 ? atMax[0].id : null;
  const filtering = !!q || level !== "all" || source !== "all";
  const firstError = useMemo(() => firstErrorOf(log), [log]);

  /* Open the failed, running or focused step when the log arrives. */
  useEffect(() => {
    const next = new Set();
    (log?.steps || []).forEach((s) => {
      if (s.status === "failed") next.add(s.id);
      if (focusLineId && s.lines.some((l) => l.id === focusLineId))
        next.add(s.id);
    });
    if (!next.size && log?.steps?.length === 1) next.add(log.steps[0].id);
    setOpen(next);
    setLit(focusLineId);
  }, [log?.kind, log?.title, log?.sandbox?.id, focusLineId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (lit && lineRefs.current[lit])
      lineRefs.current[lit].scrollIntoView({ block: "center" });
  }, [lit, open]);

  /* A live log keeps its running step open as the build moves on. */
  const runningId = (log?.steps || []).find((s) => s.status === "running")?.id;

  /* A live log follows its newest line. */
  const lineCount = (log?.steps || []).reduce((n, s) => n + s.lines.length, 0);
  useEffect(() => {
    const box = boxRef.current;
    if (log?.live && !filtering && !lit && box)
      box.scrollTop = box.scrollHeight;
  }, [lineCount, log?.live, filtering, lit]);

  if (!log)
    return (
      <Typography sx={{ typography: "s2", color: "text.tertiary", p: 2 }}>
        {emptyText || "No log for this yet."}
      </Typography>
    );

  const toggle = (id) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const jumpToError = () => {
    if (!firstError) return;
    setQuery("");
    setLevel("all");
    setSource("all");
    setOpen((prev) => new Set(prev).add(firstError.step.id));
    setLit(firstError.line.id);
  };
  const download = () => {
    const blob = new Blob([logText(log)], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${log.sandbox?.id || "sandbox"}-${log.kind}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const levelSelect = (
    <Select
      size="small"
      value={level}
      onChange={(e) => setLevel(e.target.value)}
      sx={{ typography: "s2", height: 32, flexShrink: 0 }}
    >
      {LEVELS.map((l) => (
        <MenuItem key={l.id} value={l.id} sx={{ typography: "s2" }}>
          {l.label}
        </MenuItem>
      ))}
    </Select>
  );

  const sb = log.sandbox || {};
  const duration = (sb.endedAt || Date.now()) - sb.startedAt;

  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      {!hideFacts && (
        <SandboxLine sandbox={sb} live={!!log.live} duration={duration} />
      )}

      {compact ? (
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <FormSearchField
            searchQuery={query}
            onChange={(e) => setQuery(e.target.value)}
            size="small"
            placeholder="Search the log"
            sx={{ flex: 1, minWidth: 0 }}
          />
          {levelSelect}
          {showSource && (
            <Select
              size="small"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              sx={{ typography: "s2", height: 32, flexShrink: 0 }}
            >
              {SOURCES.map((s) => (
                <MenuItem key={s.id} value={s.id} sx={{ typography: "s2" }}>
                  {s.id === "all" ? "All sources" : s.label}
                </MenuItem>
              ))}
            </Select>
          )}
          {log.live && hideFacts && (
            <Tooltip title="Live">
              <Box sx={{ display: "flex" }}>
                <StatusDot status="running" size={7} live />
              </Box>
            </Tooltip>
          )}
          <Tooltip title={firstError ? "First error" : "No errors"}>
            <span>
              <IconButton
                size="small"
                disabled={!firstError}
                onClick={jumpToError}
                aria-label="First error"
                sx={ICON_BTN}
              >
                <Iconify icon="solar:danger-circle-linear" width={16} />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip
            title={`Download · kept ${sb.keptDays || 30} days · secrets are hidden`}
          >
            <IconButton
              size="small"
              onClick={download}
              aria-label="Download"
              sx={ICON_BTN}
            >
              <Iconify icon="solar:download-minimalistic-linear" width={16} />
            </IconButton>
          </Tooltip>
        </Stack>
      ) : (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          flexWrap="wrap"
          useFlexGap
        >
          <FormSearchField
            searchQuery={query}
            onChange={(e) => setQuery(e.target.value)}
            size="small"
            placeholder="Search the log"
            sx={{ width: 220 }}
          />
          {levelSelect}
          {showSource && (
            <ToggleButtonGroup
              size="small"
              exclusive
              value={source}
              onChange={(_, v) => v && setSource(v)}
              sx={{
                "& .MuiToggleButton-root": {
                  typography: "s2",
                  px: 1.25,
                  py: 0.25,
                  textTransform: "none",
                },
              }}
            >
              {SOURCES.map((s) => (
                <ToggleButton key={s.id} value={s.id}>
                  {s.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          )}
          <Box flex={1} />
          {log.live && hideFacts && (
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <StatusDot status="running" size={7} live />
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                Live
              </Typography>
            </Stack>
          )}
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={!firstError}
            onClick={jumpToError}
            startIcon={<Iconify icon="solar:danger-circle-linear" width={14} />}
          >
            First error
          </Button>
          <Tooltip
            title={`Kept ${sb.keptDays || 30} days · secrets are hidden`}
          >
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={download}
              startIcon={
                <Iconify icon="solar:download-minimalistic-linear" width={14} />
              }
            >
              Download
            </Button>
          </Tooltip>
        </Stack>
      )}

      <Box
        ref={boxRef}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "auto",
          ...(maxHeight && { maxHeight }),
        }}
      >
        {steps.map((s, i) => {
          const isOpen =
            open.has(s.id) ||
            s.id === runningId ||
            (filtering && s.shown.length > 0);
          if (filtering && !s.shown.length) return null;
          return (
            <Box
              key={s.id}
              sx={{
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <ButtonBase
                onClick={() => toggle(s.id)}
                disabled={s.status === "pending"}
                sx={{
                  width: "100%",
                  display: "grid",
                  gridTemplateColumns:
                    "12px 14px minmax(140px, 200px) minmax(0, 1fr) auto 56px",
                  alignItems: "center",
                  columnGap: 1.25,
                  px: 1.5,
                  height: 36,
                  textAlign: "left",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify
                  icon="solar:alt-arrow-right-linear"
                  width={12}
                  sx={{
                    color: "text.disabled",
                    transform: isOpen ? "rotate(90deg)" : "none",
                    transition: "transform 120ms",
                    visibility: s.status === "pending" ? "hidden" : "visible",
                  }}
                />
                <Box sx={{ width: 14, display: "grid", placeItems: "center" }}>
                  <StepIcon status={s.status} />
                </Box>
                <Typography
                  noWrap
                  sx={{
                    typography: "s1",
                    fontWeight: 500,
                    color:
                      s.status === "pending" ? "text.disabled" : "text.primary",
                  }}
                >
                  {s.label}
                </Typography>
                {/* What the step came to, so the log reads without opening it. */}
                <Typography
                  noWrap
                  title={s.summary || undefined}
                  sx={{
                    fontFamily: MONO,
                    fontSize: 12,
                    color: s.errors > 0 ? "status.danger.fg" : "text.tertiary",
                    visibility: isOpen ? "hidden" : "visible",
                  }}
                >
                  {s.summary}
                </Typography>
                <Stack direction="row" spacing={1.25} justifyContent="flex-end">
                  {s.errors > 0 && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "status.danger.fg",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {s.errors} error{s.errors === 1 ? "" : "s"}
                    </Typography>
                  )}
                  {s.warns > 0 && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "status.warning.fg",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {s.warns} warning{s.warns === 1 ? "" : "s"}
                    </Typography>
                  )}
                </Stack>
                {/* Slowest step in bold; the hover gives its share of the
                    sandbox's whole time. */}
                <Tooltip
                  title={
                    showShare && (s.status === "done" || s.status === "failed")
                      ? `${shareOf(s.ms, totalMs)} of the sandbox's ${span(totalMs)}`
                      : ""
                  }
                  placement="left"
                >
                  <Typography
                    sx={{
                      typography: "s3",
                      textAlign: "right",
                      whiteSpace: "nowrap",
                      fontVariantNumeric: "tabular-nums",
                      ...(s.id === slowestId && showShare
                        ? { color: "text.primary", fontWeight: 600 }
                        : { color: "text.tertiary" }),
                    }}
                  >
                    {s.status === "pending"
                      ? ""
                      : s.status === "running"
                        ? "now"
                        : span(s.ms)}
                  </Typography>
                </Tooltip>
              </ButtonBase>
              {isOpen && (
                <Box sx={{ pb: 1, bgcolor: "background.neutral" }}>
                  {(filtering ? s.shown : s.lines).map((l) => (
                    <LogLine
                      key={l.id}
                      line={l}
                      at={startedAt + l.t}
                      highlight={l.id === lit}
                      lineRef={(el) => {
                        lineRefs.current[l.id] = el;
                      }}
                    />
                  ))}
                  {s.status === "running" && (
                    <Typography
                      sx={{
                        fontFamily: MONO,
                        fontSize: 12,
                        color: "text.disabled",
                        px: 1.5,
                        pt: 0.5,
                      }}
                    >
                      waiting for output…
                    </Typography>
                  )}
                </Box>
              )}
            </Box>
          );
        })}
        {filtering && steps.every((s) => !s.shown.length) && (
          <Typography sx={{ typography: "s2", color: "text.tertiary", p: 2 }}>
            No lines match.
          </Typography>
        )}
      </Box>
    </Stack>
  );
}

LogViewer.propTypes = {
  log: PropTypes.object,
  focusLineId: PropTypes.string,
  hideFacts: PropTypes.bool,
  compact: PropTypes.bool,
  maxHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  emptyText: PropTypes.string,
};
