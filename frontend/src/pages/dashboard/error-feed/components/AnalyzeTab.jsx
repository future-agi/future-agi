import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Box,
  Button,
  Chip,
  Collapse,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
  alpha,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Iconify from "src/components/iconify";
import { purple } from "src/theme/palette";
import { useErrorFeedStore } from "../store";
import { useFollowUpRunner } from "../useAnalyzeRunner";
import {
  CONF_META,
  MESSAGE_TYPE,
  RUN_STATE,
  STEP_STATUS,
  STREAM_CHARS_PER_TICK,
  STREAM_STATUS,
  STREAM_TICK_MS,
} from "../constants";

// Run-sequence definitions + makeStepMessage / buildSynthesis live in
// `../useAnalyzeRunner` now — that hook owns the actual streaming so
// both the headline card and this tab observe the same thread state.
// Follow-up Q&A is handled in-tab via useFollowUpRunner (mounted below);
// it streams a sub-agent's steps + answer + suggestion chips per question.

// ── Visual primitives ─────────────────────────────────────────────────────

const ACCENT = purple[500];

// Per-tab-session memory of which streams have already finished. Keyed by
// a caller-supplied `identityKey` (typically `${message.id}-${slot}`). Once
// a stream completes, its key is recorded here; subsequent mounts (e.g.,
// the user tabbed away and came back) start in instant mode so the
// animation doesn't replay for content they've already seen.
//
// Resets on a hard reload of the page — that's the intended scope. The
// shared analyze thread state lives in the zustand store, but stream
// completion is a UI concern that's specific to the current session.
const STREAMED_KEYS = new Set();

// Reveal `text` one chunk per tick. Reset whenever the input text changes
// (new message arrives). Returns the visible substring + whether more is
// still incoming so the caller can render a cursor.
//
// Options:
//   - instant: jump straight to the full text (review mode)
//   - identityKey: a stable id for "this particular stream". Once the
//     stream completes, the key is remembered globally so re-mounts skip
//     the animation. Without a key, the stream replays on every mount.
function useStreamingText(text, options = {}) {
  const { instant = false, identityKey } = options;
  const fullText = typeof text === "string" ? text : "";
  const skipFromMemory = !!identityKey && STREAMED_KEYS.has(identityKey);
  const shouldSkip = instant || skipFromMemory;
  const [revealedLen, setRevealedLen] = useState(
    shouldSkip ? fullText.length : 0,
  );

  useEffect(() => {
    const skip = instant || (!!identityKey && STREAMED_KEYS.has(identityKey));
    setRevealedLen(skip ? fullText.length : 0);
  }, [fullText, instant, identityKey]);

  useEffect(() => {
    if (revealedLen >= fullText.length) {
      // Stream just hit the end — remember it so we don't replay on
      // remount.
      if (fullText.length > 0 && identityKey) {
        STREAMED_KEYS.add(identityKey);
      }
      return undefined;
    }
    const id = setInterval(() => {
      setRevealedLen((n) =>
        Math.min(n + STREAM_CHARS_PER_TICK, fullText.length),
      );
    }, STREAM_TICK_MS);
    return () => clearInterval(id);
  }, [fullText, revealedLen, identityKey]);

  return {
    revealed: fullText.slice(0, revealedLen),
    isStreaming: revealedLen < fullText.length,
  };
}

// Inline blinking caret rendered at the end of streaming text — same
// visual cue every chat LLM uses to say "still typing".
function StreamCursor({ color = ACCENT }) {
  return (
    <Box
      component="span"
      sx={{
        display: "inline-block",
        width: 8,
        height: "1.05em",
        verticalAlign: "-2px",
        bgcolor: color,
        ml: 0.4,
        borderRadius: "1px",
        animation: "stream-blink 0.9s ease-in-out infinite",
        "@keyframes stream-blink": {
          "0%, 100%": { opacity: 1 },
          "50%": { opacity: 0.2 },
        },
      }}
    />
  );
}
StreamCursor.propTypes = { color: PropTypes.string };

