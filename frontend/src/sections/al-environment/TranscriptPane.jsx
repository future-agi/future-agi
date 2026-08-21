import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Chip, Collapse, IconButton, Stack, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import { ALK_MONO } from "./alkTokens";
import ThinkingStrip from "./ThinkingStrip";
import Markdown from "./parts/Markdown";
import JsonView from "./JsonView";

const ToolStep = ({ message }) => (
  <Box
    sx={{
      borderLeft: "2px solid",
      borderColor: "divider",
      pl: 1.5,
    }}
  >
    <Stack direction="row" spacing={0.75} alignItems="baseline">
      <Box component="span" sx={{ color: message.ok === false ? "error.main" : "success.main" }}>
        {message.ok === false ? "✗" : "✓"}
      </Box>
      <Typography variant="caption" sx={{ fontFamily: ALK_MONO, color: "text.primary" }}>
        {message.tool}
      </Typography>
      {message.target && (
        <Typography variant="caption" sx={{ fontFamily: ALK_MONO, color: "text.secondary" }}>
          {message.target}
        </Typography>
      )}
    </Stack>
    {message.text && (
      <Typography
        variant="caption"
        sx={{ fontFamily: ALK_MONO, color: "text.secondary", whiteSpace: "pre-wrap" }}
      >
        {String(message.text).slice(0, 160)}
      </Typography>
    )}
    {message.detail !== undefined && message.detail !== null && (
      <JsonView value={message.detail} />
    )}
  </Box>
);

ToolStep.propTypes = {
  message: PropTypes.object.isRequired,
};

/**
 * A run of tool calls, folded away by default. The steps are the work, not the
 * conclusion, and most readers only want them when something went wrong — so a
 * run with a failure says so on the closed header.
 */
const StepsBlock = ({ items, live }) => {
  // A block that is still being streamed stays open so the work is visible as
  // it happens; it collapses once the turn moves past it.
  const [open, setOpen] = useState(Boolean(live));
  const failed = items.filter((one) => one.ok === false).length;

  useEffect(() => {
    if (!live) setOpen(false);
  }, [live]);

  return (
    <Box>
      <Stack
        direction="row"
        spacing={0.75}
        alignItems="center"
        onClick={() => setOpen((was) => !was)}
        sx={{ cursor: "pointer", userSelect: "none", color: "text.secondary" }}
      >
        <Box component="span" sx={{ fontSize: 10 }}>{open ? "▾" : "▸"}</Box>
        <Typography variant="caption" sx={{ fontFamily: ALK_MONO }}>
          {items.length} step{items.length === 1 ? "" : "s"}
          {failed ? ` · ${failed} failed` : ""}
        </Typography>
      </Stack>
      <Collapse in={open}>
        <Stack spacing={1} sx={{ mt: 0.75 }}>
          {items.map((one, index) => (
            // eslint-disable-next-line react/no-array-index-key
            <ToolStep key={index} message={one} />
          ))}
        </Stack>
      </Collapse>
    </Box>
  );
};

StepsBlock.propTypes = {
  items: PropTypes.array.isRequired,
  live: PropTypes.bool,
};

/** Consecutive tool messages fold into one block; everything else stands alone. */
const grouped = (messages) => {
  const blocks = [];
  messages.forEach((message) => {
    const last = blocks[blocks.length - 1];
    if (message.role !== "error" && message.role !== "verdict" && message.tool) {
      if (last?.kind === "steps") last.items.push(message);
      else blocks.push({ kind: "steps", items: [message] });
    } else {
      blocks.push({ kind: "one", message });
    }
  });
  return blocks;
};

/**
 * Read-only in slice 1. Slice 2 appends streamed events to the same `messages` array,
 * so the renderer deliberately knows nothing about where the messages came from.
 */
