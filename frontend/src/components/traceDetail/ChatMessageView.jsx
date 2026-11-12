import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import Markdown from "react-markdown";
import { ToolCallCard } from "src/components/tool-call/ToolCallCard";

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
  const base = ROLE_STYLES[(role || "").toLowerCase()] || ROLE_STYLES.user;
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
  // Complete OpenAI requests or Gemini/Vertex contents.
  if (
    data &&
    typeof data === "object" &&
    !Array.isArray(data) &&
    (Array.isArray(data.messages) || Array.isArray(data.contents))
  )
    return true;
  return false;
}

/** System turns among the attribute-extracted input messages. */
function attributeSystemTurns(attrInputMessages) {
  return (attrInputMessages || [])
    .filter((msg) => msg?.role === "system")
    .map(normalizeMessage);
}

// A system prompt recorded outside the captured turns goes first, without
// replacing captured turns and their tool metadata. Nothing is added when the
// turns have their own system turn, or when no turn is readable (those are
// left for the attribute fallback).
function withSystemTurns(captured, systemMessages) {
  if (
    !captured.some((msg) => msg.role !== "unknown") ||
    captured.some((msg) => msg.role === "system")
  ) {
    return captured;
  }
  return [...systemMessages, ...captured];
}

/** Parse messages from input — handles OpenAI, Gemini/Vertex, and plain formats */
function parseMessages(input, attrInputMessages = []) {
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
  // Complete request: {messages: [{role, content, tool_calls, ...}]}.
  // LangChain records a batch of message lists: {messages: [[...]]}.
  if (Array.isArray(data?.messages)) {
    const captured = data.messages.flat().map(normalizeMessage);
    // Anthropic keeps the system prompt beside the messages.
    const hasRequestSystem = Array.isArray(data.system)
      ? data.system.length > 0
      : Boolean(data.system);
    return withSystemTurns(
      captured,
      hasRequestSystem
        ? [normalizeMessage({ role: "system", content: data.system })]
        : attributeSystemTurns(attrInputMessages),
    );
  }
  // Gemini format: {contents: [{role, parts}], model: "..."}
  // Gemini system instructions live outside contents.
  if (Array.isArray(data?.contents)) {
    return withSystemTurns(
      data.contents.map(normalizeMessage),
      attributeSystemTurns(attrInputMessages),
    );
  }
  return [];
}

/** OTel GenAI gen_ai.system_instructions (text parts) as a system turn. */
function systemInstructionTurns(instructions) {
  let value = instructions;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      // Plain-text instructions are shown as they are.
    }
  }
  if (value == null) return [];
  const text = (Array.isArray(value) ? value : [value])
    .map((part) => {
      if (typeof part === "string") return part;
      if (typeof part?.content === "string") return part.content;
      if (typeof part?.text === "string") return part.text;
      return JSON.stringify(part);
    })
    .filter((part) => part && part.trim())
    .join("\n\n");
  return text ? [{ role: "system", content: [text] }] : [];
}

// A prompt recorded as plain text or as a non-message object, rather than as
// a message list, is still the turn's input: show it as one user message.
function promptMessages(input) {
  if (isOpenAIMessages(input)) return [];
  if (typeof input === "string") {
    return input.trim() ? [{ role: "user", content: [input] }] : [];
  }
  if (
    input &&
    typeof input === "object" &&
    !Array.isArray(input) &&
    Object.keys(input).length > 0
  ) {
    const json = JSON.stringify(input, null, 2);
    return [{ role: "user", content: [`\`\`\`json\n${json}\n\`\`\``] }];
  }
  return [];
}

// Image addresses the card may load; any other address stays as text.
const IMAGE_SRC_PATTERN = /^(?:https?:\/\/|data:image\/|blob:)/i;

