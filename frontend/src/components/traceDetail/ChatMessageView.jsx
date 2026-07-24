import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import Markdown from "react-markdown";
import { ToolCallCard } from "src/components/tool-call/ToolCallCard";

// Role badge colors — accent colors work in both themes; bg/border use alpha for transparency
const ROLE_STYLES = {
  system: { color: "#6B7280", icon: "mdi:cog-outline" },
  user: { color: "#2563EB", icon: "mdi:account-outline" },
  assistant: { color: "#7C3AED", icon: "mdi:robot-outline" },
  model: { color: "#7C3AED", icon: "mdi:robot-outline" }, // Gemini uses "model" for assistant
  tool: { color: "#EA580C", icon: "mdi:wrench-outline" },
  developer: { color: "#16A34A", icon: "mdi:code-braces" },
  function: { color: "#EA580C", icon: "mdi:function-variant" },
};

const getRoleStyle = (role) => {
  const base = ROLE_STYLES[(role || "").toLowerCase()] || ROLE_STYLES.user;
  return {
    ...base,
    bg: alpha(base.color, 0.08),
    border: alpha(base.color, 0.2),
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
    typeof data[0] === "object" &&
    "role" in data[0]
  )
    return true;
  // Gemini/Vertex format: {contents: [{role, parts}], model: "..."}
  if (
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
    return { role: "unknown", content: [String(msg)] };
  const role = msg.role || "unknown";
  // OpenAI format: {role, content: "text" or [{type, text}]}
  if (msg.content != null) {
    if (typeof msg.content === "string")
      return { role, content: [msg.content] };
    if (Array.isArray(msg.content)) {
      return {
        role,
        content: msg.content.map((c) =>
          typeof c === "string" ? c : c?.text || JSON.stringify(c),
        ),
      };
    }
    return { role, content: [JSON.stringify(msg.content)] };
  }
  // Gemini format: {role, parts: [{text: "..."}]}
  if (msg.parts && Array.isArray(msg.parts)) {
    const texts = msg.parts.map((p) => {
      if (typeof p === "string") return p;
      if (p?.text) return p.text;
      if (p?.functionCall)
        return `**Tool call:** \`${p.functionCall.name}\`\n\`\`\`json\n${JSON.stringify(p.functionCall.args, null, 2)}\n\`\`\``;
      if (p?.function_call)
        return `**Tool call:** \`${p.function_call.name}\`\n\`\`\`json\n${JSON.stringify(p.function_call.args, null, 2)}\n\`\`\``;
      return JSON.stringify(p);
    });
    return { role, content: texts };
  }
  return { role, content: [JSON.stringify(msg)] };
}

// ── Single Message Card ──
const MessageCard = ({ message }) => {
  const role = message.role || "user";
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
      return content
        .map((p) => {
          if (typeof p === "string") return p;
          if (p?.type === "text") return p.text;
          if (p?.text) return p.text;
          return JSON.stringify(p);
        })
        .join("\n\n");
    }
    return JSON.stringify(content, null, 2);
  }, [content]);

  return (
    <Box
      sx={{
        border: `1px solid ${style.border}`,
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
          <Typography sx={{ fontSize: 11, color: style.color, opacity: 0.7 }}>
            : {name}
          </Typography>
        )}
        {toolCallId && (
          <Typography
            sx={{
              fontSize: 10,
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
          fontSize: 13,
          lineHeight: 1.5,
          color: "text.primary",
          "& p": { m: 0 },
          "& pre": { whiteSpace: "pre-wrap", fontSize: 11 },
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
  const outputMessage = useMemo(() => {
    if (attrOutputMessages?.length > 0) {
      return normalizeMessage(attrOutputMessages[0]);
    }
    if (!output) return null;
    let data = output;
    if (typeof data === "string") {
      try {
        data = JSON.parse(data);
      } catch {
        return { role: "assistant", content: [output] };
      }
    }
    // Gemini output: {content: {role: "model", parts: [{text: "..."}]}}
    if (typeof data === "object" && data.content && data.content.role) {
      return normalizeMessage(data.content);
    }
    // OpenAI: {role, content}
    if (typeof data === "object" && data.role) return normalizeMessage(data);
    if (
      typeof data === "object" &&
      data.content &&
      typeof data.content === "string"
    ) {
      return { role: "assistant", content: [data.content] };
    }
    return null;
  }, [output, attrOutputMessages]);

  const allMessages = useMemo(() => {
    const msgs = [...messages];
    // Add output as last assistant message if not already present
    if (
      outputMessage &&
      !msgs.some(
        (m) => m.role === "assistant" && m.content === outputMessage.content,
      )
    ) {
      msgs.push(outputMessage);
    }
    return msgs;
  }, [messages, outputMessage]);

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
                icon="mdi:brain"
                width={14}
                sx={{ color: "text.secondary" }}
              />
              <Typography
                sx={{ fontSize: 12, fontWeight: 600, color: "text.primary" }}
              >
                {model}
              </Typography>
              {provider && (
                <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
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
                fontSize: 11,
                color: "text.secondary",
              }}
            >
              {promptTokens ? (
                <>
                  <Typography component="span" sx={{ fontSize: 11 }}>
                    {promptTokens.toLocaleString()}
                  </Typography>
                  <Iconify
                    icon="mdi:arrow-down"
                    width={11}
                    sx={{ color: "text.secondary" }}
                  />
                  <Box component="span" sx={{ mx: 0.25 }}>
                    {" "}
                  </Box>
                  <Typography component="span" sx={{ fontSize: 11 }}>
                    {(completionTokens || 0).toLocaleString()}
                  </Typography>
                  <Iconify
                    icon="mdi:arrow-up"
                    width={11}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography component="span" sx={{ fontSize: 11, ml: 0.5 }}>
                    (Σ {(totalTokens || 0).toLocaleString()})
                  </Typography>
                </>
              ) : (
                <Typography component="span" sx={{ fontSize: 11 }}>
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
          <Box
            onClick={() => setShowAll(true)}
            sx={{
              textAlign: "center",
              py: 0.5,
              cursor: "pointer",
              color: "primary.main",
              fontSize: 12,
              fontWeight: 500,
              "&:hover": { textDecoration: "underline" },
            }}
          >
            Show {hiddenCount} more message{hiddenCount > 1 ? "s" : ""}...
          </Box>
          {visibleMessages.slice(1).map((msg, i) => (
            <MessageCard key={i + 1} message={msg} />
          ))}
        </>
      )}
      {!shouldCollapse &&
        allMessages.map((msg, i) => <MessageCard key={i} message={msg} />)}

      {showAll && allMessages.length > COLLAPSE_THRESHOLD && (
        <Box
          onClick={() => setShowAll(false)}
          sx={{
            textAlign: "center",
            py: 0.5,
            cursor: "pointer",
            color: "text.secondary",
            fontSize: 11,
            "&:hover": { color: "primary.main" },
          }}
        >
          Collapse messages
        </Box>
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
