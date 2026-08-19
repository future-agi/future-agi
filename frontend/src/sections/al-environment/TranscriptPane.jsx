import { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "./alkTokens";
import ThinkingStrip from "./ThinkingStrip";
import Markdown from "./parts/Markdown";
import JsonView from "./JsonView";

/**
 * Read-only in slice 1. Slice 2 appends streamed events to the same `messages` array,
 * so the renderer deliberately knows nothing about where the messages came from.
 */
const TranscriptPane = ({ messages, hasSession, thinking, spentUsd }) => {
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

  return (
    <Stack spacing={1.5} sx={{ px: 3, py: 2, overflowY: "auto" }}>
      {messages.map((message, index) => (
        // History entries have no id of their own; order is their identity.
        // eslint-disable-next-line react/no-array-index-key
        <Box key={index}>
          {message.role === "error" ? (
            <Typography
              sx={{
                fontFamily: ALK_MONO,
                fontSize: 12.5,
                color: "accent.fail",
                borderLeft: "2px solid",
                borderColor: "accent.fail",
                pl: 1.25,
              }}
            >
              {message.text}
            </Typography>
          ) : message.role === "verdict" ? (
            <Stack direction="row" alignItems="center" spacing={1}>
              <Chip
                size="small"
                color={message.detail?.passed ? "success" : "error"}
                label={message.detail?.passed ? "pass" : "fail"}
              />
              <Typography variant="body2" sx={{ fontFamily: ALK_MONO }}>
                {message.detail?.scenario || message.text}
              </Typography>
              {message.detail?.of !== undefined && (
                <Typography variant="caption" color="text.secondary">
                  {message.detail.met}/{message.detail.of} checks
                </Typography>
              )}
            </Stack>
          ) : message.tool ? (
            <Box
              sx={{
                borderLeft: "2px solid",
                borderColor: "divider",
                pl: 1.5,
              }}
            >
              <Stack direction="row" spacing={0.75} alignItems="baseline">
                <Box component="span" sx={{ color: message.ok === false ? "accent.fail" : "accent.pass" }}>
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
          ) : (
            <>
              <Typography variant="caption" sx={{ fontFamily: ALK_MONO, color: "text.secondary" }}>
                {(message.role || "tester").toUpperCase()}
              </Typography>
              {message.role === "you" ? (
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                  {message.text}
                </Typography>
              ) : (
                <Markdown text={message.text} />
              )}
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
};

export default TranscriptPane;
