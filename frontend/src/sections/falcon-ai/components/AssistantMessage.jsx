import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import Iconify from "src/components/iconify";
import useFalconStore from "../store/useFalconStore";
import { groupBlocks } from "../helpers/toolTrail";
import TextBlock from "./TextBlock";
import ThinkingTrail from "./ThinkingTrail";
import CompletionCard from "./CompletionCard";

export default function AssistantMessage({ message, onFeedback }) {
  const [hovered, setHovered] = useState(false);
  const [feedback, setFeedback] = useState(message.feedback || null);
  const toolCalls = message.tool_calls || [];
  const blocks = message.blocks || [];
  const hasBlocks = blocks.length > 0;
  const isStreaming = useFalconStore((s) => s.isStreaming);
  const streamingMessageId = useFalconStore((s) => s.streamingMessageId);
  const isThisStreaming = isStreaming && streamingMessageId === message.id;
  const isEmpty =
    !message.content && toolCalls.length === 0 && !hasBlocks && !message.error;
  const hasRunningTool = toolCalls.some((tc) => tc.status === "running");

  return (
    <Box
      sx={{
        px: 0,
        py: 0.75,
        maxWidth: 800,
        width: "100%",
        mx: "auto",
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Content area — no bubble, no avatar */}
      <Box sx={{ minWidth: 0, overflow: "hidden" }}>
        {/* Loading indicator when empty and streaming */}
        {isEmpty && isThisStreaming && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              py: 1,
            }}
          >
            <Box
              sx={{
                display: "flex",
                gap: 0.5,
                alignItems: "center",
              }}
            >
              {[0, 1, 2].map((i) => (
                <Box
                  key={i}
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    bgcolor: "text.disabled",
                    animation: "falcon-dot-pulse 1.4s ease-in-out infinite",
                    animationDelay: `${i * 0.2}s`,
                    "@keyframes falcon-dot-pulse": {
                      "0%, 80%, 100%": {
                        opacity: 0.3,
                        transform: "scale(0.8)",
                      },
                      "40%": { opacity: 1, transform: "scale(1)" },
                    },
                  }}
                />
              ))}
            </Box>
          </Box>
        )}

        {/* Content blocks — sequential rendering */}
        {hasBlocks ? (
          groupBlocks(blocks).map((block) => {
            if (block.type === "text" && block.content) {
              return <TextBlock key={block.id} content={block.content} />;
            }
            if (block.type === "trail") {
              return (
                <ThinkingTrail
                  key={block.id}
                  toolCalls={block.toolCalls}
                  isStreaming={isThisStreaming}
                />
              );
            }
            if (block.type === "completion_card" && block.card) {
              return <CompletionCard key={block.id} card={block.card} />;
            }
            return null;
          })
        ) : (
          <>
            {toolCalls.length > 0 && (
              <ThinkingTrail
                toolCalls={toolCalls}
                isStreaming={isThisStreaming}
              />
            )}
            <TextBlock content={message.content} />
          </>
        )}

        {message.error && (
          <Typography
            variant="body2"
            sx={{
              mt: 0.5,
              color: "error.main",
              fontSize: 14,
            }}
          >
            {message.error}
          </Typography>
        )}

        {/* Completion card — only for legacy messages without blocks */}
        {!hasBlocks && message.completion_card && (
          <CompletionCard card={message.completion_card} />
        )}

        {/* Persistent loading indicator while streaming — shows between tool
            calls and during LLM thinking. The trail carries its own live line,
            so the dots stay out of the way while a tool is running. */}
        {isThisStreaming && !isEmpty && !hasRunningTool && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              mt: 1.5,
              mb: 0.5,
            }}
          >
            <Box sx={{ display: "flex", gap: 0.4, alignItems: "center" }}>
              {[0, 1, 2].map((i) => (
                <Box
                  key={i}
                  sx={{
                    width: 5,
                    height: 5,
                    borderRadius: "50%",
                    bgcolor: "text.disabled",
                    animation: "falcon-dot-pulse 1.4s ease-in-out infinite",
                    animationDelay: `${i * 0.2}s`,
                    "@keyframes falcon-dot-pulse": {
                      "0%, 80%, 100%": {
                        opacity: 0.3,
                        transform: "scale(0.8)",
                      },
                      "40%": { opacity: 1, transform: "scale(1)" },
                    },
                  }}
                />
              ))}
            </Box>
          </Box>
        )}
      </Box>

      {/* Actions — show on hover, feedback persists */}
      {!isThisStreaming &&
        (message.content || toolCalls.length > 0 || hasBlocks) && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.25,
              mt: 0.5,
              height: 24,
              opacity: hovered || feedback ? 1 : 0,
              transition: "opacity 0.15s ease",
            }}
          >
            <IconButton
              size="small"
              onClick={() => {
                navigator.clipboard.writeText(message.content || "");
              }}
              sx={{
                p: 0.4,
                color: "text.disabled",
                "&:hover": { color: "text.secondary" },
              }}
              title="Copy"
            >
              <Iconify icon="mdi:content-copy" width={14} />
            </IconButton>

            <IconButton
              size="small"
              onClick={() => {
                const val = feedback === "up" ? null : "up";
                setFeedback(val);
                onFeedback?.(message.id, val);
              }}
              sx={{
                p: 0.4,
                color: feedback === "up" ? "primary.main" : "text.disabled",
                "&:hover": {
                  color: feedback === "up" ? "primary.main" : "text.secondary",
                },
              }}
              title="Good response"
            >
              <Iconify
                icon={
                  feedback === "up" ? "mdi:thumb-up" : "mdi:thumb-up-outline"
                }
                width={14}
              />
            </IconButton>

            <IconButton
              size="small"
              onClick={() => {
                const val = feedback === "down" ? null : "down";
                setFeedback(val);
                onFeedback?.(message.id, val);
              }}
              sx={{
                p: 0.4,
                color: feedback === "down" ? "error.main" : "text.disabled",
                "&:hover": {
                  color: feedback === "down" ? "error.main" : "text.secondary",
                },
              }}
              title="Bad response"
            >
              <Iconify
                icon={
                  feedback === "down"
                    ? "mdi:thumb-down"
                    : "mdi:thumb-down-outline"
                }
                width={14}
              />
            </IconButton>
          </Box>
        )}
    </Box>
  );
}

AssistantMessage.propTypes = {
  message: PropTypes.shape({
    id: PropTypes.string,
    content: PropTypes.string,
    created_at: PropTypes.string,
    error: PropTypes.string,
    feedback: PropTypes.string,
    completion_card: PropTypes.object,
    tool_calls: PropTypes.array,
    blocks: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string,
        type: PropTypes.oneOf(["text", "tool_call", "completion_card"]),
        content: PropTypes.string,
        toolCall: PropTypes.object,
        card: PropTypes.object,
      }),
    ),
  }).isRequired,
  onFeedback: PropTypes.func,
};