// Compact markdown renderer for agent prose (reasoning, synthesis). The model
// emits real markdown — bold, `code`, headings, lists — so render it instead of
// dumping the raw `**`/backtick syntax. Styling inherits the caller's font via
// sx; element margins are tightened to chat density.
function AnalyzeMarkdown({
  text,
  fontSize = "12px",
  color = "text.secondary",
  italic = false,
  sx,
}) {
  return (
    <Box
      sx={{
        fontSize,
        color,
        lineHeight: 1.6,
        fontStyle: italic ? "italic" : "normal",
        wordBreak: "break-word",
        "& > :first-of-type": { mt: 0 },
        "& > :last-child": { mb: 0 },
        "& p": { m: 0, mb: 0.75 },
        "& strong": { fontWeight: 700, color: "text.primary" },
        "& em": { fontStyle: "italic" },
        "& a": { color: ACCENT, textDecoration: "underline" },
        "& ul, & ol": { m: 0, mb: 0.75, pl: 2.25 },
        "& li": { mb: 0.2 },
        "& h1, & h2, & h3, & h4, & h5, & h6": {
          fontSize: "1.05em",
          fontWeight: 700,
          color: "text.primary",
          m: 0,
          mb: 0.4,
        },
        "& code": {
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          fontSize: "0.9em",
          fontStyle: "normal",
          px: 0.5,
          py: "1px",
          borderRadius: "3px",
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? alpha("#fff", 0.08)
              : alpha("#000", 0.05),
        },
        "& pre": {
          m: 0,
          mb: 0.75,
          p: 1,
          borderRadius: "6px",
          overflowX: "auto",
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? alpha("#fff", 0.05)
              : alpha("#000", 0.04),
        },
        "& pre code": { bgcolor: "transparent", p: 0, fontSize: "11px" },
        "& blockquote": {
          m: 0,
          mb: 0.75,
          pl: 1,
          borderLeft: "2px solid",
          borderColor: "divider",
          color: "text.secondary",
        },
        "& table": {
          borderCollapse: "collapse",
          width: "auto",
          my: 0.75,
          fontSize: "0.95em",
          display: "block",
          overflowX: "auto",
        },
        "& th, & td": {
          border: "1px solid",
          borderColor: "divider",
          px: 1,
          py: 0.5,
          textAlign: "left",
        },
        "& th": {
          fontWeight: 700,
          color: "text.primary",
          bgcolor: (theme) =>
            theme.palette.mode === "dark"
              ? alpha("#fff", 0.04)
              : alpha("#000", 0.03),
        },
        "& hr": {
          border: "none",
          borderTop: "1px solid",
          borderColor: "divider",
          my: 1,
        },
        ...sx,
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text || ""}</ReactMarkdown>
    </Box>
  );
}
AnalyzeMarkdown.propTypes = {
  text: PropTypes.string,
  fontSize: PropTypes.string,
  color: PropTypes.string,
  italic: PropTypes.bool,
  sx: PropTypes.object,
};

// Drop-in replacement for <Typography>{text}</Typography> that streams the
// text in when `instant` is false. When `instant` is true (e.g., the user
// is opening a long-completed reasoning block) the text appears in full —
// nobody wants a 1980s typewriter for content they're just reviewing.
//
// `onComplete` fires exactly once when the stream finishes (or immediately
// on mount in instant mode). Used by the SequentialReveal coordinator to
// advance to the next block / list item.
function StreamingPlainText({
  text,
  instant,
  cursorColor,
  onComplete,
  identityKey,
  ...typoProps
}) {
  const fullText = typeof text === "string" ? text : "";
  const { revealed, isStreaming } = useStreamingText(text, {
    instant,
    identityKey,
  });

  // Fire onComplete exactly once when the stream reaches the end. Held in
  // a ref so changing the callback identity per render doesn't re-fire.
  const onCompleteRef = useRef(onComplete);
  useEffect(() => {
    onCompleteRef.current = onComplete;
  });
  const firedRef = useRef(false);
  useEffect(() => {
    firedRef.current = false;
  }, [fullText]);
  useEffect(() => {
    if (!isStreaming && fullText.length > 0 && !firedRef.current) {
      firedRef.current = true;
      onCompleteRef.current?.();
    }
  }, [isStreaming, fullText.length]);

  return (
    <Typography {...typoProps}>
      {revealed}
      {isStreaming && <StreamCursor color={cursorColor} />}
    </Typography>
  );
}
StreamingPlainText.propTypes = {
  text: PropTypes.string,
  instant: PropTypes.bool,
  cursorColor: PropTypes.string,
  onComplete: PropTypes.func,
  identityKey: PropTypes.string,
};

// Coordinates a sequence of N "phases". In review mode (live=false) all
// phases are revealed at once. In live mode phase 0 is active first;
// advancing the phase reveals the next one. Children opt in by checking
// `phase === i` for "I'm streaming now" and calling `advance()` when done.
function useSequentialReveal(total, live) {
  const [phase, setPhase] = useState(live ? 0 : total);
  const advance = useCallback(() => {
    setPhase((n) => Math.min(n + 1, total));
  }, [total]);
  // If `total` shrinks (rare, but possible if the data updates) clamp the
  // phase so we don't get stuck past the end.
  useEffect(() => {
    setPhase((n) => Math.min(n, total));
  }, [total]);
  return { phase, advance };
}

// Fade-in + slight slide-up wrapper for any newly-mounted message card so
// each one settles in instead of popping. ~200ms ease-out, subtle 6px lift.
function FadeIn({ children }) {
  return (
    <Box
      sx={{
        animation: "msg-in 220ms cubic-bezier(0.2, 0.65, 0.3, 1) both",
        "@keyframes msg-in": {
          "0%": { opacity: 0, transform: "translateY(6px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
      }}
    >
      {children}
    </Box>
  );
}
FadeIn.propTypes = { children: PropTypes.node };

// Internal — list items reveal one at a time in live mode. In review
// mode every item shows in full from the start.
function SequentialListItems({ items, live, onComplete, identityPrefix }) {
  const { phase, advance } = useSequentialReveal(items.length, live);

  // When all items have streamed (or instantly on mount in review mode),
  // bubble completion up so the parent advances to the next block.
  const onCompleteRef = useRef(onComplete);
  useEffect(() => {
    onCompleteRef.current = onComplete;
  });
  const firedRef = useRef(false);
  useEffect(() => {
    if (phase >= items.length && !firedRef.current) {
      firedRef.current = true;
      onCompleteRef.current?.();
    }
  }, [phase, items.length]);

  return (
    <Stack gap={0.4}>
      {items.map((it, i) => {
        const visible = !live || i <= phase;
        const active = live && i === phase;
        if (!visible) return null;
        return (
          <Stack key={i} direction="row" gap={0.75} alignItems="flex-start">
            <Box
              sx={{
                width: 4,
                height: 4,
                borderRadius: "50%",
                bgcolor: "text.disabled",
                mt: "7px",
                flexShrink: 0,
              }}
            />
            <StreamingPlainText
              text={it}
              instant={!active}
              onComplete={active ? advance : undefined}
              identityKey={
                identityPrefix ? `${identityPrefix}-item-${i}` : undefined
              }
              fontSize="11.5px"
              color="text.secondary"
              sx={{ lineHeight: 1.55 }}
            />
          </Stack>
        );
      })}
    </Stack>
  );
}
SequentialListItems.propTypes = {
  items: PropTypes.array.isRequired,
  live: PropTypes.bool,
  onComplete: PropTypes.func,
  identityPrefix: PropTypes.string,
};

// One block of a step's expanded reasoning — Claude-Code-style. When
// `live` is true (the parent step is currently running) text content
// streams in word-by-word with a blinking cursor; when false (the step
// has already completed and the user is just reviewing it) text renders
// in full so we don't make people watch a typewriter for content that's
// already done.
//
// `onComplete` is the signal the parent SequentialReveal coordinator
// listens for to advance to the next block. Each kind wires it up where
// the *last* streaming element finishes:
//   reasoning → its single text stream
//   tool      → the `→ output` stream (or immediately if no output)
//   list      → the last item's stream (via SequentialListItems)
//   code      → its single text stream
function StepDetailBlock({ block, live, onComplete, identityKey }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  // Used by branches with no streamed content (e.g., tool block with no
  // output). Fire onComplete once on mount when in live mode so the
  // sequencer doesn't stall waiting on a stream that never starts.
  const onCompleteRef = useRef(onComplete);
  useEffect(() => {
    onCompleteRef.current = onComplete;
  });
  const fireOnceRef = useRef(false);
  const fireOnce = useCallback(() => {
    if (fireOnceRef.current) return;
    fireOnceRef.current = true;
    onCompleteRef.current?.();
  }, []);

  if (block.kind === "reasoning") {
    return (
      <StreamingPlainText
        text={block.text}
        instant={!live}
        onComplete={onComplete}
        identityKey={identityKey}
        fontSize="11.5px"
        color="text.secondary"
        sx={{ lineHeight: 1.65 }}
      />
    );
  }

  if (block.kind === "tool") {
    return (
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "6px",
          bgcolor: isDark ? alpha("#fff", 0.025) : alpha("#000", 0.02),
          px: 1,
          py: 0.75,
        }}
      >
        <Stack direction="row" alignItems="center" gap={0.5}>
          <Iconify
            icon="mdi:wrench-outline"
            width={11}
            sx={{ color: ACCENT }}
          />
          <Typography
            fontSize="11px"
            fontWeight={600}
            sx={{
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              color: "text.primary",
            }}
          >
            {block.name}
          </Typography>
        </Stack>
        {block.input != null && (
          <Typography
            fontSize="10.5px"
            sx={{
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              color: "text.disabled",
              mt: 0.3,
              wordBreak: "break-word",
            }}
          >
            {block.input}
          </Typography>
        )}
        {block.output != null ? (
          <StreamingPlainText
            text={`→ ${block.output}`}
            instant={!live}
            onComplete={onComplete}
            identityKey={identityKey}
            fontSize="10.5px"
            sx={{
              fontFamily: "ui-monospace, SFMono-Regular, monospace",
              color: "text.secondary",
              mt: 0.3,
              wordBreak: "break-word",
            }}
          />
        ) : (
          <SignalOnMount fire={fireOnce} />
        )}
      </Box>
    );
  }

  if (block.kind === "list") {
    return (
      <Box>
        {block.title && (
          <Typography
            fontSize="9.5px"
            fontWeight={700}
            color="text.disabled"
            sx={{
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              mb: 0.5,
            }}
          >
            {block.title}
          </Typography>
        )}
        <SequentialListItems
          items={block.items}
          live={live}
          onComplete={onComplete}
          identityPrefix={identityKey}
        />
      </Box>
    );
  }

  if (block.kind === "code") {
    return (
      <StreamingPlainText
        text={block.text}
        instant={!live}
        onComplete={onComplete}
        identityKey={identityKey}
        component="pre"
        sx={{
          m: 0,
          p: 1,
          borderRadius: "6px",
          bgcolor: isDark ? alpha("#fff", 0.03) : alpha("#000", 0.03),
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          fontSize: "10.5px",
          lineHeight: 1.5,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          color: "text.secondary",
          overflow: "auto",
        }}
      />
    );
  }
  return null;
}
StepDetailBlock.propTypes = {
  block: PropTypes.object.isRequired,
  live: PropTypes.bool,
  onComplete: PropTypes.func,
  identityKey: PropTypes.string,
};

// Tiny helper — fire a callback exactly once after mount. Used by tool
// blocks that have no streamed content; the sequencer would otherwise
// wait forever on a stream that never starts.
function SignalOnMount({ fire }) {
  useEffect(() => {
    fire();
  }, [fire]);
  return null;
}
SignalOnMount.propTypes = { fire: PropTypes.func };

// Renders a step's details list with sequential reveal: in live mode
// each block streams in turn (one finishes before the next appears); in
// review mode every block shows at once. `identityPrefix` is the
// owning step's id; combined with the block index it gives each stream
// a stable per-session identity so tab-away/tab-back doesn't replay.
function StepDetailsPanel({ blocks, live, identityPrefix }) {
  const { phase, advance } = useSequentialReveal(blocks.length, live);

  const keyForBlock = (i) =>
    identityPrefix ? `${identityPrefix}-detail-${i}` : undefined;

  if (!live) {
    return (
      <Stack gap={1} sx={{ pt: 1 }}>
        {blocks.map((block, i) => (
          <StepDetailBlock
            key={i}
            block={block}
            live={false}
            identityKey={keyForBlock(i)}
          />
        ))}
      </Stack>
    );
  }

  return (
    <Stack gap={1} sx={{ pt: 1 }}>
      {blocks.map((block, i) => {
        if (i > phase) return null;
        const active = i === phase;
        return (
          <FadeIn key={i}>
            <StepDetailBlock
              block={block}
              live={active}
              onComplete={active ? advance : undefined}
              identityKey={keyForBlock(i)}
            />
          </FadeIn>
        );
      })}
    </Stack>
  );
}
StepDetailsPanel.propTypes = {
  blocks: PropTypes.array.isRequired,
  live: PropTypes.bool,
  identityPrefix: PropTypes.string,
};

function StepCard({ step }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const isRunning = step.status === STEP_STATUS.RUNNING;
  const isQueued = step.status === STEP_STATUS.QUEUED;
  const isDone = step.status === STEP_STATUS.DONE;
  const hasDetails = (isRunning || isDone) && step.details?.length > 0;
  // Done steps default collapsed; the actively-running step auto-expands so
  // you watch the reasoning stream live (like Claude Code).
  const [expanded, setExpanded] = useState(false);
  const open = expanded || (isRunning && hasDetails);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: isRunning ? alpha(ACCENT, 0.35) : "divider",
        borderRadius: "8px",
        bgcolor: isRunning
          ? alpha(ACCENT, isDark ? 0.08 : 0.04)
          : isDark
            ? alpha("#fff", 0.02)
            : "background.paper",
        opacity: isQueued ? 0.55 : 1,
        transition: "all 0.2s",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        gap={1.25}
        onClick={hasDetails ? () => setExpanded((v) => !v) : undefined}
        sx={{
          px: 1.5,
          py: 1.25,
          cursor: hasDetails ? "pointer" : "default",
          userSelect: "none",
          "&:hover": hasDetails
            ? { bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.015) }
            : {},
        }}
      >
        <Box
          sx={{
            width: 18,
            height: 18,
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            mt: "1px",
            bgcolor: isDone
              ? alpha("#5ACE6D", isDark ? 0.18 : 0.14)
              : isRunning
                ? alpha(ACCENT, 0.18)
                : isDark
                  ? alpha("#fff", 0.06)
                  : alpha("#000", 0.05),
          }}
        >
          {isRunning ? (
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                border: "2px solid",
                borderColor: alpha(ACCENT, 0.25),
                borderTopColor: ACCENT,
                animation: "spin 0.8s linear infinite",
                "@keyframes spin": { to: { transform: "rotate(360deg)" } },
              }}
            />
          ) : isDone ? (
            <Iconify icon="mdi:check" width={12} sx={{ color: "#5ACE6D" }} />
          ) : (
            <Iconify
              icon="mdi:dots-horizontal"
              width={12}
              sx={{ color: "text.disabled" }}
            />
          )}
        </Box>
        <Stack gap={0.4} flex={1} minWidth={0}>
          <Typography fontSize="12.5px" fontWeight={600} color="text.primary">
            {step.title}
          </Typography>
          {(isRunning || isDone) && (
            <Typography
              fontSize="11.5px"
              color="text.secondary"
              sx={{ lineHeight: 1.5 }}
            >
              {step.detail}
            </Typography>
          )}
          {isDone && step.chips?.length > 0 && (
            <Stack direction="row" gap={0.5} flexWrap="wrap" sx={{ mt: 0.25 }}>
              {step.chips.map((c) => (
                <Chip
                  key={c}
                  label={c}
                  size="small"
                  sx={{
                    height: 18,
                    fontSize: "10px",
                    fontFamily: "ui-monospace, SFMono-Regular, monospace",
                    borderRadius: "4px",
                    bgcolor: "action.hover",
                    color: "text.secondary",
                    "& .MuiChip-label": { px: "6px" },
                  }}
                />
              ))}
            </Stack>
          )}
        </Stack>
        {hasDetails && (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.3}
            sx={{ flexShrink: 0, mt: "1px" }}
          >
            <Typography fontSize="10px" color="text.disabled">
              {open ? "Hide" : "Details"}
            </Typography>
            <Iconify
              icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
              width={15}
              sx={{ color: "text.disabled" }}
            />
          </Stack>
        )}
      </Stack>

      {hasDetails && (
        <Collapse in={open} unmountOnExit>
          <Box
            sx={{
              px: 1.5,
              pb: 1.5,
              pt: 0.25,
              ml: "30px",
              borderTop: "1px dashed",
              borderColor: "divider",
            }}
          >
            <StepDetailsPanel
              blocks={step.details}
              live={isRunning}
              identityPrefix={step.id}
            />
          </Box>
        </Collapse>
      )}
    </Box>
  );
}
StepCard.propTypes = { step: PropTypes.object.isRequired };

