import PropTypes from "prop-types";
import { useEffect, useReducer, useRef, useState } from "react";
import { Box, Stack, Typography, TextField, IconButton } from "@mui/material";
import { visuallyHidden } from "@mui/utils";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";

import { ENV_TAB_RAIL_HEIGHT } from "../../environmentOptions";
import { CONSOLE_COPY } from "../build.constants";
import { JumpToLatestBar, StopOrbit } from "./BuilderProgress";
import { Turn } from "./ConsoleTurn";
// Voice input is temporarily disabled (Web Speech is blocked in Brave); re-enable
// with a server-side transcription path. Kept for that follow-up.
// import VoiceInput from "./VoiceInput";
import { subscribeComposerScaffold } from "./composerScaffoldBus";

const INITIAL = { draft: "", scaffolds: [] };

function composerReducer(state, action) {
  switch (action.type) {
    case "draft":
      return { ...state, draft: action.value };
    case "scaffold":
      return state.scaffolds.includes(action.text)
        ? state
        : { ...state, scaffolds: [...state.scaffolds, action.text] };
    case "unscaffold":
      return { ...state, scaffolds: state.scaffolds.filter((_, i) => i !== action.index) };
    case "clear":
      return INITIAL;
    default:
      return state;
  }
}

// Tight on purpose: rows are about 34px, so anything wider pulls back someone
// reading the last few rows.
const NEAR_BOTTOM_PX = 8;

// Things worth reading. Tool rows and other progress only keep the ship flying,
// or the new-message ping would replace it almost at once. Rebuild and rollback
// cards are their own system turns, with no steps.
const MESSAGE_KINDS = new Set(["note", "ask", "error"]);

const countMessages = (turns) =>
  (turns || []).reduce(
    (total, turn) =>
      total +
      (turn.role === "system" ? 1 : 0) +
      (turn.steps || []).filter((s) => MESSAGE_KINDS.has(s.kind)).length,
    0,
  );

/**
 * The console, as a chat.
 *
 * Read like a conversation rather than a log: a measured column, the user's own
 * turns in a bubble and the builder's in plain text, tool calls folded into
 * quiet rows. Steps stream in one at a time so you can watch the work and
 * interrupt it.
 *
 * `preComposer` is kept for parity with the designer (an intake questionnaire
 * or a selection-context chip pins there).
 *
 * `frozen` locks the composer until the environment is Live: it blocks input
 * the same way `running` does, but stays blocked until the env finishes
 * building rather than until the last message returns.
 *
 * `onCollapse` adds a collapse button to the header. `active` is false while
 * the console is collapsed (mounted but hidden); it skips the auto-scroll then
 * and jumps to the latest turn once the console is shown again.
 *
 * Scrolling up stops the list following new events, so the user keeps their
 * place; a bar above the composer shows work continuing or a new message.
 */