/** The address of an image content part, or null for any other part. */
function imagePartUrl(part) {
  if (!part || typeof part !== "object") return null;
  const url = [
    part.image_url?.url, // OpenAI chat: {type: "image_url", image_url: {url}}
    part.image_url, // OpenAI responses: {type: "input_image", image_url}
    part.image?.url,
    part.image, // flattened message_content.image
    part["image.url"], // flattened message_content.image.url
    part["image.image.url"], // flattened OpenInference image
    part["image_url.url"],
  ].find((value) => typeof value === "string" && value.trim());
  return url && IMAGE_SRC_PATTERN.test(url.trim()) ? url.trim() : null;
}

const LANGCHAIN_ROLES = new Map([
  ["human", "user"],
  ["ai", "assistant"],
  ["system", "system"],
  ["tool", "tool"],
  ["function", "function"],
]);

/** LangChain serializes a message as {lc, type, id: [..., class], kwargs}. */
const isLangChainMessage = (msg) =>
  msg.lc != null &&
  Array.isArray(msg.id) &&
  msg.kwargs != null &&
  typeof msg.kwargs === "object";

/** A LangChain-serialized message as a {role, content, tool_calls} message. */
function fromLangChainMessage(msg) {
  const { kwargs } = msg;
  const className = String(msg.id[msg.id.length - 1] || "");
  const kind = String(
    kwargs.type || className.replace(/Message(?:Chunk)?$/, ""),
  ).toLowerCase();
  const role =
    (kind === "chat" && kwargs.role) || LANGCHAIN_ROLES.get(kind) || kind;
  const toolCalls = kwargs.tool_calls?.length
    ? kwargs.tool_calls
    : kwargs.additional_kwargs?.tool_calls;
  return {
    role: role || "unknown",
    content: kwargs.content,
    ...(toolCalls?.length > 0
      ? {
          // LangChain tool calls carry their arguments as `args`.
          tool_calls: toolCalls.map((call) =>
            call?.args !== undefined ? { ...call, arguments: call.args } : call,
          ),
        }
      : {}),
    ...(kwargs.tool_call_id != null
      ? { tool_call_id: kwargs.tool_call_id }
      : {}),
    ...(kwargs.name != null ? { name: kwargs.name } : {}),
  };
}

// OpenLLMetry records tool calls as flat `tool_calls.N.<field>` message keys.
const DOTTED_TOOL_CALL = /^tool_calls\.(\d+)\.(.+)$/;

/** Fold flat `tool_calls.N.*` keys into a tool_calls list. */
function foldDottedToolCalls(msg) {
  if (Array.isArray(msg.tool_calls) || Array.isArray(msg.toolCalls)) return msg;
  const calls = {};
  const rest = {};
  Object.entries(msg).forEach(([key, value]) => {
    const match = DOTTED_TOOL_CALL.exec(key);
    if (!match) {
      rest[key] = value;
      return;
    }
    const call = calls[match[1]] || (calls[match[1]] = {});
    // `function.name` / `function.arguments` nest like the OpenAI shape.
    const [field, ...nested] = match[2].split(".");
    if (nested.length) {
      const parent = typeof call[field] === "object" ? call[field] : {};
      call[field] = { ...parent, [nested.join(".")]: value };
    } else {
      call[field] = value;
    }
  });
  const indices = Object.keys(calls).sort((a, b) => a - b);
  if (!indices.length) return msg;
  return { ...rest, tool_calls: indices.map((index) => calls[index]) };
}

/** A tool result as text: strings as is, text blocks joined, else JSON. */
function resultText(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value
      .map((block) => {
        if (typeof block === "string") return block;
        if (typeof block?.text === "string") return block.text;
        return JSON.stringify(block);
      })
      .join("\n\n");
  }
  return `\`\`\`json\n${JSON.stringify(value, null, 2)}\n\`\`\``;
}

/**
 * A tool call, tool result or reasoning block, from Anthropic content blocks
 * or OTel GenAI parts; null for any other block.
 */