// The agent's native thinking, streamed inline as its own block (Claude-Code
// style) — a dim, rule-bordered "Thinking" passage between steps. Streaming is
// keyed off the message id so tabbing away and back doesn't replay it.
function ReasoningBlock({ text }) {
  // Collapsed by default — the full thinking is a wall of text most people skim
  // past. Header shows a faint one-line preview so it's scannable; click to
  // expand the whole passage.
  const [open, setOpen] = useState(false);
  const preview = (text || "").replace(/\s+/g, " ").trim().slice(0, 100);
  return (
    <Stack direction="row" gap={1} sx={{ pl: 0.5 }}>
      <Iconify
        icon="mdi:brain"
        width={13}
        sx={{ color: "text.disabled", mt: "3px", flexShrink: 0 }}
      />
      <Box
        sx={{
          flex: 1,
          minWidth: 0,
          borderLeft: "2px solid",
          borderColor: "divider",
          pl: 1.25,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          onClick={() => setOpen((v) => !v)}
          sx={{ cursor: "pointer", userSelect: "none" }}
        >
          <Typography
            fontSize="9.5px"
            fontWeight={700}
            color="text.disabled"
            sx={{
              textTransform: "uppercase",
              letterSpacing: "0.07em",
              flexShrink: 0,
            }}
          >
            Thinking
          </Typography>
          {!open && preview && (
            <Typography
              fontSize="11px"
              color="text.disabled"
              noWrap
              sx={{ flex: 1, minWidth: 0, fontStyle: "italic", opacity: 0.6 }}
            >
              {preview}…
            </Typography>
          )}
          <Iconify
            icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={14}
            sx={{ color: "text.disabled", ml: "auto", flexShrink: 0 }}
          />
        </Stack>
        <Collapse in={open} unmountOnExit>
          <AnalyzeMarkdown
            text={text}
            italic
            fontSize="11.5px"
            color="text.secondary"
            sx={{ opacity: 0.85, mt: 0.5 }}
          />
        </Collapse>
      </Box>
    </Stack>
  );
}
ReasoningBlock.propTypes = {
  text: PropTypes.string,
};

function SynthesisCard({ synthesis }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  // Stream the headline first; once it finishes, stream the fix below it.
  // identityKey is keyed off the synthesis message id so a re-mount (user
  // tabbed away and came back) skips the animation if both already streamed.
  // `instant` (replayed-from-cache synthesis) skips the typewriter entirely.
  const head = useStreamingText(synthesis.headline, {
    instant: synthesis.instant,
    identityKey: `${synthesis.id}-head`,
  });
  const headDone = !head.isStreaming;
  const fix = useStreamingText(headDone ? synthesis.fix : "", {
    instant: synthesis.instant,
    identityKey: `${synthesis.id}-fix`,
  });
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: alpha("#7857FC", 0.3),
        borderRadius: "8px",
        bgcolor: alpha("#7857FC", isDark ? 0.06 : 0.03),
        p: 1.5,
        position: "relative",
      }}
    >
      <Stack direction="row" alignItems="center" gap={0.5} sx={{ mb: 1 }}>
        <Iconify
          icon="mdi:star-four-points"
          width={12}
          sx={{ color: "#7857FC" }}
        />
        <Typography
          fontSize="10.5px"
          fontWeight={700}
          sx={{
            color: "#7857FC",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
          }}
        >
          Synthesis
        </Typography>
        {CONF_META[synthesis.confidence] && (
          <Typography
            fontSize="9.5px"
            fontWeight={700}
            sx={{
              ml: "auto",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              color: CONF_META[synthesis.confidence].color,
              px: 0.75,
              py: 0.25,
              borderRadius: "3px",
              bgcolor: alpha(
                CONF_META[synthesis.confidence].color,
                isDark ? 0.16 : 0.12,
              ),
            }}
          >
            {CONF_META[synthesis.confidence].label}
          </Typography>
        )}
      </Stack>
      <Box sx={{ mb: 1 }}>
        <AnalyzeMarkdown
          text={head.revealed}
          fontSize="13.5px"
          color="text.primary"
          sx={{ lineHeight: 1.55 }}
        />
        {head.isStreaming && <StreamCursor />}
      </Box>
      {headDone && synthesis.fix && (
        <Stack direction="row" gap={1} alignItems="flex-start">
          <Typography
            fontSize="10px"
            fontWeight={700}
            sx={{
              color: "#5ACE6D",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              mt: "3px",
              flexShrink: 0,
              px: 0.75,
              py: 0.25,
              borderRadius: "3px",
              bgcolor: alpha("#5ACE6D", isDark ? 0.14 : 0.12),
            }}
          >
            Fix
          </Typography>
          <Box sx={{ flex: 1 }}>
            <AnalyzeMarkdown
              text={fix.revealed}
              fontSize="12.5px"
              color="text.secondary"
              sx={{ lineHeight: 1.6 }}
            />
            {fix.isStreaming && <StreamCursor color="#5ACE6D" />}
          </Box>
        </Stack>
      )}
    </Box>
  );
}
SynthesisCard.propTypes = {
  synthesis: PropTypes.object.isRequired,
};

