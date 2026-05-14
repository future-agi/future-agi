import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  IconButton,
  Stack,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import CellMarkdown from "src/sections/common/CellMarkdown";
import useVoiceAudioStore from "./voiceAudioStore";
import { computeTotals, enrichTurns, formatClock } from "./transcriptUtils";

// Highlight query matches in a string — returns React nodes.
const highlightMatches = (text, query) => {
  if (!query) return text;
  const q = query.trim();
  if (!q) return text;
  const parts = [];
  let idx = 0;
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  while (idx < text.length) {
    const found = lower.indexOf(needle, idx);
    if (found === -1) {
      parts.push(text.slice(idx));
      break;
    }
    if (found > idx) parts.push(text.slice(idx, found));
    parts.push(
      <Box
        key={`m-${found}`}
        component="mark"
        sx={{
          bgcolor: "warning.lighter",
          color: "warning.darker",
          px: "2px",
          borderRadius: "2px",
        }}
      >
        {text.slice(found, found + needle.length)}
      </Box>,
    );
    idx = found + needle.length;
  }
  return <>{parts}</>;
};

// ─────────────────────────────────────────────────────────────────────────────
// Speaker colors (theme-aware via palette lookup)
// ─────────────────────────────────────────────────────────────────────────────
const useSpeakerColors = () => {
  const theme = useTheme();
  return useMemo(
    () => ({
      assistant:
        theme.palette.mode === "dark"
          ? theme.palette.primary.light
          : theme.palette.primary.main,
      user: theme.palette.mode === "dark" ? "#FF9933" : "#E9690C",
      system: theme.palette.mode === "dark" ? "#a78bfa" : "#7c3aed",
      tool: theme.palette.mode === "dark" ? "#fbbf24" : "#d97706",
      unknown: theme.palette.text.disabled,
    }),
    [theme],
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Header — talk ratio + speaker timeline strip
// ─────────────────────────────────────────────────────────────────────────────

// Fixed order matches the audio player's track order (top → bottom:
// customer, assistant) so each wave's color visibly lines up with the
// same role in this legend. Any other role still shows up but trails.
const TALK_ROLE_ORDER = ["user", "assistant", "system", "tool"];

// Display labels for the TALK RATIO legend. Internal role keys (`user`,
// `assistant`, …) don't always read naturally — the search filter tabs
// above display `user` as "Customer", so the legend uses the same
// mapping for visual consistency. Override with the `roleLabels` prop.
const DEFAULT_ROLE_LABELS = {
  user: "Customer",
  assistant: "Assistant",
  system: "System",
  tool: "Tool",
};

const TalkRatioBar = ({
  totals,
  colors,
  hideLabel = false,
  hidePercentages = false,
  legendAlign = "left",
  roleLabels = DEFAULT_ROLE_LABELS,
}) => {
  const total = totals.total || 1;
  const segments = Object.entries(totals.byRole)
    .filter(([, v]) => v > 0)
    .sort((a, b) => {
      const ia = TALK_ROLE_ORDER.indexOf(a[0]);
      const ib = TALK_ROLE_ORDER.indexOf(b[0]);
      const ra = ia === -1 ? TALK_ROLE_ORDER.length : ia;
      const rb = ib === -1 ? TALK_ROLE_ORDER.length : ib;
      return ra - rb;
    });

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ minWidth: 0, width: "100%" }}
    >
      {!hideLabel && (
        <Typography
          sx={{
            fontSize: 10,
            fontWeight: 600,
            color: "text.secondary",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          Talk ratio
        </Typography>
      )}
      <Stack
        direction="row"
        gap={1.25}
        sx={{
          flexWrap: "wrap",
          ...(legendAlign === "right" ? { ml: "auto" } : {}),
        }}
      >
        {segments.map(([role, val]) => (
          <Stack key={role} direction="row" alignItems="center" gap={0.5}>
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: "2px",
                bgcolor: colors[role] || colors.unknown,
              }}
            />
            <Typography
              sx={{
                fontSize: 10,
                color: "text.secondary",
                textTransform: "capitalize",
              }}
            >
              {(() => {
                const label = roleLabels?.[role] || role;
                return hidePercentages
                  ? label
                  : `${label} ${Math.round((val / total) * 100)}%`;
              })()}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Stack>
  );
};

TalkRatioBar.propTypes = {
  totals: PropTypes.object.isRequired,
  colors: PropTypes.object.isRequired,
  hideLabel: PropTypes.bool,
  hidePercentages: PropTypes.bool,
  legendAlign: PropTypes.oneOf(["left", "right"]),
  roleLabels: PropTypes.object,
};

const SpeakerTimelineStrip = ({
  turns,
  colors,
  duration,
  currentTime,
  onSeek,
}) => {
  // Use explicit duration from audio when we have it; otherwise fall back to
  // the last turn's end so the strip still renders before audio loads.
  const dur = useMemo(() => {
    if (duration && duration > 0) return duration;
    for (let i = turns.length - 1; i >= 0; i--) {
      if (turns[i].end != null) return turns[i].end;
    }
    return 0;
  }, [duration, turns]);

  const stripRef = useRef(null);

  const handleClick = (e) => {
    if (!dur || !stripRef.current) return;
    const rect = stripRef.current.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    const t = Math.max(0, Math.min(dur, pct * dur));
    onSeek?.(t);
  };

  if (!dur || turns.length === 0) return null;

  const playheadPct =
    dur > 0 ? Math.max(0, Math.min(100, ((currentTime || 0) / dur) * 100)) : 0;

  return (
    <Box
      ref={stripRef}
      onClick={handleClick}
      sx={{
        position: "relative",
        height: 14,
        bgcolor: "background.neutral",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "3px",
        cursor: "pointer",
        overflow: "hidden",
      }}
    >
      {turns.map((t) => {
        if (t.start == null || t.end == null) return null;
        const left = (t.start / dur) * 100;
        const width = Math.max(0.4, ((t.end - t.start) / dur) * 100);
        return (
          <Tooltip
            key={t.id}
            title={`${t.role} · ${formatClock(t.start)}`}
            arrow
            placement="top"
          >
            <Box
              sx={{
                position: "absolute",
                top: 2,
                bottom: 2,
                left: `${left}%`,
                width: `${width}%`,
                bgcolor: colors[t.role] || colors.unknown,
                opacity: 0.75,
                borderRadius: "2px",
                transition: "opacity 120ms",
                "&:hover": { opacity: 1 },
              }}
            />
          </Tooltip>
        );
      })}
      {/* Playhead */}
      <Box
        sx={{
          position: "absolute",
          top: -2,
          bottom: -2,
          left: `${playheadPct}%`,
          width: "2px",
          bgcolor: "text.primary",
          boxShadow: "0 0 0 1px rgba(0,0,0,0.2)",
          pointerEvents: "none",
        }}
      />
    </Box>
  );
};

SpeakerTimelineStrip.propTypes = {
  turns: PropTypes.array.isRequired,
  colors: PropTypes.object.isRequired,
  duration: PropTypes.number,
  currentTime: PropTypes.number,
  onSeek: PropTypes.func,
};

// ─────────────────────────────────────────────────────────────────────────────
// Turn row
// ─────────────────────────────────────────────────────────────────────────────

const TurnRow = React.forwardRef(
  (
    { turn, colors, query, isPlaying, isFocused, onSeek, onCopy, onAnnotate },
    ref,
  ) => {
    const color = colors[turn.role] || colors.unknown;

    return (
      <Box
        ref={ref}
        onClick={() => {
          if (turn.start != null) onSeek?.(turn.start);
        }}
        sx={{
          position: "relative",
          pl: 1.25,
          pr: 1,
          py: 0.75,
          borderBottom: "1px solid",
          borderColor: "divider",
          cursor: turn.start != null ? "pointer" : "default",
          bgcolor: isPlaying
            ? "rgba(123, 86, 219, 0.08)"
            : isFocused
              ? "action.hover"
              : "transparent",
          transition: "background-color 80ms",
          "&:hover": {
            bgcolor: isPlaying ? "rgba(123, 86, 219, 0.12)" : "action.hover",
          },
          "&:hover .turn-actions": { opacity: 1 },
          "&::before": {
            content: '""',
            position: "absolute",
            left: 0,
            top: 6,
            bottom: 6,
            width: "3px",
            borderRadius: "2px",
            bgcolor: color,
            opacity: isPlaying ? 1 : 0.7,
          },
        }}
      >
        {/* Meta row: timestamp · duration · actions */}
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ minHeight: 16 }}
        >
          {turn.start != null && (
            <Typography
              sx={{
                fontFamily: "monospace",
                fontSize: 10,
                color: "text.secondary",
                px: 0.5,
                borderRadius: "2px",
                pointerEvents: "none",
              }}
            >
              {formatClock(turn.start)}
            </Typography>
          )}

          {turn.duration != null && turn.duration > 0 && (
            <Typography
              sx={{
                fontFamily: "monospace",
                fontSize: 9.5,
                color: "text.disabled",
              }}
            >
              {turn.duration.toFixed(1)}s
            </Typography>
          )}

          {turn.overlapsPrev && (
            <Tooltip
              title="Interruption — started before previous turn ended"
              arrow
            >
              <Box
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "2px",
                  px: 0.5,
                  bgcolor: "error.lighter",
                  color: "error.dark",
                  borderRadius: "2px",
                  fontSize: 9,
                  fontWeight: 600,
                  letterSpacing: "0.02em",
                  textTransform: "uppercase",
                }}
              >
                <Iconify icon="mdi:alert-circle" width={10} />
                interrupt
              </Box>
            </Tooltip>
          )}

          <Box sx={{ flex: 1 }} />

          {/* Hover actions */}
          <Stack
            direction="row"
            spacing={0.25}
            className="turn-actions"
            sx={{ opacity: 0, transition: "opacity 120ms" }}
            onClick={(e) => e.stopPropagation()}
          >
            <Tooltip title="Copy turn" arrow>
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  onCopy?.(turn);
                }}
                sx={{ width: 22, height: 22 }}
              >
                <Iconify icon="tabler:copy" width={12} />
              </IconButton>
            </Tooltip>
            {onAnnotate && (
              <Tooltip title="Annotate turn" arrow>
                <IconButton
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    onAnnotate?.(turn);
                  }}
                  sx={{ width: 22, height: 22 }}
                >
                  <Iconify icon="mdi:comment-text-outline" width={12} />
                </IconButton>
              </Tooltip>
            )}
          </Stack>
        </Stack>

        {/* Content */}
        <Box
          sx={{
            mt: 0.25,
            fontSize: 12.5,
            lineHeight: 1.45,
            color: "text.primary",
            "& p": { m: 0 },
            "& p + p": { mt: 0.5 },
            wordBreak: "break-word",
          }}
        >
          {query ? (
            <Typography
              sx={{ fontSize: 12.5, lineHeight: 1.45, whiteSpace: "pre-wrap" }}
            >
              {highlightMatches(turn.content, query)}
            </Typography>
          ) : (
            <CellMarkdown spacing={0} text={turn.content} />
          )}
        </Box>
      </Box>
    );
  },
);

