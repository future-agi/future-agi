import PropTypes from "prop-types";
import { useState } from "react";
import { Box, IconButton, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useWorkspaceChat } from "src/api/simulate-environments/workspaceChat";
import * as fixDummy from "src/api/simulate-environments/readinessFix.dummy"; // dummy:
import { Step, Working } from "../../../buildEnvironment/console/ConsoleTurn";
import { READY_TO_RUN_COPY as COPY } from "./readiness.constants";

// The fix's own part of the builder chat: from the message the fix sent on.
const fixTurns = (turns, messageId) => {
  const from = (turns || []).findIndex((turn) => turn.id === messageId);
  return from < 0 ? [] : turns.slice(from);
};

function CompactTurn({ turn }) {
  if (turn.role === "user") {
    return (
      <Stack alignItems="flex-end" spacing={0.25}>
        <Box
          sx={{
            maxWidth: "85%",
            px: 1.25,
            py: 0.75,
            borderRadius: 1,
            bgcolor: "action.selected",
            // Waiting its turn: the builder hasn't picked it up yet.
            opacity: turn.queued ? 0.6 : 1,
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.primary",
              whiteSpace: "pre-wrap",
            }}
          >
            {turn.text}
          </Typography>
        </Box>
        {turn.queued && (
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {COPY.chatQueued}
          </Typography>
        )}
      </Stack>
    );
  }
  if (turn.role !== "builder") return null;
  return (
    <Stack spacing={0.75}>
      {(turn.steps || []).map((step, i) => (
        <CompactStep key={step.id ?? i} step={step} />
      ))}
    </Stack>
  );
}

const STEP_ICON = {
  running: { icon: "svg-spinners:180-ring", color: "text.secondary" },
  completed: { icon: "solar:check-circle-linear", color: "success.main" },
  failed: { icon: "solar:danger-circle-linear", color: "error.main" },
};

// The popup's small type for the plain steps; question cards and anything
// richer keep the builder chat's own rendering.
function CompactStep({ step }) {
  if (step.kind === "think" || step.kind === "note") {
    return (
      <Typography
        sx={{
          typography: "s3",
          color: step.kind === "think" ? "text.secondary" : "text.primary",
        }}
      >
        {step.text}
      </Typography>
    );
  }
  if (
    step.kind === "tool" ||
    step.kind === "completed" ||
    step.kind === "error"
  ) {
    let state = step.state || "completed";
    if (step.kind === "error") state = "failed";
    const mark = STEP_ICON[state] || STEP_ICON.completed;
    return (
      <Stack direction="row" spacing={0.75} alignItems="center">
        <Iconify
          icon={mark.icon}
          width={14}
          sx={{ color: mark.color, flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s3", color: "text.primary" }}>
          {step.label || step.text}
        </Typography>
        {step.result && (
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
          >
            {step.result}
          </Typography>
        )}
      </Stack>
    );
  }
  return <Step step={step} />;
}

CompactStep.propTypes = { step: PropTypes.object.isRequired };

CompactTurn.propTypes = { turn: PropTypes.object.isRequired };

// The builder chat for a running fix, inside the Run plan popup. Unscoped, so
// rows still selected on the Scenarios tab aren't edited by a reply here.
export default function FixChat({ env, fixRun, lines = [] }) {
  const chat = useWorkspaceChat(env, { source: "harness", scoped: false });
  const [draft, setDraft] = useState("");
  const view = {
    turns: fixTurns(chat.turns, fixRun?.message_id),
    send: chat.send,
    busy: chat.running,
  };
  Object.assign(view, fixDummy.useDummyChat(fixRun)); // dummy:
  const { turns, send, busy } = view;
  // Lines added while the fix runs wait in the server's queue until the
  // builder picks them up; then they arrive in the chat like any message.
  const queued = (fixRun?.queue || []).map((item, i) => ({
    id: `queued-${i}`,
    role: "user",
    text: item.message,
    queued: true,
  }));

  const submit = () => {
    const text = draft.trim();
    if (!text || chat.frozen) return;
    send(text);
    setDraft("");
  };

  return (
    <Box
      data-testid="fix-chat"
      component="section"
      aria-label={COPY.chatLabel}
      // The group's footer: the chat belongs to the whole group, not a line.
      sx={{
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        px: 1.5,
        py: 1,
      }}
    >
      <Stack spacing={1.25}>
        <Stack direction="row" spacing={0.75} alignItems="center">
          <Iconify
            icon="solar:chat-round-dots-linear"
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography sx={{ typography: "s3", color: "text.primary" }}>
            <Box component="span" sx={{ fontWeight: "fontWeightSemiBold" }}>
              {COPY.chatLabel}
            </Box>
            {lines.length > 0 && (
              <Box component="span" sx={{ color: "text.secondary" }}>
                {` · ${COPY.chatFixing(lines)}`}
              </Box>
            )}
          </Typography>
        </Stack>
        {[...turns, ...queued].map((turn) => (
          <CompactTurn key={turn.id} turn={turn} />
        ))}
        {busy && <Working label={COPY.chatWorking} />}
      </Stack>
      <Stack
        direction="row"
        spacing={0.5}
        alignItems="flex-end"
        sx={{
          mt: 1.25,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.paper",
          px: 1,
          py: 0.5,
        }}
      >
        <TextField
          fullWidth
          multiline
          maxRows={3}
          size="small"
          variant="standard"
          placeholder={chat.frozen ? chat.frozenReason : COPY.chatReply}
          disabled={chat.frozen}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          InputProps={{ disableUnderline: true, sx: { typography: "s3" } }}
          inputProps={{ "aria-label": COPY.chatReply }}
        />
        <IconButton
          size="small"
          aria-label={COPY.chatSend}
          disabled={!draft.trim() || chat.frozen}
          onClick={submit}
        >
          <Iconify icon="solar:arrow-up-linear" width={16} />
        </IconButton>
      </Stack>
    </Box>
  );
}

FixChat.propTypes = {
  env: PropTypes.object.isRequired,
  // The running fix; its message_id is where the fix's conversation starts.
  fixRun: PropTypes.object,
  // Names of the lines the fix is working on.
  lines: PropTypes.arrayOf(PropTypes.string),
};
