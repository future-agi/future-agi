import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import Markdown from "react-markdown";
import { ToolCallCard } from "src/components/tool-call/ToolCallCard";
import { normalizeMessageRole } from "src/utils/message-role";
import { messageBlockText, serializeMessageValue } from "src/utils/message-text";

// Role badge colors — keyed into palette.accent so each mode gets its own hue;
// bg/border derive from the resolved accent via alpha for transparency
const ROLE_STYLES = {
  system: { accent: "neutral", icon: "solar:settings-linear" },
  user: { accent: "info", icon: "solar:user-linear" },
  assistant: { accent: "agent", icon: "solar:bot-linear" },
  model: { accent: "agent", icon: "solar:bot-linear" }, // Gemini uses "model" for assistant
  tool: { accent: "tool", icon: "solar:toolbox-linear" },
  developer: { accent: "pass", icon: "solar:code-linear" },
  function: { accent: "tool", icon: "solar:programming-linear" },
};

const getRoleStyle = (role) => {
  const key = normalizeMessageRole(role).toLowerCase();
  const base = Object.hasOwn(ROLE_STYLES, key) ? ROLE_STYLES[key] : ROLE_STYLES.user;
  return {
    ...base,
    color: `accent.${base.accent}`,
    bg: (t) => alpha(t.palette.accent[base.accent], 0.08),
    border: (t) => alpha(t.palette.accent[base.accent], 0.2),
  };
};

/** Detect OpenAI-format messages array */
export function isOpenAIMessages(input) {
  if (!input) return false;
  let data = input;
  if (typeof input === "string") {
    try {
      data = JSON.parse(input);
    } catch {
      return false;
    }
  }
  // Direct array of messages: [{role, content}] or [{role, parts}]
  if (
    Array.isArray(data) &&
    data.length > 0 &&
    data[0] != null &&
    typeof data[0] === "object" &&
    "role" in data[0]
  )
    return true;
  // Gemini/Vertex format: {contents: [{role, parts}], model: "..."}
  if (
    data != null &&
    typeof data === "object" &&
    !Array.isArray(data) &&
    data.contents &&
    Array.isArray(data.contents)
  )
    return true;
  return false;
}

/** Parse messages from input — handles OpenAI, Gemini/Vertex, and plain formats */
function parseMessages(input) {
  if (!input) return [];
  let data = input;
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch {
      return [];
    }
  }
  // Direct array: [{role, content}] or [{role, parts}]
  if (Array.isArray(data)) {
    return data.map(normalizeMessage);
  }
  // Gemini format: {contents: [{role, parts}], model: "..."}
  if (
    data != null &&
    typeof data === "object" &&
    data.contents &&
    Array.isArray(data.contents)
  ) {
    return data.contents.map(normalizeMessage);
  }
  return [];
}

/** Normalize a message to {role, content} format (handles Gemini parts) */
function normalizeMessage(msg) {
  if (!msg || typeof msg !== "object")
    return { role: "unknown", content: [messageBlockText(msg)] };
  const role = normalizeMessageRole(msg.role);
  // OpenAI format: {role, content: "text" or [{type, text}]}
  if (msg.content != null) {
    if (typeof msg.content === "string")
      return { role, content: [msg.content] };
    if (Array.isArray(msg.content)) {
      return {
        role,
        content: msg.content.map(messageBlockText),
      };
    }
    return { role, content: [serializeMessageValue(msg.content)] };
  }
  // Gemini format: {role, parts: [{text: "..."}]}
  if (msg.parts && Array.isArray(msg.parts)) {
    const texts = msg.parts.map((p) => {
      if (typeof p === "string") return p;
      if (p && Object.hasOwn(p, "text")) return messageBlockText(p);
      const call = p?.functionCall || p?.function_call;
      if (call)
        return `**Tool call:** \`${messageBlockText(call.name)}\`\n\`\`\`json\n${serializeMessageValue(call.args, 2)}\n\`\`\``;
      return messageBlockText(p);
    });
    return { role, content: texts };
  }
  return { role, content: [serializeMessageValue(msg)] };
}

