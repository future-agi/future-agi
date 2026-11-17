import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CellMarkdown from "src/sections/common/CellMarkdown";
import {
  ToolCallCard,
  ToolResultCard,
} from "src/components/tool-call/ToolCallCard";
import { LazyImage } from "./MediaViewers";

const PAGE = 200;

const ROLE = {
  system: { label: "System", accent: "neutral", icon: "mdi:cog-outline" },
  user: { label: "User", accent: "info", icon: "mdi:account-outline" },
  assistant: { label: "Assistant", accent: "agent", icon: "mdi:robot-outline" },
  tool: { label: "Tool", accent: "tool", icon: "mdi:wrench-outline" },
  function: { label: "Function", accent: "tool", icon: "mdi:function-variant" },
  developer: { label: "Developer", accent: "pass", icon: "mdi:code-braces" },
};
const roleStyle = (role) =>
  ROLE[role] ?? {
    label: role ? role[0].toUpperCase() + role.slice(1) : "Message",
    accent: "neutral",
    icon: "mdi:message-outline",
  };

const parts = (content) => {
  if (content == null) return [];
  if (typeof content === "string") return [{ type: "text", text: content }];
  if (Array.isArray(content))
    return content.map((p) =>
      typeof p === "string"
        ? { type: "text", text: p }
        : p ?? { type: "text", text: "" },
    );
  return [{ type: "text", text: JSON.stringify(content, null, 2) }];
};

const Message = ({ message }) => {
  const style = roleStyle(message.role);
  const items = parts(message.content);
  const toolCalls = message.tool_calls ?? [];
  const empty =
    !items.some((p) => (p.type === "text" ? p.text : true)) &&
    !toolCalls.length;
  return (
    <Box
      data-chat-turn={message.role ?? "message"}
      sx={{
        border: "1px solid",
        borderColor: (t) => alpha(t.palette.accent[style.accent], 0.24),
        borderRadius: "6px",
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{
          px: 1.25,
          py: 0.375,
          bgcolor: (t) => alpha(t.palette.accent[style.accent], 0.08),
        }}
      >
        <Iconify
          icon={style.icon}
          width={14}
          sx={{ color: `accent.${style.accent}` }}
        />
        <Typography
          typography="s3"
          fontWeight={600}
          sx={{ color: `accent.${style.accent}` }}
        >
          {style.label}
        </Typography>
        {message.name && (
          <Typography typography="s3" color="text.secondary" noWrap>
            {`· ${message.name}`}
          </Typography>
        )}
        {message.tool_call_id && (
          <Typography
            typography="s3"
            color="text.disabled"
            sx={{ ml: "auto", fontFamily: "monospace" }}
          >
            {message.tool_call_id}
          </Typography>
        )}
      </Stack>
      <Box sx={{ px: 1.25, py: 0.75 }}>
        {message.role === "tool" && typeof message.content === "string" ? (
          <ToolResultCard content={prettyJson(message.content)} />
        ) : (
          <Stack gap={0.75}>
            {items.map((p, i) => {
              if (p.type === "text")
                return p.text ? (
                  <CellMarkdown key={i} text={p.text} fontSize={13} />
                ) : null;
              if (p.type === "image_url" && p.image_url?.url)
                return <LazyImage key={i} src={p.image_url.url} height={120} />;
              if (p.type === "input_audio")
                return (
                  <Typography key={i} typography="s2" color="text.secondary">
                    Audio input
                  </Typography>
                );
              return (
                <Typography
                  key={i}
                  typography="s2"
                  color="text.secondary"
                  sx={{ fontFamily: "monospace" }}
                >
                  {JSON.stringify(p)}
                </Typography>
              );
            })}
            {empty && (
              <Typography
                typography="s2"
                color="text.disabled"
                sx={{ fontStyle: "italic" }}
              >
                (empty)
              </Typography>
            )}
          </Stack>
        )}
        {toolCalls.length > 0 && (
          <Box sx={{ mt: items.length ? 0.75 : 0 }}>
            {toolCalls.map((tc, i) => (
              <ToolCallCard
                key={tc.id ?? i}
                toolCall={{
                  name: tc.function?.name ?? tc.name ?? "tool",
                  arguments: prettyJson(
                    tc.function?.arguments ?? tc.arguments ?? "",
                  ),
                }}
              />
            ))}
          </Box>
        )}
      </Box>
    </Box>
  );
};

Message.propTypes = { message: PropTypes.object.isRequired };

function prettyJson(v) {
  if (typeof v !== "string") return v;
  try {
    return JSON.stringify(JSON.parse(v), null, 2);
  } catch {
    return v;
  }
}

/**
 * ChatView — the `Chat` view mode: messages as role cards, tool calls as
 * cards with their JSON arguments, tool replies as result cards, image
 * parts inline. 200 messages at a time (`Showing first 200 of 500 items ·
 * Show more`). `normalizedFrom` shows `Normalized from Anthropic`.
 */
const ChatView = ({ messages, normalizedFrom }) => {
  const [limit, setLimit] = useState(PAGE);
  const list = useMemo(() => messages ?? [], [messages]);
  if (!list.length)
    return (
      <Typography
        typography="s1"
        color="text.disabled"
        sx={{ fontStyle: "italic" }}
      >
        Empty chat
      </Typography>
    );
  return (
    <Stack gap={1}>
      {normalizedFrom && (
        <Box>
          <Box
            component="span"
            sx={{
              px: 0.75,
              borderRadius: 0.5,
              typography: "s3",
              fontWeight: 500,
              lineHeight: "18px",
              bgcolor: "action.hover",
              color: "text.secondary",
            }}
          >
            {`Normalized from ${normalizedFrom}`}
          </Box>
        </Box>
      )}
      {list.slice(0, limit).map((m, i) => (
        <Message key={i} message={m} />
      ))}
      {list.length > limit && (
        <Typography typography="s2" color="text.secondary">
          {`Showing first ${limit.toLocaleString("en-US")} of ${list.length.toLocaleString("en-US")} items · `}
          <Button
            size="small"
            variant="text"
            onClick={() => setLimit((l) => l + PAGE)}
            sx={{
              p: 0,
              minWidth: 0,
              typography: "s2",
              verticalAlign: "baseline",
            }}
          >
            Show more
          </Button>
        </Typography>
      )}
    </Stack>
  );
};

ChatView.propTypes = {
  messages: PropTypes.array,
  normalizedFrom: PropTypes.string,
};

export default ChatView;