function RunHeader({ label, timestamp }) {
  return (
    <Stack direction="row" alignItems="center" gap={1.25} sx={{ py: 0.5 }}>
      <Box sx={{ flex: 1, height: "1px", bgcolor: "divider" }} />
      <Stack direction="row" alignItems="center" gap={0.5}>
        <Iconify
          icon="mdi:star-four-points-outline"
          width={11}
          sx={{ color: "text.disabled" }}
        />
        <Typography
          fontSize="10px"
          fontWeight={600}
          color="text.disabled"
          sx={{ textTransform: "uppercase", letterSpacing: "0.08em" }}
        >
          {label} · {timestamp}
        </Typography>
      </Stack>
      <Box sx={{ flex: 1, height: "1px", bgcolor: "divider" }} />
    </Stack>
  );
}
RunHeader.propTypes = { label: PropTypes.string, timestamp: PropTypes.string };

// ── Follow-up message renderers ──────────────────────────────────────────

// User-submitted question — right-aligned chat bubble.
function UserQuestionBubble({ text }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Stack direction="row" justifyContent="flex-end" sx={{ pl: 6 }}>
      <Box
        sx={{
          px: 1.5,
          py: 1,
          borderRadius: "12px 12px 4px 12px",
          maxWidth: "85%",
          bgcolor: isDark ? alpha("#fff", 0.07) : alpha(ACCENT, 0.08),
          border: "1px solid",
          borderColor: isDark ? alpha("#fff", 0.1) : alpha(ACCENT, 0.16),
        }}
      >
        <Typography
          fontSize="13px"
          color="text.primary"
          sx={{ lineHeight: 1.55 }}
        >
          {text}
        </Typography>
      </Box>
    </Stack>
  );
}
UserQuestionBubble.propTypes = { text: PropTypes.string.isRequired };