// ── Single Message Card ──
const MessageCard = ({ message }) => {
  const role = normalizeMessageRole(message.role, "user");
  const style = getRoleStyle(role);
  const content = message.content;
  const toolCalls = message.tool_calls || message.toolCalls;
  const toolCallId = message.tool_call_id || message.toolCallId;
  const name = message.name;

  // Content can be string, array of strings, array of content parts, or null
  const textContent = useMemo(() => {
    if (content == null) return "";
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      return content.map(messageBlockText).join("\n\n");
    }
    return serializeMessageValue(content, 2);
  }, [content]);

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: style.border,
        borderRadius: "6px",
        overflow: "hidden",
      }}
    >
      {/* Role header — excluded from find-in-page */}
      <Box
        data-search-skip="true"
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 1.5,
          py: 0.5,
          bgcolor: style.bg,
        }}
      >
        <Iconify icon={style.icon} width={14} sx={{ color: style.color }} />
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 600,
            color: style.color,
            textTransform: "capitalize",
          }}
        >
          {role}
        </Typography>
        {name && (
          <Typography sx={{ fontSize: 12, color: style.color, opacity: 0.7 }}>
            : {name}
          </Typography>
        )}
        {toolCallId && (
          <Typography
            sx={{
              fontSize: 12,
              color: "text.disabled",
              ml: "auto",
              fontFamily: "monospace",
            }}
          >
            {toolCallId}
          </Typography>
        )}
      </Box>

      {/* Content */}
      <Box
        sx={{
          px: 1.5,
          py: 1,
          // Message text at the UI default (12/18), like the rest of the
          // drawer; markdown blocks inherit it rather than the 13px body.
          fontSize: 12,
          lineHeight: "18px",
          color: "text.primary",
          "& p, & li, & td, & th": {
            fontSize: "inherit",
            lineHeight: "inherit",
          },
          "& p": { m: 0 },
          "& pre": { whiteSpace: "pre-wrap", fontSize: 12 },
        }}
      >
        {textContent ? (
          <Markdown>{textContent}</Markdown>
        ) : (
          <Typography
            sx={{ fontSize: 12, color: "text.disabled", fontStyle: "italic" }}
          >
            (empty)
          </Typography>
        )}
      </Box>

      {/* Tool calls within this message */}
      {toolCalls?.length > 0 && (
        <Box sx={{ px: 1.5, pb: 1 }}>
          {toolCalls.map((tc, i) => (
            <ToolCallCard
              key={tc.id || i}
              toolCall={{
                name: tc.function?.name || tc.name || "tool",
                arguments: tc.function?.arguments || tc.arguments || "",
              }}
            />
          ))}
        </Box>
      )}
    </Box>
  );
};

MessageCard.propTypes = { message: PropTypes.object.isRequired };