export default function BuilderConsole({
  turns,
  running,
  onSend,
  onStop,
  canStop = false,
  working = canStop,
  stopping = false,
  preComposer,
  frozen = false,
  frozenReason,
  onCollapse,
  collapseRef,
  active = true,
}) {
  const [state, dispatch] = useReducer(composerReducer, INITIAL);
  const { draft, scaffolds } = state;
  const listRef = useRef(null);
  const lastScrollRef = useRef({ top: 0, height: 0 });
  const [following, setFollowing] = useState(true);
  const messageCount = countMessages(turns);
  const [seenMessages, setSeenMessages] = useState(messageCount);

  useEffect(
    () => subscribeComposerScaffold((text) => dispatch({ type: "scaffold", text })),
    [],
  );

  // Follow the transcript only as it grows, not when a card's state changes.
  const count = turns?.length ?? 0;
  const lastTurn = turns?.[count - 1];
  const lastId = lastTurn?.id;
  const lastSteps = lastTurn?.steps?.length;
  useEffect(() => {
    if (!active || !following) return;
    // Scroll only the message list. scrollIntoView would also scroll every
    // ancestor, including the collapsing column's clip box mid-animation.
    const list = listRef.current;
    list?.scrollTo?.({ top: list.scrollHeight, behavior: "smooth" });
  }, [count, lastId, lastSteps, running, canStop, active, following]);

  // Sending from any path (composer, a question card, Stop) and reopening the
  // console both return to the latest turn. Adjusted during render, so the next
  // paint already follows.
  const [prev, setPrev] = useState({ running, active });
  if (prev.running !== running || prev.active !== active) {
    if ((running && !prev.running) || (active && !prev.active)) setFollowing(true);
    setPrev({ running, active });
  }

  const handleScroll = () => {
    const list = listRef.current;
    if (!list || !active) return;
    const { scrollTop, scrollHeight, clientHeight } = list;
    if (scrollHeight - scrollTop - clientHeight < NEAR_BOTTOM_PX) {
      setFollowing(true);
    } else if (scrollTop < lastScrollRef.current.top && scrollHeight === lastScrollRef.current.height) {
      // Only moving up counts as leaving: a big event can land mid smooth scroll
      // and put the list far from the bottom without the user doing anything. A
      // changed height means text rewrapped and moved the list, not the user.
      setFollowing(false);
      setSeenMessages(messageCount);
    }
    lastScrollRef.current = { top: scrollTop, height: scrollHeight };
  };

  // Frozen === the env is not Live yet, so the builder can't accept edits. It
  // blocks the composer exactly like `running`, but persists across turns.
  const blocked = running || frozen;
  const reason = frozenReason || CONSOLE_COPY.frozen;

  const hasContent = draft.trim() || scaffolds.length > 0;

  // "Busy" drives the working indicator: the brief send round-trip (running) OR a
  // turn still in flight (canStop = a queued/responding message waiting on the
  // agent). Distinct from `blocked`, so the composer stays usable while waiting.
  // `working` narrows it when Stop also covers work that only waits.
  const busy = running || working;
  const hasNewMessage = !following && messageCount > seenMessages;
  // One button: Stop while there's something to stop, Send as soon as the user types.
  const showStop = onStop && (canStop || running) && !frozen && !hasContent;

  const send = () => {
    const text = draft.trim();
    const scaffoldText = scaffolds.join(". ");
    const combined = [scaffoldText, text].filter(Boolean).join(scaffoldText && text ? ". " : "");
    if (!combined || blocked) return;
    dispatch({ type: "clear" });
    onSend?.(combined);
  };

  return (
    <Stack sx={{ height: "100%", minWidth: 0 }}>
      {/* Same height as the workspace tab rail, so the two headers line up. */}
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{
          flexShrink: 0, pl: 2, pr: 1, height: ENV_TAB_RAIL_HEIGHT, boxSizing: "content-box",
          borderBottom: "1px solid", borderColor: "divider",
        }}
      >
        <Box
          sx={{
            width: 24, height: 24, borderRadius: "4px", display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: "action.selected",
            color: "text.secondary",
          }}
        >
          <Iconify icon="solar:chat-round-line-linear" width={13} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography role="status" noWrap sx={{ typography: "s1", color: "text.secondary" }}>
            {frozen
              ? reason
              : stopping
                ? CONSOLE_COPY.stopping
                : busy
                  ? CONSOLE_COPY.working
                  : CONSOLE_COPY.idle}
          </Typography>
        </Box>
        {onCollapse && (
          // Only while open, so the tooltip can't linger over the right pane
          // after a click hides the console.
          <CustomTooltip show={active} size="small" arrow title={CONSOLE_COPY.collapse}>
            <IconButton
              ref={collapseRef}
              aria-label={CONSOLE_COPY.collapse}
              onClick={onCollapse}
              size="small"
              sx={{ flexShrink: 0, color: "text.secondary" }}
            >
              <Iconify icon="solar:double-alt-arrow-left-linear" width={16} />
            </IconButton>
          </CustomTooltip>
        )}
      </Stack>

      {/* overflow-wrap is inherited, so every message, note, error and tool row
          below breaks a long unbroken token (a URL, an id, pasted JSON) instead
          of pushing out of the chat column. */}
      <Box
        ref={listRef}
        data-testid="builder-console-list"
        onScroll={handleScroll}
        sx={{ flex: 1, minWidth: 0, overflowY: "auto", overflowWrap: "anywhere", px: 3, py: 3 }}
      >
        <Stack spacing={3}>
          {(turns || []).length === 0 && !busy ? (
            <Stack alignItems="center" spacing={1} sx={{ py: 6, opacity: 0.7 }}>
              <Box
                sx={{
                  width: 36, height: 36, borderRadius: "50%", display: "grid", placeItems: "center",
                  bgcolor: "action.selected",
                  color: "text.secondary",
                }}
              >
                <Iconify icon="solar:chat-round-line-linear" width={17} />
              </Box>
              <Typography sx={{ typography: "s1", color: "text.secondary", textAlign: "center", maxWidth: 320 }}>
                {CONSOLE_COPY.empty}
              </Typography>
            </Stack>
          ) : (
            (turns || []).map((turn) => <Turn key={turn.id} turn={turn} />)
          )}
        </Stack>
      </Box>

      <Box role="status" sx={visuallyHidden}>
        {hasNewMessage ? CONSOLE_COPY.newMessage : ""}
      </Box>
      {!following && (hasNewMessage || busy) && (
        <JumpToLatestBar hasNewMessage={hasNewMessage} onClick={() => setFollowing(true)} />
      )}

      {/* Slot for anything a caller wants to pin above the composer (the intake
          questionnaire or the selection-context chip drops in here). */}
      {preComposer && <Box sx={{ px: 3, pb: 1 }}>{preComposer}</Box>}

      <Box sx={{ px: 3, pb: 3, pt: 1 }}>
        <Box
          sx={{
            p: 1.5, borderRadius: "8px", border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            transition: "border-color 0.15s ease",
            "&:focus-within": { borderColor: "primary.main" },
          }}
        >
          {scaffolds.length > 0 && (
            <Stack direction="row" spacing={0.75} sx={{ flexWrap: "wrap", rowGap: 0.75, mb: 1 }}>
              {scaffolds.map((s, i) => (
                <Stack
                  key={`${s}-${i}`}
                  direction="row" alignItems="center" spacing={0.5}
                  sx={{
                    pl: 1, pr: 0.5, py: 0.25, borderRadius: "4px",
                    bgcolor: "action.selected",
                    maxWidth: "100%",
                  }}
                >
                  <Iconify icon="solar:magic-stick-3-linear" width={12} sx={{ color: "text.secondary", flexShrink: 0 }} />
                  <Typography
                    sx={{
                      typography: "s3", fontWeight: 500, color: "text.secondary",
                      maxWidth: 260, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                    }}
                  >
                    {s}
                  </Typography>
                  <IconButton
                    size="small"
                    aria-label={`Remove ${s}`}
                    onClick={() => dispatch({ type: "unscaffold", index: i })}
                    sx={{ p: 0, ml: 0.25 }}
                  >
                    <Iconify icon="solar:close-circle-linear" width={13} sx={{ color: "text.disabled" }} />
                  </IconButton>
                </Stack>
              ))}
            </Stack>
          )}

          {/* Row 1: the text field on its own line so long drafts get the full width. */}
          <TextField
            fullWidth
            multiline
            maxRows={8}
            variant="standard"
            placeholder={frozen ? reason : CONSOLE_COPY.placeholder}
            value={draft}
            disabled={blocked}
            onChange={(e) => dispatch({ type: "draft", value: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
            InputProps={{ disableUnderline: true, sx: { typography: "m3", px: 0.5, py: 0.5 } }}
          />

          {/* Row 2: toolbar — flex-spacer · stop · send. */}
          <Stack direction="row" alignItems="center" spacing={0.25} sx={{ mt: 0.5, pl: 0.25 }}>
            {/* Voice input disabled for now: the Web Speech API is blocked in
                Brave, so it only works in Chrome/Edge/Safari. Re-enable once a
                server-side transcription endpoint (record -> /audio/transcriptions)
                is wired so it works in every browser. VoiceInput.jsx is kept. */}
            {/* <VoiceInput onTranscript={(text) => dispatch({ type: "draft", value: text })} disabled={blocked} /> */}

            <Box flex={1} />

            {showStop ? (
              <Box sx={{ position: "relative", display: "inline-flex" }}>
                {busy && <StopOrbit />}
                <IconButton
                  aria-label="Stop"
                  title={CONSOLE_COPY.stop}
                  disabled={running || stopping}
                  onClick={onStop}
                  size="small"
                  sx={{
                    color: "text.secondary",
                    border: "1px solid", borderColor: busy ? "transparent" : "divider",
                  }}
                >
                  <Iconify icon="solar:stop-bold" width={13} />
                </IconButton>
              </Box>
            ) : (
              <IconButton
                aria-label="Send"
                disabled={!hasContent || blocked}
                onClick={send}
                size="small"
                sx={{
                  bgcolor: hasContent && !blocked ? "primary.main" : undefined,
                  color: hasContent && !blocked ? "primary.contrastText" : undefined,
                  "&:hover": { bgcolor: hasContent && !blocked ? "primary.dark" : undefined },
                  "&.Mui-disabled": { bgcolor: "action.selected", color: "text.disabled" },
                }}
              >
                <Iconify icon="solar:arrow-up-bold" width={15} />
              </IconButton>
            )}
          </Stack>
        </Box>
      </Box>
    </Stack>
  );
}

BuilderConsole.propTypes = {
  turns: PropTypes.array,
  running: PropTypes.bool,
  onSend: PropTypes.func,
  onStop: PropTypes.func,
  canStop: PropTypes.bool,
  working: PropTypes.bool,
  // A Stop is on its way.
  stopping: PropTypes.bool,
  preComposer: PropTypes.node,
  frozen: PropTypes.bool,
  frozenReason: PropTypes.string,
  onCollapse: PropTypes.func,
  // Attached to the collapse button so focus can return to it on reopen.
  collapseRef: PropTypes.oneOfType([PropTypes.func, PropTypes.shape({ current: PropTypes.any })]),
  active: PropTypes.bool,
};