// Falcon's short pre-sub-agent intro line.
function AssistantIntro({ text }) {
  return (
    <Stack direction="row" alignItems="flex-start" gap={1} sx={{ pl: 0.5 }}>
      <Iconify
        icon="mdi:star-four-points"
        width={14}
        sx={{ color: ACCENT, mt: "3px", flexShrink: 0 }}
      />
      <Typography
        fontSize="13px"
        color="text.primary"
        sx={{ lineHeight: 1.55 }}
      >
        {text}
      </Typography>
    </Stack>
  );
}
AssistantIntro.propTypes = { text: PropTypes.string.isRequired };

// One row inside the sub-agent's mini step list — denser than StepCard
// because we're nested inside a card already. Same status semantics
// (queued / running / done).
function SubagentStepRow({ step }) {
  const status = step.status ?? STEP_STATUS.QUEUED;
  const isDone = status === STEP_STATUS.DONE;
  const isRunning = status === STEP_STATUS.RUNNING;
  return (
    <Stack direction="row" alignItems="flex-start" gap={1} sx={{ py: 0.4 }}>
      <Box
        sx={{
          width: 14,
          height: 14,
          mt: "2px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {isDone ? (
          <Iconify icon="mdi:check" width={13} sx={{ color: "#5ACE6D" }} />
        ) : isRunning ? (
          <Box
            sx={{
              width: 11,
              height: 11,
              borderRadius: "50%",
              border: "2px solid",
              borderColor: alpha(ACCENT, 0.25),
              borderTopColor: ACCENT,
              animation: "spin 0.8s linear infinite",
              "@keyframes spin": { to: { transform: "rotate(360deg)" } },
            }}
          />
        ) : (
          <Iconify
            icon="mdi:circle-outline"
            width={11}
            sx={{ color: "text.disabled" }}
          />
        )}
      </Box>
      <Stack gap={0.15} sx={{ minWidth: 0 }}>
        <Typography
          fontSize="12.5px"
          fontWeight={isRunning ? 600 : 500}
          color={
            isRunning
              ? "text.primary"
              : isDone
                ? "text.primary"
                : "text.secondary"
          }
          sx={{ lineHeight: 1.4 }}
        >
          {step.title}
        </Typography>
        {isDone && step.detail && step.detail !== "—" && (
          <Typography
            fontSize="11.5px"
            color="text.secondary"
            sx={{ lineHeight: 1.45 }}
          >
            {step.detail}
          </Typography>
        )}
      </Stack>
    </Stack>
  );
}
SubagentStepRow.propTypes = { step: PropTypes.object.isRequired };

// Light **bold** parsing for the sub-agent answer body — minimal markdown.
// `trailingNode` is rendered inline at the very end of the text (so a
// streaming cursor sits next to the last revealed character).
// Stream a markdown answer word-by-word + a blinking cursor until complete.
// Falcon answers use full markdown (headings, tables, lists, code) — render
// them through AnalyzeMarkdown, not the old bold/code-only mini parser. The
// partial text is valid markdown at each step, so styling settles as it streams.
function StreamingMarkdown({ text, identityKey }) {
  const { revealed, isStreaming } = useStreamingText(text, { identityKey });
  return (
    <Box>
      <AnalyzeMarkdown text={revealed} fontSize="13px" color="text.primary" />
      {isStreaming && <StreamCursor />}
    </Box>
  );
}
StreamingMarkdown.propTypes = {
  text: PropTypes.string.isRequired,
  identityKey: PropTypes.string,
};