TurnRow.displayName = "TurnRowInner";
TurnRow.propTypes = {
  turn: PropTypes.object.isRequired,
  colors: PropTypes.object.isRequired,
  query: PropTypes.string,
  isPlaying: PropTypes.bool,
  isFocused: PropTypes.bool,
  onSeek: PropTypes.func,
  onCopy: PropTypes.func,
  onAnnotate: PropTypes.func,
};

// Memoized wrapper — with ~40 turns and a 60Hz currentTime poll, the list
// re-renders roughly once per animation frame. Without memoization each
// re-render touches every row's CellMarkdown tree, which causes the visible
// flicker. Only compare the fields that actually affect what's rendered.
const MemoTurnRow = React.memo(TurnRow, (prev, next) => {
  if (prev.turn !== next.turn) return false;
  if (prev.isPlaying !== next.isPlaying) return false;
  if (prev.isFocused !== next.isFocused) return false;
  if (prev.query !== next.query) return false;
  if (prev.colors !== next.colors) return false;
  if (prev.onSeek !== next.onSeek) return false;
  if (prev.onCopy !== next.onCopy) return false;
  if (prev.onAnnotate !== next.onAnnotate) return false;
  return true;
});

// ─────────────────────────────────────────────────────────────────────────────
// Silence divider
// ─────────────────────────────────────────────────────────────────────────────