const TranscriptPane = ({ messages, hasSession, thinking, spentUsd, onDismissError }) => {
  const theme = useTheme();
  const foot = useRef(null);

  // Follow the stream. Without this a long turn writes itself off the bottom of the pane.
  useEffect(() => {
    // Guarded: jsdom has no layout, so scrollIntoView is absent under test.
    foot.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, thinking]);

  if (!hasSession) {
    return (
      <Box sx={{ px: 3, py: 2 }}>
        <Typography variant="caption" sx={{ fontFamily: ALK_MONO, color: "text.secondary" }}>
          TESTER
        </Typography>
        <Typography variant="body2" sx={{ mt: 1 }}>
          No session yet. Press &quot;+ new&quot; to start one, then say which agent to test and
          where its code lives.
        </Typography>
      </Box>
    );
  }

  if (messages.length === 0 && !thinking) {
    return (
      <Box sx={{ px: 3, py: 2 }}>
        <Typography variant="caption" sx={{ fontFamily: ALK_MONO, color: "text.secondary" }}>
          TESTER
        </Typography>
        <Typography variant="body2" sx={{ mt: 1 }}>
          Which agent would you like to test? Tell me where its code lives.
        </Typography>
      </Box>
    );
  }

  const blocks = grouped(messages);

  return (
    <Stack spacing={1.5} sx={{ px: 3, py: 2, overflowY: "auto" }}>
      {blocks.map((block, index) => (
        // History entries have no id of their own; order is their identity.
        // eslint-disable-next-line react/no-array-index-key
        <Box
          key={index}
          sx={
            block.kind === "one" && block.message.role === "you"
              ? { alignSelf: "flex-end", textAlign: "right", maxWidth: "80%" }
              : { alignSelf: "stretch" }
          }
        >
          {block.kind === "steps" ? (
            <StepsBlock
              items={block.items}
              live={Boolean(thinking) && index === blocks.length - 1}
            />
          ) : block.message.role === "error" ? (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                borderLeft: "2px solid",
                borderColor: "accent.fail",
                pl: 1.25,
              }}
            >
              <Typography
                sx={{ fontFamily: ALK_MONO, fontSize: 12.5, color: "error.main", flexGrow: 1 }}
              >
                {block.message.text}
              </Typography>
              {onDismissError && (
                <IconButton
                  size="small"
                  aria-label="dismiss"
                  onClick={() => onDismissError(block.message)}
                  sx={{ color: "error.main", p: 0.25 }}
                >
                  <Box component="span" sx={{ fontSize: 14, lineHeight: 1 }}>✕</Box>
                </IconButton>
              )}
            </Stack>
          ) : block.message.role === "verdict" ? (
            <Stack direction="row" alignItems="center" spacing={1}>
              <Chip
                size="small"
                color={block.message.detail?.passed ? "success" : "error"}
                label={block.message.detail?.passed ? "pass" : "fail"}
              />
              <Typography variant="body2" sx={{ fontFamily: ALK_MONO }}>
                {block.message.detail?.scenario || block.message.text}
              </Typography>
              {block.message.detail?.of !== undefined && (
                <Typography variant="caption" color="text.secondary">
                  {block.message.detail.met}/{block.message.detail.of} checks
                </Typography>
              )}
            </Stack>
          ) : block.message.role === "you" ? (
            // Same bubble the Falcon AI chat draws for the person's side.
            <Box
              sx={{
                bgcolor: alpha(
                  theme.palette.primary.main,
                  theme.palette.mode === "dark" ? 0.15 : 0.08,
                ),
                borderRadius: "10px",
                px: 2,
                py: 1.25,
                display: "inline-block",
                textAlign: "left",
              }}
            >
              <Typography
                variant="body2"
                sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.8 }}
              >
                {block.message.text}
              </Typography>
            </Box>
          ) : (
            <>
              <Typography variant="caption" sx={{ fontFamily: ALK_MONO, color: "text.secondary" }}>
                {(block.message.role || "tester").toUpperCase()}
              </Typography>
              <Markdown text={block.message.text} />
            </>
          )}
        </Box>
      ))}
      {thinking && <ThinkingStrip label={thinking} spentUsd={spentUsd} />}
      <Box ref={foot} />
    </Stack>
  );
};

TranscriptPane.propTypes = {
  messages: PropTypes.array.isRequired,
  hasSession: PropTypes.bool,
  thinking: PropTypes.string,
  spentUsd: PropTypes.number,
  onDismissError: PropTypes.func,
};

export default TranscriptPane;