// The sub-agent container: header strip + sub-step list + final answer.
function SubagentCard({ msg }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const isStreaming = msg.status === STREAM_STATUS.STREAMING;
  return (
    <Stack gap={1.25} sx={{ pl: 3 }}>
      <Box
        sx={{
          border: "1px solid",
          borderColor: isDark ? alpha("#fff", 0.08) : "divider",
          borderRadius: "10px",
          overflow: "hidden",
          bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.015),
        }}
      >
        {/* Header */}
        <Stack
          direction="row"
          alignItems="center"
          gap={0.85}
          sx={{
            px: 1.5,
            py: 0.85,
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: isDark ? alpha(ACCENT, 0.08) : alpha(ACCENT, 0.05),
          }}
        >
          <Iconify icon="mdi:robot-outline" width={14} sx={{ color: ACCENT }} />
          <Typography
            fontSize="10.5px"
            fontWeight={700}
            sx={{
              color: ACCENT,
              textTransform: "uppercase",
              letterSpacing: "0.07em",
            }}
          >
            Sub-agent · {msg.title}
          </Typography>
          {msg.traceShortId && (
            <>
              <Box
                sx={{
                  width: 3,
                  height: 3,
                  borderRadius: "50%",
                  bgcolor: "text.disabled",
                }}
              />
              <Typography
                fontSize="10.5px"
                color="text.disabled"
                sx={{
                  fontFamily: "ui-monospace, SFMono-Regular, monospace",
                }}
              >
                {msg.traceShortId}
              </Typography>
            </>
          )}
          <Box sx={{ flex: 1 }} />
          {isStreaming && (
            <Typography
              fontSize="10.5px"
              fontWeight={600}
              color="text.disabled"
              sx={{ textTransform: "uppercase", letterSpacing: "0.07em" }}
            >
              running
            </Typography>
          )}
        </Stack>

        {/* Sub-steps */}
        <Box sx={{ px: 1.5, py: 1 }}>
          <Stack gap={0}>
            {msg.steps?.map((s) => (
              <SubagentStepRow key={s.id} step={s} />
            ))}
          </Stack>
        </Box>
      </Box>

      {/* Final answer — appears once all steps are done, then streams in
          LLM-style with a blinking cursor at the trailing edge. */}
      {msg.answer && (
        <StreamingMarkdown text={msg.answer} identityKey={`${msg.id}-answer`} />
      )}
    </Stack>
  );
}
SubagentCard.propTypes = { msg: PropTypes.object.isRequired };

// Sticky input bar at the bottom of the tab. Disabled until the main run
// finishes (so the user can't fork a sub-agent mid-cluster-analysis) and
// while a sub-agent is streaming (to avoid stacking parallel runs).
//
// Visual design — inspired by Claude's chat composer:
//   - Clean 1px neutral border (theme-aware) in the default state
//   - Subtle elevation via box-shadow
//   - Focus state: border lifts to the Falcon-purple accent at 1.5px
//   - No gradient stroke — the gradient looked great in light but read as
//     a glowing video-game ring in dark mode. A solid border with a focus
//     accent is calmer and works in both themes.
function FollowUpInput({ disabled, placeholder, onSubmit }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const [text, setText] = useState("");
  const [focused, setFocused] = useState(false);
  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSubmit?.(t);
    setText("");
  };
  const showAccent = focused && !disabled;
  return (
    <Box
      sx={{
        flexShrink: 0,
        border: "1.5px solid",
        borderColor: showAccent
          ? ACCENT
          : isDark
            ? alpha("#fff", 0.14)
            : alpha("#000", 0.14),
        borderRadius: "14px",
        bgcolor: isDark ? "#1c1c1f" : "#ffffff",
        boxShadow: showAccent
          ? `0 0 0 3px ${alpha(ACCENT, isDark ? 0.18 : 0.12)}, 0 1px 2px ${alpha("#000", isDark ? 0.35 : 0.06)}`
          : isDark
            ? `0 1px 2px ${alpha("#000", 0.4)}`
            : `0 1px 3px ${alpha("#000", 0.06)}`,
        px: 1.5,
        py: 0.85,
        display: "flex",
        alignItems: "center",
        gap: 0.85,
        opacity: disabled ? 0.7 : 1,
        transition:
          "border-color 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease",
      }}
    >
      <Iconify
        icon="mdi:star-four-points"
        width={15}
        sx={{ color: ACCENT, ml: 0.25, flexShrink: 0 }}
      />
      <TextField
        fullWidth
        multiline
        maxRows={6}
        size="small"
        variant="standard"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        disabled={disabled}
        placeholder={placeholder}
        InputProps={{
          disableUnderline: true,
          sx: {
            fontSize: "13.5px",
            lineHeight: 1.5,
            py: 0.5,
            // Lighter than `text.secondary` at full opacity but still more
            // legible than MUI's ghostly `text.disabled` default. Regular
            // weight so the prompt feels restrained, not assertive.
            "& input::placeholder, & textarea::placeholder": {
              color: "text.secondary",
              opacity: 0.7,
              fontWeight: 400,
            },
          },
        }}
      />
      <Tooltip
        title={disabled ? "Waiting for current run…" : "Send (Enter)"}
        arrow
      >
        <span>
          <IconButton
            size="small"
            onClick={submit}
            disabled={disabled || !text.trim()}
            sx={{
              width: 28,
              height: 28,
              borderRadius: "6px",
              bgcolor: text.trim() && !disabled ? ACCENT : "transparent",
              color: text.trim() && !disabled ? "#fff" : "text.disabled",
              "&:hover": {
                bgcolor:
                  text.trim() && !disabled
                    ? "#6845E8"
                    : isDark
                      ? alpha("#fff", 0.05)
                      : alpha("#000", 0.04),
              },
              "&.Mui-disabled": {
                color: "text.disabled",
              },
            }}
          >
            <Iconify icon="mdi:arrow-up" width={14} />
          </IconButton>
        </span>
      </Tooltip>
    </Box>
  );
}
FollowUpInput.propTypes = {
  disabled: PropTypes.bool,
  placeholder: PropTypes.string,
  onSubmit: PropTypes.func,
};

