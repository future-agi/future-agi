import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { format, isValid } from "date-fns";
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
import { computeDiff, matchConversationsByIndex } from "./compareUtils";
import { statusColors } from "src/utils/runStatus";

// Side-by-side baseline-vs-replay transcript with optional word-level diff.

// Speaker colors mirror VoiceDetailDrawerV2/TranscriptView (kept inline).
const useSpeakerColors = () => {
  const theme = useTheme();
  return useMemo(
    () => ({
      assistant:
        theme.palette.mode === "dark"
          ? theme.palette.primary.light
          : theme.palette.primary.main,
      user: statusColors(theme, "info").fg,
      system: theme.palette.text.secondary,
      tool: statusColors(theme, "warning").fg,
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
  const isRemoval = side === "A" && part.removed;
  const isAddition = side === "B" && part.added;

  if (!isRemoval && !isAddition) {
    return (
      <Box component="span" sx={{ whiteSpace: "pre-wrap" }}>
        {part.value}
      </Box>
    );
  }

  const bg = isRemoval ? "status.danger.bg" : "status.success.bg";
  const fg = isRemoval ? "status.danger.fg" : "status.success.fg";

  return (
    <Box
      component="span"
      sx={{
        bgcolor: bg,
        color: fg,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        fontWeight: 600,
        borderRadius: "4px",
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

// Render one column from a pair whose diff was already computed (see the
// `pairs` memo). Falls back to plain content when diffing is off or empty.
const renderSide = (pair, side, showDiff) => {
  const content = side === "A" ? pair.baselineContent : pair.replayedContent;
  if (!showDiff) return content;
  const diff = side === "A" ? pair.diffA : pair.diffB;
  if (!diff || diff.length === 0) return content;
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
          fontSize: 12,
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
              fontSize: 12,
              fontWeight: 600,
              textTransform: "none",
              letterSpacing: 0,
              color: "text.secondary",
            }}
          >
            {speakerLabel}
          </Typography>
        </Box>
        {turn.timeStamp && isValid(new Date(turn.timeStamp)) && (
          <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
            {format(new Date(turn.timeStamp), "HH:mm:ss")}
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
            borderRadius: "4px",
            bgcolor: colors[role],
          }}
        />
        <Typography
          sx={{
            fontSize: 12,
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
  const fg =
    tone === "error"
      ? "status.danger.fg"
      : tone === "success"
        ? "status.success.fg"
        : "text.secondary";
  const bg =
    tone === "error"
      ? "status.danger.bg"
      : tone === "success"
        ? "status.success.bg"
        : "action.hover";
  return (
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        fontSize: 12,
        fontWeight: 600,
        color: fg,
        bgcolor: bg,
        px: 0.75,
        py: 0.25,
        borderRadius: "8px",
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

  // Compute each pair's word-diff once (only when diffing is on) and reuse it
  // for the removal/addition counts and both render columns — no recompute.
  const pairs = useMemo(
    () =>
      matchedConversations.map((m) => {
        const baselineContent = m.baseline?.content || "";
        const replayedContent = m.replayed?.content || "";
        if (!showDiff) {
          return {
            ...m,
            baselineContent,
            replayedContent,
            diffA: null,
            diffB: null,
          };
        }
        return {
          ...m,
          baselineContent,
          replayedContent,
          diffA: computeDiff(baselineContent, replayedContent, "A"),
          diffB: computeDiff(baselineContent, replayedContent, "B"),
        };
      }),
    [matchedConversations, showDiff],
  );

  const filteredPairs = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return pairs;
    return pairs.filter(
      (m) =>
        m.baselineContent.toLowerCase().includes(q) ||
        m.replayedContent.toLowerCase().includes(q),
    );
  }, [pairs, query]);

  const { removalsCount, additionsCount } = useMemo(() => {
    if (!showDiff) return { removalsCount: 0, additionsCount: 0 };
    let removals = 0;
    let additions = 0;
    for (const p of pairs) {
      if (p.diffA) for (const part of p.diffA) if (part.removed) removals += 1;
      if (p.diffB) for (const part of p.diffB) if (part.added) additions += 1;
    }
    return { removalsCount: removals, additionsCount: additions };
  }, [pairs, showDiff]);

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
                    icon="solar:magnifier-linear"
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
                icon="solar:info-circle-linear"
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
                  fontSize: 12,
                  fontWeight: 600,
                  color: "text.primary",
                  textTransform: "none",
                  letterSpacing: 0,
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
                  fontSize: 12,
                  fontWeight: 600,
                  color: "text.primary",
                  textTransform: "none",
                  letterSpacing: 0,
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
            filteredPairs.map((match, i) => (
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
                  content={renderSide(match, "A", showDiff)}
                  isPlaceholder={!match.baseline}
                />
                <TurnCell
                  turn={match.replayed}
                  colors={colors}
                  content={renderSide(match, "B", showDiff)}
                  isPlaceholder={!match.replayed}
                />
              </Box>
            ))
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