function toolOrReasoningBlock(block) {
  switch (block?.type) {
    case "tool_use": // Anthropic
      return {
        toolCall: { id: block.id, name: block.name, arguments: block.input },
      };
    case "tool_call": // OTel GenAI (LangChain content blocks use args)
      return {
        toolCall: {
          id: block.id,
          name: block.name,
          arguments: block.arguments ?? block.args,
        },
      };
    case "tool_result": // Anthropic
      return {
        toolCallId: block.tool_use_id,
        text: [block.is_error ? "**Error:**" : "", resultText(block.content)]
          .filter(Boolean)
          .join("\n\n"),
      };
    case "tool_call_response": // OTel GenAI
      return { toolCallId: block.id, text: resultText(block.response) };
    case "thinking": // Anthropic
      return { text: `**Thinking:** ${block.thinking ?? ""}` };
    case "reasoning": // OTel GenAI
      return { text: `**Reasoning:** ${resultText(block.content)}` };
    default:
      return null;
  }
}

/**
 * Content blocks or parts as display entries. Tool calls become tool_calls
 * (unless the message lists its own) and a tool result's ID its
 * tool_call_id, so tool cards and the header show them instead of JSON.
 */
function withBlocks(message, blocks, displayBlock) {
  const content = [];
  const toolCalls = [];
  let toolCallId = null;
  blocks.forEach((block) => {
    const entry = toolOrReasoningBlock(block);
    if (!entry) {
      content.push(displayBlock(block));
      return;
    }
    if (entry.toolCall) toolCalls.push(entry.toolCall);
    if (entry.text != null) content.push(entry.text);
    if (toolCallId == null && entry.toolCallId != null) {
      toolCallId = entry.toolCallId;
    }
  });
  const hasToolCalls = (message.tool_calls || message.toolCalls)?.length > 0;
  const hasToolCallId = (message.tool_call_id ?? message.toolCallId) != null;
  return {
    ...message,
    content,
    ...(toolCalls.length > 0 && !hasToolCalls ? { tool_calls: toolCalls } : {}),
    ...(toolCallId != null && !hasToolCallId
      ? { tool_call_id: toolCallId }
      : {}),
  };
}