// Sticky bottom block — example-question chips on top, input bar on the
// bottom — so the compose affordance reads as a single unit at the foot
// of the tab. The chip list is contextual: starter examples until the
// user asks something, then the latest sub-agent's "Try asking" set.
function ComposeArea({
  suggestions,
  suggestionsHeader,
  disabled,
  placeholder,
  onSubmit,
}) {
  return (
    <Stack gap={1} sx={{ flexShrink: 0 }}>
      {suggestions?.length > 0 && (
        <Stack gap={0.5} sx={{ px: 0.25 }}>
          <Typography
            fontSize="9.5px"
            fontWeight={700}
            color="text.disabled"
            sx={{ textTransform: "uppercase", letterSpacing: "0.09em" }}
          >
            {suggestionsHeader}
          </Typography>
          <Stack direction="row" gap={0.6} flexWrap="wrap">
            {suggestions.map((q) => (
              <Chip
                key={q}
                label={q}
                size="small"
                disabled={disabled}
                onClick={() => onSubmit?.(q)}
                sx={{
                  height: 26,
                  borderRadius: "13px",
                  fontSize: "12px",
                  fontWeight: 500,
                  cursor: "pointer",
                  bgcolor: (theme) =>
                    theme.palette.mode === "dark"
                      ? alpha("#fff", 0.05)
                      : alpha("#000", 0.04),
                  color: "text.primary",
                  border: "1px solid",
                  borderColor: "divider",
                  "&:hover": {
                    bgcolor: (theme) =>
                      theme.palette.mode === "dark"
                        ? alpha("#fff", 0.09)
                        : alpha("#000", 0.06),
                  },
                }}
              />
            ))}
          </Stack>
        </Stack>
      )}
      <FollowUpInput
        disabled={disabled}
        placeholder={placeholder}
        onSubmit={onSubmit}
      />
    </Stack>
  );
}
ComposeArea.propTypes = {
  suggestions: PropTypes.array,
  suggestionsHeader: PropTypes.string,
  disabled: PropTypes.bool,
  placeholder: PropTypes.string,
  onSubmit: PropTypes.func,
};

// Fallback "Try asking" set — used only when the agent's run returned no
// grounded suggestions. Cluster-agnostic but honest (no invented specifics);
// the agent's own suggested_questions replace these whenever present.
const STARTER_SUGGESTIONS = [
  "Show me a failing trace",
  "What changed before this started?",
  "How do I verify the fix?",
];

// ── Main AnalyzeTab ───────────────────────────────────────────────────────