const SilenceGap = ({ seconds }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 1,
      pl: 2,
      pr: 1.5,
      py: 0.25,
      bgcolor: "background.default",
      borderBottom: "1px dashed",
      borderColor: "divider",
    }}
  >
    <Iconify
      icon="mdi:timer-sand-empty"
      width={10}
      sx={{ color: "text.disabled" }}
    />
    <Typography
      sx={{ fontSize: 9.5, color: "text.disabled", fontFamily: "monospace" }}
    >
      {seconds.toFixed(1)}s silence
    </Typography>
    <Box sx={{ flex: 1, borderTop: "1px dashed", borderColor: "divider" }} />
  </Box>
);

SilenceGap.propTypes = { seconds: PropTypes.number.isRequired };

// ─────────────────────────────────────────────────────────────────────────────
// Main TranscriptView
// ─────────────────────────────────────────────────────────────────────────────

const FILTERS = [
  { id: "all", label: "All" },
  { id: "assistant", label: "Assistant" },
  { id: "user", label: "Customer" },
];

const TranscriptView = ({
  transcript,
  onAnnotate,
  // Header-row customization hooks used by the chat drawer to repurpose
  // the voice TalkRatioBar as a compact speaker legend:
  //   - hideTimelineStrip: drop the orange/white bar below the TALK RATIO
  //     row (chat doesn't have a speaker timeline derived from audio).
  //   - hideTalkRatioPercentages: render only colored-dot + role label
  //     (no percentage text) — keeps the row reading as a legend, not
  //     a stats row.
  //   - talkRatioLegendAlign: "right" pushes the legend chips to the end
  //     of the row so TALK RATIO sits left-aligned with the legend on
  //     the opposite side.
  hideTimelineStrip = false,
  hideTalkRatioLabel = false,
  hideTalkRatioPercentages = false,
  talkRatioLegendAlign = "left",
  // "Silence" is a voice concept (dead air between speaker turns). For
  // chat transcripts the gap between messages is just response latency,
  // not silence — so the chat drawer passes true here to suppress the
  // inline silence dividers.
  hideSilenceMarkers = false,
}) => {
  const colors = useSpeakerColors();

  const seekTo = useVoiceAudioStore((s) => s.seekTo);
  const currentTime = useVoiceAudioStore((s) => s.currentTime);
  const duration = useVoiceAudioStore((s) => s.duration);

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [focusIdx, setFocusIdx] = useState(-1);
  // autoScroll: true → the list follows the playing row as audio advances.
  // Turned off the moment the user scrolls/wheels/keys; re-enabled when the
  // user clicks a row, clicks "Follow playback", or jumps via the timeline.
  const [autoScroll, setAutoScroll] = useState(true);

  const turns = useMemo(() => enrichTurns(transcript), [transcript]);

  const totals = useMemo(() => computeTotals(turns), [turns]);

  const filteredTurns = useMemo(() => {
    const q = query.trim().toLowerCase();
    return turns.filter((t) => {
      if (filter === "assistant" && t.role !== "assistant") return false;
      if (filter === "user" && t.role !== "user") return false;
      if (t.role === "system") return false;
      if (q && !t.content.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [turns, query, filter]);

  // Which turn is currently playing based on currentTime?
  //
  // Logic: prefer the turn whose [start, end] window contains the cursor.
  // Fall back to "the latest turn whose start has been reached" so the
  // highlight never briefly disappears in the silence between two turns.
  // Using start-only (not requiring end) avoids the flicker of toggling
  // back to -1 at every turn boundary.
  const playingIdx = useMemo(() => {
    if (currentTime == null || filteredTurns.length === 0) return -1;
    // Nothing has started yet — no highlight.
    const firstStart = filteredTurns.find((t) => t.start != null)?.start;
    if (firstStart == null || currentTime + 0.05 < firstStart) return -1;

    let latest = -1;
    for (let i = 0; i < filteredTurns.length; i++) {
      const t = filteredTurns[i];
      if (t.start == null) continue;
      if (t.start <= currentTime + 0.05) {
        latest = i;
      } else {
        // Turns are sorted by start; first non-match means we're done.
        break;
      }
    }
    return latest;
  }, [filteredTurns, currentTime]);

  // Keyboard nav (j/k)
  useEffect(() => {
    const onKey = (e) => {
      if (
        e.target.tagName === "INPUT" ||
        e.target.tagName === "TEXTAREA" ||
        e.target.isContentEditable
      )
        return;
      if (e.key === "j" || e.key === "J") {
        e.preventDefault();
        setFocusIdx((i) => Math.min(filteredTurns.length - 1, i + 1));
      } else if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        setFocusIdx((i) => Math.max(0, i - 1));
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [filteredTurns.length]);

  // Scroll focused row into view (j/k nav overrides autoScroll — it's an
  // explicit navigation action)
  const rowRefs = useRef([]);
  const listRef = useRef(null);
  useEffect(() => {
    if (focusIdx >= 0) {
      rowRefs.current[focusIdx]?.scrollIntoView({
        block: "nearest",
        behavior: "smooth",
      });
    }
  }, [focusIdx]);

  // Auto-scroll to playing row — debounced so that rapid playingIdx churn
  // (e.g. the currentTime poller nudging by fractions of a second) does not
  // trigger a scrollIntoView on every frame. We only actually scroll once
  // the value has been stable for ~250ms, which matches how a human reads.
  const lastScrolledIdxRef = useRef(-1);
  useEffect(() => {
    if (!autoScroll) return;
    if (playingIdx < 0) return;
    if (playingIdx === lastScrolledIdxRef.current) return;
    const handle = setTimeout(() => {
      if (lastScrolledIdxRef.current === playingIdx) return;
      lastScrolledIdxRef.current = playingIdx;
      rowRefs.current[playingIdx]?.scrollIntoView({
        block: "center",
        behavior: "smooth",
      });
    }, 250);
    return () => clearTimeout(handle);
  }, [playingIdx, autoScroll]);

  // When the transcript source changes (user jumped to a different
  // recording), move the view to wherever the audio is now — but only if
  // autoScroll is still active. If the user had already disabled autoScroll
  // on the previous call, preserve that preference across the jump.
  useEffect(() => {
    lastScrolledIdxRef.current = -1;
    if (!autoScroll) return;
    if (playingIdx >= 0) {
      rowRefs.current[playingIdx]?.scrollIntoView({
        block: "center",
        behavior: "auto",
      });
      lastScrolledIdxRef.current = playingIdx;
    } else {
      listRef.current?.scrollTo?.({ top: 0, behavior: "auto" });
    }
    // Intentionally depending on `transcript` identity, not playingIdx, so
    // this fires exactly once per new call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcript]);

  // Detect user-initiated scroll intent. Using wheel/touch/keydown (rather
  // than the scroll event) lets us distinguish manual scrolling from the
  // programmatic scrollIntoView calls above without fragile flag passing.
  const handleUserScrollIntent = useCallback(() => {
    setAutoScroll(false);
  }, []);

  const handleListKeyDown = useCallback((e) => {
    if (
      ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"].includes(
        e.key,
      )
    ) {
      setAutoScroll(false);
      // Transcript owns arrow-scroll when focused — don't bubble to the
      // drawer-level ArrowUp/Down = prev/next-call listener.
      if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.stopPropagation();
      }
    }
  }, []);

  const handleSeek = useCallback(
    (time) => {
      if (typeof time !== "number" || Number.isNaN(time)) return;
      // Clicking a row (or the timeline strip) is an explicit action — resume
      // auto-follow so the next highlighted row is brought into view.
      setAutoScroll(true);
      seekTo(time);
    },
    [seekTo],
  );

  const handleResumeFollow = useCallback(() => {
    setAutoScroll(true);
    if (playingIdx >= 0) {
      rowRefs.current[playingIdx]?.scrollIntoView({
        block: "center",
        behavior: "smooth",
      });
    }
  }, [playingIdx]);

  const handleCopyTurn = useCallback((turn) => {
    const text = `[${formatClock(turn.start)}] ${turn.rawRole || turn.role}: ${turn.content}`;
    navigator.clipboard.writeText(text).then(() => {
      enqueueSnackbar("Turn copied", {
        variant: "info",
        autoHideDuration: 1200,
      });
    });
  }, []);

  if (turns.length === 0) {
    return null;
  }

  return (
    <Stack
      gap={1}
      sx={{
        width: "100%",
        minWidth: 0,
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Header: talk ratio + timeline strip — flat, no wrapper border.
          Chat drawer hides the timeline strip (no audio-backed speaker
          timeline) and uses the talk-ratio row as a compact legend. */}
      <Stack gap={0.75} sx={{ flexShrink: 0 }}>
        <TalkRatioBar
          totals={totals}
          colors={colors}
          hideLabel={hideTalkRatioLabel}
          hidePercentages={hideTalkRatioPercentages}
          legendAlign={talkRatioLegendAlign}
        />
        {!hideTimelineStrip && (
          <SpeakerTimelineStrip
            turns={turns}
            colors={colors}
            duration={duration}
            currentTime={currentTime}
            onSeek={handleSeek}
          />
        )}
      </Stack>

      {/* Toolbar: search + filter pills */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ flexShrink: 0 }}
      >
        <Box
          sx={{
            flex: 1,
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            px: 1,
            py: 0.25,
            bgcolor: "background.paper",
          }}
        >
          <Iconify icon="mdi:magnify" width={13} color="text.disabled" />
          <Box
            component="input"
            placeholder="Search transcript"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            sx={{
              flex: 1,
              border: "none",
              outline: "none",
              bgcolor: "transparent",
              fontSize: 11,
              color: "text.primary",
              fontFamily: "inherit",
              py: 0.25,
              "&::placeholder": { color: "text.disabled" },
            }}
          />
          {query && (
            <Iconify
              icon="mdi:close"
              width={12}
              onClick={() => setQuery("")}
              sx={{
                cursor: "pointer",
                color: "text.disabled",
                "&:hover": { color: "text.primary" },
              }}
            />
          )}
        </Box>

        <Box
          sx={{
            display: "inline-flex",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            overflow: "hidden",
            bgcolor: "background.paper",
          }}
        >
          {FILTERS.map((f) => (
            <ButtonBase
              key={f.id}
              onClick={() => setFilter(f.id)}
              sx={{
                px: 1,
                py: 0.4,
                fontSize: 11,
                fontWeight: filter === f.id ? 600 : 400,
                color: filter === f.id ? "text.primary" : "text.secondary",
                bgcolor: filter === f.id ? "background.neutral" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
                borderRight:
                  f.id !== FILTERS[FILTERS.length - 1].id
                    ? "1px solid"
                    : "none",
                borderColor: "divider",
              }}
            >
              {f.label}
            </ButtonBase>
          ))}
        </Box>

        <Tooltip title="Press J/K to navigate turns" arrow placement="top">
          <Iconify
            icon="mdi:keyboard-outline"
            width={14}
            sx={{ color: "text.disabled" }}
          />
        </Tooltip>
      </Stack>

      {/* Turns list — no wrapper border, rows handle their own dividers */}
      <Box
        ref={listRef}
        tabIndex={0}
        onWheel={handleUserScrollIntent}
        onTouchMove={handleUserScrollIntent}
        onKeyDown={handleListKeyDown}
        sx={{
          position: "relative",
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          outline: "none",
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        {filteredTurns.length === 0 ? (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              minHeight: 120,
            }}
          >
            <Typography sx={{ fontSize: 11, color: "text.disabled" }}>
              No matching turns
            </Typography>
          </Box>
        ) : (
          filteredTurns.map((turn, i) => (
            <React.Fragment key={turn.id}>
              {!hideSilenceMarkers &&
                turn.silenceBefore != null &&
                turn.silenceBefore > 0.3 && (
                  <SilenceGap seconds={turn.silenceBefore} />
                )}
              <MemoTurnRow
                ref={(el) => (rowRefs.current[i] = el)}
                turn={turn}
                colors={colors}
                query={query}
                isPlaying={i === playingIdx}
                isFocused={i === focusIdx}
                onSeek={handleSeek}
                onCopy={handleCopyTurn}
                onAnnotate={onAnnotate}
              />
            </React.Fragment>
          ))
        )}

        {/* Follow playback pill — floats over the list when auto-scroll is
            off and something is currently playing. Click to resume follow. */}
        {!autoScroll && playingIdx >= 0 && (
          <Box
            sx={{
              position: "sticky",
              bottom: 8,
              display: "flex",
              justifyContent: "center",
              pointerEvents: "none",
              mt: 1,
              mb: 0.5,
              zIndex: 2,
            }}
          >
            <ButtonBase
              onClick={handleResumeFollow}
              sx={{
                pointerEvents: "auto",
                display: "inline-flex",
                alignItems: "center",
                gap: 0.75,
                px: 1.25,
                py: 0.5,
                bgcolor: "primary.main",
                color: "primary.contrastText",
                borderRadius: "999px",
                fontSize: 11,
                fontWeight: 600,
                boxShadow: "0 4px 16px rgba(0,0,0,0.18)",
                "&:hover": {
                  bgcolor: "primary.dark",
                },
              }}
            >
              <Iconify icon="mdi:target" width={13} />
              Follow playback
            </ButtonBase>
          </Box>
        )}
      </Box>
    </Stack>
  );
};

TranscriptView.propTypes = {
  transcript: PropTypes.array,
  onAnnotate: PropTypes.func,
  hideTimelineStrip: PropTypes.bool,
  hideTalkRatioLabel: PropTypes.bool,
  hideTalkRatioPercentages: PropTypes.bool,
  talkRatioLegendAlign: PropTypes.oneOf(["left", "right"]),
  hideSilenceMarkers: PropTypes.bool,
};

export default TranscriptView;