// ── ChatMessageView ──
const ChatMessageView = ({
  input,
  output,
  model,
  provider,
  promptTokens,
  completionTokens,
  totalTokens,
  attrInputMessages,
  attrOutputMessages,
}) => {
  const [showAll, setShowAll] = useState(false);
  // Prefer attribute-extracted messages (from span_attributes like llm.inputMessages)
  // Fall back to parsing raw input as OpenAI messages format
  const messages = useMemo(() => {
    if (attrInputMessages?.length > 0) return attrInputMessages;
    return parseMessages(input);
  }, [input, attrInputMessages]);

  // Parse output — prefer attribute-extracted, fall back to raw output
  const outputMessages = useMemo(() => {
    if (attrOutputMessages?.length > 0) {
      return attrOutputMessages.map(normalizeMessage);
    }
    if (output == null || output === "") return [];
    let data = output;
    if (typeof data === "string") {
      try {
        data = JSON.parse(data);
      } catch {
        return [{ role: "assistant", content: [output] }];
      }
    }
    if (data == null || (Array.isArray(data) && data.length === 0)) return [];
    // Role-bearing arrays are separate messages; other arrays are reply blocks.
    if (
      Array.isArray(data) &&
      data.every((message) => typeof message?.role === "string" && message.role)
    ) {
      return data.map(normalizeMessage);
    }
    // Raw OpenAI completion: {choices: [{message: {role, content}}]}.
    if (typeof data.choices?.[0]?.message?.role === "string") {
      return [normalizeMessage(data.choices[0].message)];
    }
    // Gemini output: {content: {role: "model", parts: [{text: "..."}]}}
    if (typeof data === "object" && data.content && data.content.role) {
      return [normalizeMessage(data.content)];
    }
    // OpenAI: {role, content}
    if (typeof data === "object" && data.role) return [normalizeMessage(data)];
    if (
      typeof data === "object" &&
      data.content &&
      typeof data.content === "string"
    ) {
      return [{ role: "assistant", content: [data.content] }];
    }
    // Raw output may be content blocks or a JSON scalar, not a message envelope.
    // Keep unknown blocks as JSON rather than silently hiding the model's reply.
    return [normalizeMessage({ role: "assistant", content: data })];
  }, [output, attrOutputMessages]);

  const allMessages = useMemo(
    () => [...messages, ...outputMessages],
    [messages, outputMessages],
  );

  const COLLAPSE_THRESHOLD = 4;
  const shouldCollapse = allMessages.length > COLLAPSE_THRESHOLD && !showAll;
  const visibleMessages = shouldCollapse
    ? [allMessages[0], ...allMessages.slice(-3)]
    : allMessages;
  const hiddenCount = allMessages.length - visibleMessages.length;

  return (
    <Stack spacing={1}>
      {/* Model + tokens header */}
      {(model || totalTokens) && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            flexWrap: "wrap",
          }}
        >
          {model && (
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                px: 1,
                py: 0.25,
                bgcolor: "background.neutral",
                borderRadius: "4px",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Iconify
                icon="solar:brain-linear"
                width={14}
                sx={{ color: "text.secondary" }}
              />
              <Typography
                sx={{ fontSize: 12, fontWeight: 600, color: "text.primary" }}
              >
                {model}
              </Typography>
              {provider && (
                <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
                  ({provider})
                </Typography>
              )}
            </Box>
          )}
          {(promptTokens || completionTokens || totalTokens) && (
            <Box
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.25,
                fontSize: 12,
                color: "text.secondary",
              }}
            >
              {promptTokens ? (
                <>
                  <Typography component="span" sx={{ fontSize: 12 }}>
                    {promptTokens.toLocaleString()}
                  </Typography>
                  <Iconify
                    icon="solar:arrow-down-linear"
                    width={12}
                    sx={{ color: "text.secondary" }}
                  />
                  <Box component="span" sx={{ mx: 0.25 }}>
                    {" "}
                  </Box>
                  <Typography component="span" sx={{ fontSize: 12 }}>
                    {(completionTokens || 0).toLocaleString()}
                  </Typography>
                  <Iconify
                    icon="solar:arrow-up-linear"
                    width={12}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography component="span" sx={{ fontSize: 12, ml: 0.5 }}>
                    (Σ {(totalTokens || 0).toLocaleString()})
                  </Typography>
                </>
              ) : (
                <Typography component="span" sx={{ fontSize: 12 }}>
                  {(totalTokens || 0).toLocaleString()} tokens
                </Typography>
              )}
            </Box>
          )}
        </Box>
      )}

      {/* Messages */}
      {shouldCollapse && (
        <>
          <MessageCard message={visibleMessages[0]} />
          <Button
            size="small"
            variant="text"
            fullWidth
            onClick={() => setShowAll(true)}
          >
            Show {hiddenCount} more message{hiddenCount > 1 ? "s" : ""}...
          </Button>
          {visibleMessages.slice(1).map((msg, i) => (
            <MessageCard key={i + 1} message={msg} />
          ))}
        </>
      )}
      {!shouldCollapse &&
        allMessages.map((msg, i) => <MessageCard key={i} message={msg} />)}

      {showAll && allMessages.length > COLLAPSE_THRESHOLD && (
        <Button
          size="small"
          variant="text"
          fullWidth
          onClick={() => setShowAll(false)}
        >
          Collapse messages
        </Button>
      )}
    </Stack>
  );
};

ChatMessageView.propTypes = {
  input: PropTypes.any,
  output: PropTypes.any,
  model: PropTypes.string,
  provider: PropTypes.string,
  promptTokens: PropTypes.number,
  completionTokens: PropTypes.number,
  totalTokens: PropTypes.number,
  attrInputMessages: PropTypes.array,
  attrOutputMessages: PropTypes.array,
};

export default React.memo(ChatMessageView);