export default function AnalyzeTab({ error }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const clusterId = error?.cluster_id;
  const thread = useErrorFeedStore(
    (s) => s.analyzeThreadsByCluster[clusterId] ?? null,
  );
  const setAnalyzePendingStart = useErrorFeedStore(
    (s) => s.setAnalyzePendingStart,
  );
  // Owns the follow-up Q&A streaming. Independent of the main analyze
  // runner (which is mounted at the parent / headline-card layer) so the
  // two flows don't share timer state.
  const { runFollowUp } = useFollowUpRunner(clusterId, error);

  const messages = thread?.messages ?? [];
  const runState = thread?.runState ?? RUN_STATE.IDLE;
  // Live setup-progress line (rca_status) shown in the loader before the first
  // real frame lands, so the pre-LLM dead-air shows actual activity.
  const setupStatus = thread?.status;
  const followUpRunState = thread?.followUpRunState ?? RUN_STATE.IDLE;
  const isStreaming = runState === RUN_STATE.STREAMING;
  const isFollowUpStreaming = followUpRunState === RUN_STATE.STREAMING;
  const mainRunDone = runState === RUN_STATE.DONE;

  // "Try asking" chips are a starter affordance — grounded questions to kick
  // off the conversation off the synthesis. Once the user has summoned Falcon
  // (asked any follow-up), get out of the way: it's a normal chat from there,
  // so the chips disappear rather than re-seeding after every answer.
  const hasFollowedUp = useMemo(
    () => messages.some((m) => m.type === MESSAGE_TYPE.USER_QUESTION),
    [messages],
  );
  const latestSuggestionsMsg = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].type === MESSAGE_TYPE.SUGGESTIONS) return messages[i];
    }
    return null;
  }, [messages]);
  const composeSuggestions = hasFollowedUp
    ? []
    : latestSuggestionsMsg?.items ?? STARTER_SUGGESTIONS;
  const composeHeader = "Try asking";

  // Chronological order — the cluster steps build the case, the synthesis
  // is the headline, follow-ups continue the conversation below it.
  // (Earlier this tab pushed the synthesis to the top; that was fine while
  // there were no follow-ups but reads awkwardly once the user is mid-Q&A.)
  const scrollerRef = useRef(null);

  // Always follow the latest message — for streaming runs AND for follow-ups
  // the user just submitted, the bottom is where the action is.
  useEffect(() => {
    if (!scrollerRef.current) return;
    scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
  }, [messages.length, runState, followUpRunState]);

  // While text is actively streaming, the reasoning / answer body grows
  // char-by-char inside useStreamingText (internal state — message count and
  // length don't change), so the effect above never re-fires and the live
  // text overflows below the fold. Keep the scroller pinned to the bottom on
  // an interval for as long as either side is streaming.
  useEffect(() => {
    if (!isStreaming && !isFollowUpStreaming) return undefined;
    const id = setInterval(() => {
      const el = scrollerRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, STREAM_TICK_MS * 4);
    return () => clearInterval(id);
  }, [isStreaming, isFollowUpStreaming]);

  // Both empty-state CTA and Re-run dispatch via the pending flag so the
  // shared runner (and therefore the headline card) sees the same trigger.
  const onTriggerRun = () => setAnalyzePendingStart(clusterId, true);

  // Format the run-started timestamp once.
  const startedLabel = useMemo(() => {
    if (!thread?.startedAt) return null;
    const d = new Date(thread.startedAt);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }, [thread?.startedAt]);

  return (
    <Stack
      gap={1.5}
      sx={{
        // Parent (ErrorFeedDetailView) gives us a fixed-height flex column,
        // so we just fill it. The internal scroller handles overflow and
        // the ComposeArea docks at the foot of this Stack — which is the
        // bottom of the viewport.
        width: "100%",
        flex: 1,
        minHeight: 0,
        py: 0.5,
      }}
    >
      {/* Context strip */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 1.5,
          py: 1,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: isDark ? alpha("#fff", 0.02) : alpha("#000", 0.02),
          flexShrink: 0,
        }}
      >
        <Iconify
          icon="mdi:layers-outline"
          width={14}
          sx={{ color: "text.disabled" }}
        />
        <Typography
          fontSize="12px"
          fontWeight={600}
          color="text.primary"
          noWrap
        >
          {error?.error?.name ?? "Cluster"}
        </Typography>
        <Typography fontSize="11.5px" color="text.disabled">
          · {error?.trace_count?.toLocaleString() ?? "—"} traces
        </Typography>
        {startedLabel && (
          <Typography fontSize="11.5px" color="text.disabled">
            · started {startedLabel}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        <Tooltip title="Re-run with current cluster state (1 credit)" arrow>
          <span>
            <Button
              size="small"
              variant="text"
              disabled={isStreaming}
              onClick={onTriggerRun}
              startIcon={<Iconify icon="mdi:refresh" width={12} />}
              sx={{
                height: 24,
                fontSize: "11.5px",
                textTransform: "none",
                color: "text.secondary",
                "&:hover": { color: "text.primary" },
              }}
            >
              Re-run
            </Button>
          </span>
        </Tooltip>
      </Stack>

      {/* Scrollable message stream */}
      <Box
        ref={scrollerRef}
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: isDark ? alpha("#fff", 0.012) : "background.paper",
        }}
      >
        <Stack gap={1.25} sx={{ p: 1.5 }}>
          {messages.length === 0 && !isStreaming ? (
            <Stack
              alignItems="center"
              justifyContent="center"
              gap={1.25}
              sx={{
                py: 6,
                px: 2,
                textAlign: "center",
                maxWidth: 460,
                mx: "auto",
              }}
            >
              <Box
                sx={{
                  width: 44,
                  height: 44,
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  bgcolor: alpha("#7857FC", isDark ? 0.16 : 0.1),
                }}
              >
                <Iconify
                  icon="mdi:star-four-points-outline"
                  width={20}
                  sx={{ color: "#7857FC" }}
                />
              </Box>
              <Typography fontSize="14px" fontWeight={600} color="text.primary">
                No analysis yet
              </Typography>
              <Typography
                fontSize="12px"
                color="text.secondary"
                sx={{ lineHeight: 1.55 }}
              >
                Kick off a cluster-level analysis. Sub-agents will sample
                representative calls, compare against a passing baseline, and
                synthesise the result here.
              </Typography>
              <Button
                size="small"
                variant="contained"
                startIcon={<Iconify icon="mdi:star-four-points" width={13} />}
                onClick={onTriggerRun}
                sx={{
                  mt: 0.5,
                  height: 32,
                  fontSize: "12.5px",
                  fontWeight: 600,
                  borderRadius: "8px",
                  textTransform: "none",
                  // White button in dark theme, purple in light.
                  bgcolor: isDark ? "#fff" : "#7857FC",
                  color: isDark ? "#111" : "#fff",
                  px: 1.75,
                  "&:hover": { bgcolor: isDark ? "#e8e8e8" : "#6845E8" },
                  boxShadow: "none",
                }}
              >
                Analyze this cluster
              </Button>
            </Stack>
          ) : messages.length === 0 && isStreaming ? (
            <Stack
              alignItems="center"
              justifyContent="center"
              gap={1.5}
              sx={{
                py: 6,
                px: 2,
                textAlign: "center",
                maxWidth: 460,
                mx: "auto",
              }}
            >
              <Box
                sx={{
                  width: 22,
                  height: 22,
                  borderRadius: "50%",
                  border: "2px solid",
                  borderColor: alpha(ACCENT, 0.25),
                  borderTopColor: ACCENT,
                  animation: "spin 0.8s linear infinite",
                  "@keyframes spin": { to: { transform: "rotate(360deg)" } },
                }}
              />
              <Typography fontSize="13px" fontWeight={600} color="text.primary">
                {setupStatus || "Spinning up sub-agents…"}
              </Typography>
              <Typography fontSize="11.5px" color="text.secondary">
                Sampling representative calls and reading the telemetry. The
                investigation will stream in here as it goes.
              </Typography>
            </Stack>
          ) : (
            messages.map((m) => {
              const render = (() => {
                if (m.type === MESSAGE_TYPE.REASONING)
                  // Collapsed-by-default thinking, rendered as markdown.
                  return <ReasoningBlock text={m.text} />;
                if (m.type === MESSAGE_TYPE.STEP) return <StepCard step={m} />;
                if (m.type === MESSAGE_TYPE.SYNTHESIS)
                  return <SynthesisCard synthesis={m} />;
                if (m.type === MESSAGE_TYPE.RUN_HEADER)
                  return <RunHeader label={m.label} timestamp={m.timestamp} />;
                if (m.type === MESSAGE_TYPE.USER_QUESTION)
                  return <UserQuestionBubble text={m.text} />;
                if (m.type === MESSAGE_TYPE.ASSISTANT_INTRO)
                  return <AssistantIntro text={m.text} />;
                if (m.type === MESSAGE_TYPE.SUBAGENT)
                  return <SubagentCard msg={m} />;
                // suggestions are rendered in the bottom ComposeArea
                // instead of inline in the thread.
                return null;
              })();
              if (!render) return null;
              return <FadeIn key={m.id}>{render}</FadeIn>;
            })
          )}
        </Stack>
      </Box>

      {/* Sticky compose area — example chips + input, anchored to the
          bottom of the tab. Visible once the main run has produced a
          synthesis; chips seed with curated examples until the user asks
          their first follow-up, then track the latest sub-agent's
          "Try asking" set. */}
      {mainRunDone && thread?.conversationId ? (
        <ComposeArea
          suggestions={composeSuggestions}
          suggestionsHeader={composeHeader}
          disabled={isFollowUpStreaming}
          placeholder={
            isFollowUpStreaming
              ? "Falcon is investigating…"
              : "Ask Falcon a follow-up…"
          }
          onSubmit={runFollowUp}
        />
      ) : mainRunDone && thread?.cachedOnly ? (
        // Cached result from a prior session — the live conversation (and its
        // reasoning trail) isn't persisted yet, so re-run to chat / watch it.
        <Typography
          fontSize="11.5px"
          color="text.disabled"
          sx={{ textAlign: "center", flexShrink: 0, py: 0.5 }}
        >
          Showing the last analysis · Re-run to watch the full investigation and
          ask follow-ups
        </Typography>
      ) : null}
    </Stack>
  );
}
AnalyzeTab.propTypes = { error: PropTypes.object };