/** Normalize content while retaining tool calls, result IDs and message names. */
function normalizeMessage(raw) {
  if (!raw || typeof raw !== "object")
    return { role: "unknown", content: [String(raw)] };
  const msg = isLangChainMessage(raw)
    ? fromLangChainMessage(raw)
    : foldDottedToolCalls(raw);
  const message = { ...msg, role: msg.role || "unknown" };
  // OpenAI format: {role, content: "text" or [{type, text}]}
  if (msg.content != null) {
    if (typeof msg.content === "string")
      return { ...message, content: [msg.content] };
    if (Array.isArray(msg.content)) {
      return withBlocks(message, msg.content, (c) => {
        if (typeof c === "string") return c;
        if (c?.text) return c.text;
        // Image parts stay whole so the card shows the picture, not its JSON.
        return imagePartUrl(c) ? c : JSON.stringify(c);
      });
    }
    return { ...message, content: [JSON.stringify(msg.content)] };
  }
  // Gemini parts use text; OTel GenAI text parts use content.
  if (msg.parts && Array.isArray(msg.parts)) {
    return withBlocks(message, msg.parts, (p) => {
      if (typeof p === "string") return p;
      if (typeof p?.text === "string") return p.text;
      if (p?.type === "text" && typeof p.content === "string") return p.content;
      if (p?.functionCall)
        return `**Tool call:** \`${p.functionCall.name}\`\n\`\`\`json\n${JSON.stringify(p.functionCall.args, null, 2)}\n\`\`\``;
      if (p?.function_call)
        return `**Tool call:** \`${p.function_call.name}\`\n\`\`\`json\n${JSON.stringify(p.function_call.args, null, 2)}\n\`\`\``;
      return JSON.stringify(p);
    });
  }
  // Tool-only assistant messages have no text; the tool cards carry their body.
  if ((msg.tool_calls || msg.toolCalls)?.length > 0) {
    return { ...message, content: [] };
  }
  return { ...message, content: [JSON.stringify(msg)] };
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
  const { textContent, imageUrls } = useMemo(() => {
    if (content == null) return { textContent: "", imageUrls: [] };
    if (typeof content === "string")
      return { textContent: content, imageUrls: [] };
    if (Array.isArray(content)) {
      const texts = [];
      const urls = [];
      content.forEach((p) => {
        if (typeof p === "string") texts.push(p);
        else if (p?.type === "text" || p?.text) texts.push(p.text);
        else if (imagePartUrl(p)) urls.push(imagePartUrl(p));
        else texts.push(JSON.stringify(p));
      });
      return { textContent: texts.join("\n\n"), imageUrls: urls };
    }
    return { textContent: JSON.stringify(content, null, 2), imageUrls: [] };
  }, [content]);
  // A tool-call-only turn shows its tool cards instead of the empty note.
  const isEmpty =
    !textContent && imageUrls.length === 0 && !(toolCalls?.length > 0);

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
        {textContent && <Markdown>{textContent}</Markdown>}
        {imageUrls.length > 0 && (
          <Box
            sx={{
              display: "flex",
              flexWrap: "wrap",
              gap: 1,
              mt: textContent ? 1 : 0,
            }}
          >
            {imageUrls.map((url, i) => (
              <Box
                key={i}
                component="img"
                src={url}
                alt="Image in this message"
                loading="lazy"
                sx={{
                  display: "block",
                  maxWidth: "100%",
                  maxHeight: 240,
                  objectFit: "contain",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                }}
              />
            ))}
          </Box>
        )}
        {isEmpty && (
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
  systemInstructions,
}) => {
  const [showAll, setShowAll] = useState(false);
  // Captured histories retain tool metadata that flattened attributes can lose,
  // so they win unless no captured turn is readable. With neither, a plain
  // prompt stays visible (e.g. output-only attributes). System instructions
  // recorded beside the turns go first when no system turn is present.
  const messages = useMemo(() => {
    const captured = parseMessages(input, attrInputMessages);
    let turns = captured;
    if (
      attrInputMessages?.length > 0 &&
      !captured.some((msg) => msg.role !== "unknown")
    ) {
      turns = attrInputMessages.map(normalizeMessage);
    } else if (!captured.length) {
      turns = promptMessages(input);
    }
    if (turns.some((msg) => msg.role === "system")) return turns;
    return [...systemInstructionTurns(systemInstructions), ...turns];
  }, [input, attrInputMessages, systemInstructions]);

  // Parse output — prefer attribute-extracted, fall back to raw output.
  // Always keep a list: a span may record multiple reply messages.
  const outputMessages = useMemo(() => {
    if (attrOutputMessages?.length > 0) {
      return attrOutputMessages.map(normalizeMessage);
    }
    if (!output) return [];
    let data = output;
    if (typeof data === "string") {
      try {
        data = JSON.parse(data);
      } catch {
        return [{ role: "assistant", content: [output] }];
      }
    }
    if (Array.isArray(data)) return data.map(normalizeMessage);
    // Complete OpenAI response: retain every recorded choice message.
    if (Array.isArray(data?.choices)) {
      return data.choices
        .filter((choice) => choice?.message)
        .map((choice) => normalizeMessage(choice.message));
    }
    // Gemini output: {content: {role: "model", parts: [{text: "..."}]}}
    if (data?.content?.role) {
      return [normalizeMessage(data.content)];
    }
    // OpenAI: {role, content}
    if (data?.role) return [normalizeMessage(data)];
    if (typeof data?.content === "string") {
      return [normalizeMessage({ ...data, role: "assistant" })];
    }
    return [];
  }, [output, attrOutputMessages]);

  // Repeated text can be a distinct recorded turn; retain each output entry.
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
  systemInstructions: PropTypes.any,
};

export default React.memo(ChatMessageView);
