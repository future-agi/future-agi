import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import dayjs from "dayjs";
import {
  Box,
  FormControlLabel,
  InputAdornment,
  Skeleton,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  computeDiff,
  countDiffs,
  matchConversationsByIndex,
} from "./compareUtils";

/**
 * Side-by-side baseline-vs-replay transcript with optional word-level
 * diff highlighting. Replaces the legacy
 * `BasLineCompare/CompareConversation.jsx` MUI Table layout with a
 * CSS grid that matches the new chat drawer aesthetic: same row colors
 * as `TranscriptView` (role-color left border, timestamp on top, body
 * underneath) and the same Show Diff toggle behavior.
 */

// Mirrors the speaker color scheme used by
// `VoiceDetailDrawerV2/TranscriptView.useSpeakerColors`, kept inline to
// avoid coupling to the voice file. Identical color tokens so the
// per-turn left-border accent matches what users already see in the
// regular chat transcript tab.
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

// Match the label override used by `TranscriptView` so legend chips
// read "Customer / Assistant" rather than the internal "user" key.
const ROLE_LABELS = {
  user: "Customer",
  assistant: "Assistant",
  system: "System",
  tool: "Tool",
};

// ─────────────────────────────────────────────────────────────────────────────
// Diff token rendering
// ─────────────────────────────────────────────────────────────────────────────

const DiffToken = ({ part, side }) => {
  // Theme-aware foreground / background so diff spans stay readable in
  // both light and dark modes.
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const isRemoval = side === "A" && part.removed;
  const isAddition = side === "B" && part.added;

  if (!isRemoval && !isAddition) {
    return (
      <Box component="span" sx={{ whiteSpace: "pre-wrap" }}>
        {part.value}
      </Box>
    );
  }

  const bg = isRemoval
    ? isDark
      ? theme.palette.error.darker
      : theme.palette.error.lighter
    : isDark
      ? theme.palette.success.darker
      : theme.palette.success.lighter;

  const fg = isRemoval
    ? isDark
      ? theme.palette.error.main
      : theme.palette.error.darker
    : isDark
      ? theme.palette.success.main
      : theme.palette.success.darker;

  return (
    <Box
      component="span"
      sx={{
        bgcolor: bg,
        color: fg,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        fontWeight: 600,
        borderRadius: "2px",
        px: 0.25,
      }}
    >
      {part.value}
    </Box>
  );
};
DiffToken.propTypes = {
  part: PropTypes.object.isRequired,
  side: PropTypes.oneOf(["A", "B"]).isRequired,
};

const renderDiff = (textA, textB, side, showDiff) => {
  if (!showDiff) return side === "A" ? textA : textB;
  const diff = computeDiff(textA, textB, side);
  if (diff.length === 0) return side === "A" ? textA : textB;
  return diff.map((part, i) => <DiffToken key={i} part={part} side={side} />);
};

// ─────────────────────────────────────────────────────────────────────────────
// Per-turn cell — mirrors TranscriptView's TurnRow visual structure
// (timestamp + speaker chip on top, content body underneath, role-color
// left border accent). Kept lightweight: no audio sync since chat has
// no audio.
// ─────────────────────────────────────────────────────────────────────────────

const TurnCell = ({ turn, colors, content, isPlaceholder }) => {
  if (!turn || isPlaceholder) {
    return (
      <Box
        sx={{
          minHeight: 36,
          fontStyle: "italic",
          fontSize: 11,
          // `text.disabled` + opacity:0.4 was washing the placeholder
          // out completely on light backgrounds. `text.secondary` reads
          // clearly in both themes.
          color: "text.secondary",
          py: 0.75,
          px: 1.25,
          borderLeft: "2px solid",
          borderColor: "divider",
        }}
      >
        — no matching turn —
      </Box>
    );
  }

  const role = turn.role || "unknown";
  const accent = colors[role] || colors.unknown;
  const speakerLabel = ROLE_LABELS[role] || role;

  return (
    <Box
      sx={{
        py: 0.75,
        px: 1.25,
        borderLeft: "2px solid",
        borderColor: accent,
        bgcolor: "background.paper",
        borderRadius: "0 4px 4px 0",
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.25 }}>
        <Box
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 0.5,
          }}
        >
          <Box
            sx={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              bgcolor: accent,
            }}
          />
          <Typography
            sx={{
              fontSize: 10,
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              color: "text.secondary",
            }}
          >
            {speakerLabel}
          </Typography>
        </Box>
        {turn.timeStamp && dayjs(turn.timeStamp).isValid() && (
          <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
            {dayjs(turn.timeStamp).format("HH:mm:ss")}
          </Typography>
        )}
      </Stack>
      <Typography
        component="div"
        sx={{
          fontSize: 13,
          color: "text.primary",
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {content}
      </Typography>
    </Box>
  );
};
TurnCell.propTypes = {
  turn: PropTypes.object,
  colors: PropTypes.object.isRequired,
  content: PropTypes.node,
  isPlaceholder: PropTypes.bool,
};

