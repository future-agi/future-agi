import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

const MESSAGE_LABELS = {
  user: "You",
  agent: "Agent",
  assistant: "Agent",
  system: "System",
};

/** Render the server's conversation messages as text, never raw HTML or inferred event output. */
export default function RunConversationMessages({ runId, messages }) {
  const visible = Array.isArray(messages)
    ? messages.filter(
        (message) =>
          message &&
          typeof message.text === "string" &&
          message.text.trim().length > 0 &&
          (message.conversationId === undefined ||
            message.conversationId === runId),
      )
    : [];
  const retained = visible.slice(-200);
  return (
    <Box component="section" aria-label="Conversation" sx={{ mt: 2 }}>
      <Typography variant="subtitle2">Conversation</Typography>
      {retained.length === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          No conversation messages are available yet.
        </Typography>
      ) : (
        <Stack spacing={1.5} sx={{ mt: 1, maxHeight: 560, overflow: "auto" }}>
          {visible.length > retained.length ? (
            <Typography variant="caption" color="text.secondary">
              Showing the latest {retained.length} messages.
            </Typography>
          ) : null}
          {retained.map((message, index) => (
            <Box
              component="article"
              key={message.id || `${message.role}-${index}`}
              aria-label={MESSAGE_LABELS[message.role] || "Message"}
              sx={{
                p: 1.5,
                borderRadius: 1,
                bgcolor:
                  message.role === "user"
                    ? "action.hover"
                    : "background.neutral",
              }}
            >
              <Typography variant="caption" color="text.secondary">
                {MESSAGE_LABELS[message.role] || "Message"}
              </Typography>
              <Typography
                variant="body2"
                sx={{
                  mt: 0.5,
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                }}
              >
                {message.text}
              </Typography>
            </Box>
          ))}
        </Stack>
      )}
    </Box>
  );
}

RunConversationMessages.propTypes = {
  runId: PropTypes.string.isRequired,
  messages: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      conversationId: PropTypes.string,
      role: PropTypes.string,
      text: PropTypes.string,
    }),
  ),
};
