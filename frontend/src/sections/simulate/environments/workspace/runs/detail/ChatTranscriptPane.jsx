import PropTypes from "prop-types";
import React, { useState } from "react";
import { Box, Stack, Typography, Tab, TextField, InputAdornment } from "@mui/material";

import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import CallParticipants from "src/components/VoiceDetailDrawerV2/CallParticipants";
import OtherPartyName from "src/components/VoiceDetailDrawerV2/OtherPartyName";
import { DEFAULT_ROLE_LABELS } from "src/components/VoiceDetailDrawerV2/constants";
import {
  isOtherRole,
  otherParties,
} from "src/components/VoiceDetailDrawerV2/transcriptUtils";

const ROLE_LABEL = {
  agent: "Assistant",
  customer: "Customer",
  tool: "Tool call",
};

// Voice turns carry a real `at` (seconds into the recording); chat turns don't,
// so a made-up mm:ss there would misrepresent the transcript. Fall back to the
// turn ordinal instead of inventing a timestamp.
function turnStamp(turn, index) {
  if (turn.at == null) return `#${index + 1}`;
  const m = Math.floor(turn.at / 60);
  const s = Math.floor(turn.at % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

// A single tool call rendered inline under the turn that made it. Chat messages
// carry `tool_calls`; a voice/transcript row folds tool rows into the same
// field. Absence is the common case, so this renders nothing unless present.
function ToolCall({ call }) {
  const fn = call?.function || call?.name || call;
  const name = typeof fn === "object" ? fn?.name : fn;
  const args = typeof fn === "object" ? fn?.arguments : call?.arguments;
  return (
    <Stack
      direction="row"
      spacing={1}
      alignItems="flex-start"
      sx={{ mt: 0.5, px: 1, py: 0.5, borderRadius: "4px", bgcolor: "background.neutral" }}
    >
      <Iconify icon="solar:code-linear" width={13} sx={{ color: "text.subtitle", mt: "2px", flexShrink: 0 }} />
      <Typography
        sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary", wordBreak: "break-word" }}
      >
        {name || "tool"}
        {args ? `(${typeof args === "string" ? args : JSON.stringify(args)})` : "()"}
      </Typography>
    </Stack>
  );
}
ToolCall.propTypes = { call: PropTypes.any };

// The left pane: the conversation, in order, filterable by speaker and text.
// Tool calls hang under the turn that issued them so a "said, not done" gap is
// visible in the transcript itself rather than buried in a side tab.
export default function ChatTranscriptPane({ turns, customerName }) {
  const [roleFilter, setRoleFilter] = useState("all");
  const [query, setQuery] = useState("");
  const others = otherParties(turns);

  const shown = turns.filter((s) => {
    if (
      roleFilter === "others"
        ? !isOtherRole(s.role)
        : roleFilter !== "all" && s.role !== roleFilter
    )
      return false;
    if (query && !s.text?.toLowerCase().includes(query.toLowerCase())) return false;
    return true;
  });

  return (
    <>
      {others.length > 0 && (
        <Box sx={{ px: 2, pt: 1.25 }}>
          <CallParticipants others={others} customerName={customerName} />
        </Box>
      )}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1.25 }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Search transcript"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: "text.subtitle" }} />
              </InputAdornment>
            ),
          }}
        />
        <SegmentedTabs value={roleFilter} onChange={(_, v) => setRoleFilter(v)} sx={{ flexShrink: 0 }}>
          <Tab value="all" label="All" />
          <Tab value="agent" label="Assistant" />
          <Tab value="customer" label="Customer" />
          {others.length > 0 && (
            <Tab value="others" label={DEFAULT_ROLE_LABELS.others} />
          )}
        </SegmentedTabs>
      </Stack>

      <Stack sx={{ px: 2, pb: 2 }}>
        {shown.map((s, i) => (
          <Box
            key={i}
            sx={{
              borderLeft: "2px solid",
              // Each speaker keeps its own mark, so a tool call never reads as the customer.
              borderColor:
                s.role === "agent"
                  ? "text.secondary"
                  : s.role === "tool"
                    ? "warning.main"
                    : "text.disabled",
              bgcolor:
                s.role === "agent"
                  ? "action.hover"
                  : s.role === "tool"
                    ? "transparent"
                    : "background.neutral",
              px: 1.75,
              py: 1.25,
            }}
          >
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}
            >
              {turnStamp(s, i)} ·{" "}
              {isOtherRole(s.role) ? (
                <OtherPartyName party={s} color="text.subtitle" />
              ) : (
                ROLE_LABEL[s.role] || s.role
              )}
            </Typography>
            {s.text && <Typography sx={{ typography: "s1", mt: 0.25 }}>{s.text}</Typography>}
            {Array.isArray(s.toolCalls) &&
              s.toolCalls.map((c, ci) => <ToolCall key={ci} call={c} />)}
          </Box>
        ))}
        {shown.length === 0 && (
          <Typography sx={{ px: 1.75, py: 2, typography: "s2", color: "text.subtitle" }}>
            {turns.length === 0 ? "No messages captured for this conversation." : "No turns match that filter."}
          </Typography>
        )}
      </Stack>
    </>
  );
}
ChatTranscriptPane.propTypes = {
  customerName: PropTypes.string,
  turns: PropTypes.arrayOf(
    PropTypes.shape({
      role: PropTypes.string,
      speakerName: PropTypes.string,
      actorRole: PropTypes.string,
      channel: PropTypes.string,
      text: PropTypes.string,
      toolCalls: PropTypes.any,
    }),
  ).isRequired,
};