// ─────────────────────────────────────────────────────────────────────────────
// Speaker legend — two chips in the column header, same look the chat
// transcript view uses.
// ─────────────────────────────────────────────────────────────────────────────

const SpeakerLegend = ({ colors }) => (
  <Stack direction="row" spacing={1.25}>
    {["user", "assistant"].map((role) => (
      <Stack key={role} direction="row" alignItems="center" spacing={0.5}>
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: "2px",
            bgcolor: colors[role],
          }}
        />
        <Typography
          sx={{
            fontSize: 10,
            color: "text.secondary",
            textTransform: "capitalize",
          }}
        >
          {ROLE_LABELS[role]}
        </Typography>
      </Stack>
    ))}
  </Stack>
);
SpeakerLegend.propTypes = { colors: PropTypes.object.isRequired };

const CountPill = ({ tone, count, label }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const fg = (() => {
    if (tone === "error") {
      return isDark ? theme.palette.error.main : theme.palette.error.darker;
    }
    if (tone === "success") {
      return isDark ? theme.palette.success.main : theme.palette.success.darker;
    }
    return theme.palette.text.secondary;
  })();
  const bg = (() => {
    if (tone === "error") {
      return isDark ? theme.palette.error.darker : theme.palette.error.lighter;
    }
    if (tone === "success") {
      return isDark
        ? theme.palette.success.darker
        : theme.palette.success.lighter;
    }
    return theme.palette.action.hover;
  })();
  return (
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        fontSize: 10,
        fontWeight: 600,
        color: fg,
        bgcolor: bg,
        px: 0.75,
        py: 0.25,
        borderRadius: "10px",
      }}
    >
      {label} ({count})
    </Box>
  );
};
CountPill.propTypes = {
  tone: PropTypes.oneOf(["error", "success"]).isRequired,
  count: PropTypes.number.isRequired,
  label: PropTypes.string.isRequired,
};

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

const ChatCompareTranscript = ({ data, isLoading }) => {
  const colors = useSpeakerColors();
  const [showDiff, setShowDiff] = useState(false);
  const [query, setQuery] = useState("");

  const matchedConversations = useMemo(
    () =>
      matchConversationsByIndex(data?.baselineSession, data?.replayedSession),
    [data?.baselineSession, data?.replayedSession],
  );

  const filteredPairs = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return matchedConversations;
    return matchedConversations.filter((m) => {
      const a = (m.baseline?.content || "").toLowerCase();
      const b = (m.replayed?.content || "").toLowerCase();
      return a.includes(q) || b.includes(q);
    });
  }, [matchedConversations, query]);

  const { removalsCount, additionsCount } = useMemo(() => {
    if (!showDiff) return { removalsCount: 0, additionsCount: 0 };
    return countDiffs(matchedConversations);
  }, [matchedConversations, showDiff]);

  if (isLoading) {
    return (
      <Stack gap={1}>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
        >
          <Skeleton variant="text" width={160} height={20} />
          <Skeleton variant="rectangular" width={160} height={28} />
        </Stack>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            p: 1.5,
          }}
        >
          {[0, 1, 2, 3].map((i) => (
            <Stack key={i} gap={0.5}>
              <Skeleton variant="text" width="80%" height={14} />
              <Skeleton variant="rectangular" height={48} />
              <Skeleton variant="text" width="60%" height={14} />
              <Skeleton variant="rectangular" height={48} />
            </Stack>
          ))}
        </Box>
      </Stack>
    );
  }

  if (!data || matchedConversations.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 160,
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: "4px",
        }}
      >
        <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
          No comparison transcript available
        </Typography>
      </Box>
    );
  }

  return (
    <Stack gap={1}>
      {/* Toolbar: title + counts + search + Show Diff */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ flexWrap: "wrap", rowGap: 1 }}
      >
        <Typography
          sx={{
            fontSize: 13,
            fontWeight: 600,
            color: "text.primary",
            flexShrink: 0,
          }}
        >
          Conversation comparison
        </Typography>

        {showDiff && (
          <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
            <CountPill tone="error" count={removalsCount} label="Removals" />
            <CountPill
              tone="success"
              count={additionsCount}
              label="Additions"
            />
          </Stack>
        )}

        <Box sx={{ flex: 1, minWidth: 160 }}>
          <TextField
            size="small"
            placeholder="Search both columns"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            fullWidth
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="mdi:magnify"
                    width={14}
                    sx={{ color: "text.secondary" }}
                  />
                </InputAdornment>
              ),
              sx: {
                fontSize: 12,
                height: 28,
                "& input": { p: "4px 0" },
              },
            }}
          />
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          spacing={0.25}
          sx={{ flexShrink: 0 }}
        >
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={showDiff}
                onChange={(e) => setShowDiff(e.target.checked)}
              />
            }
            label={
              <Typography
                sx={{
                  fontSize: 12,
                  color: "text.primary",
                  fontWeight: 500,
                }}
              >
                Show diff
              </Typography>
            }
            sx={{ mx: 0 }}
          />
          <Tooltip
            arrow
            placement="bottom"
            title="Highlight word-level differences between baseline and replay messages"
          >
            <Box
              component="span"
              sx={{ display: "inline-flex", alignItems: "center" }}
            >
              <Iconify
                icon="mdi:information-outline"
                width={14}
                sx={{ color: "text.disabled" }}
              />
            </Box>
          </Tooltip>
        </Stack>
      </Stack>

      {/* Side-by-side grid */}
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          overflow: "hidden",
        }}
      >
        {/* Sticky column headers */}
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            position: "sticky",
            top: 0,
            zIndex: 1,
            bgcolor: "background.default",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box
            sx={{ p: 1.25, borderRight: "1px solid", borderColor: "divider" }}
          >
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              spacing={1}
            >
              <Typography
                sx={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "text.primary",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                Baseline
              </Typography>
              <SpeakerLegend colors={colors} />
            </Stack>
          </Box>
          <Box sx={{ p: 1.25 }}>
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              spacing={1}
            >
              <Typography
                sx={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "text.primary",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}
              >
                Replay
              </Typography>
              <SpeakerLegend colors={colors} />
            </Stack>
          </Box>
        </Box>

        {/* Paired turn rows */}
        <Box
          sx={{
            maxHeight: 720,
            overflowY: "auto",
          }}
        >
          {filteredPairs.length === 0 ? (
            <Box
              sx={{
                p: 4,
                textAlign: "center",
              }}
            >
              <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
                No turns match “{query}”
              </Typography>
            </Box>
          ) : (
            filteredPairs.map((match, i) => {
              const baselineContent = match.baseline?.content || "";
              const replayedContent = match.replayed?.content || "";
              return (
                <Box
                  key={match.baseline?.id || match.replayed?.id || `pair-${i}`}
                  sx={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    columnGap: 1,
                    p: 1,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    "&:last-of-type": { borderBottom: "none" },
                  }}
                >
                  <TurnCell
                    turn={match.baseline}
                    colors={colors}
                    content={renderDiff(
                      baselineContent,
                      replayedContent,
                      "A",
                      showDiff,
                    )}
                    isPlaceholder={!match.baseline}
                  />
                  <TurnCell
                    turn={match.replayed}
                    colors={colors}
                    content={renderDiff(
                      baselineContent,
                      replayedContent,
                      "B",
                      showDiff,
                    )}
                    isPlaceholder={!match.replayed}
                  />
                </Box>
              );
            })
          )}
        </Box>
      </Box>
    </Stack>
  );
};

ChatCompareTranscript.propTypes = {
  data: PropTypes.object,
  isLoading: PropTypes.bool,
};

export default ChatCompareTranscript;
