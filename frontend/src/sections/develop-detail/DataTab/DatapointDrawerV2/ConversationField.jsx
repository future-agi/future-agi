import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import {
  failingTurnNote,
  parseConversation,
} from "src/sections/develop-detail/TaskSuite/itemModel";
import countTurns from "src/sections/common/Values/countTurns";
import ConversationThread from "./ConversationThread";

// Turns shown before `Show all`: through the failing turn (+ the reply to
// it), at least 4.
const MIN_SHOWN = 4;

/**
 * IT-R3 G5 P2: a conversation-shaped field in the Row tab reads as the chat
 * (the Conversation tab's bubbles, failing turn named), not as a JSON tree.
 * Long conversations show `Showing turns 1–4 of 8 · Show all`; the JSON is
 * on the Raw tab.
 */
const ConversationField = ({ value, production = null, onOpenTab }) => {
  const conv = useMemo(() => parseConversation(value), [value]);
  const failNote = useMemo(
    () => failingTurnNote(conv, production),
    [conv, production],
  );
  const [all, setAll] = useState(false);
  if (!conv) return null;
  const total = countTurns(conv.turns);
  const turnMessages = conv.turns.filter(
    (turn) => !["tool", "system"].includes(turn.role),
  );
  const upTo = Math.max(MIN_SHOWN, (failNote?.turn ?? 0) + 1);
  const shown = all || total <= 8 ? total : Math.min(total, upTo);
  const view =
    shown < total ? { ...conv, turns: turnMessages.slice(0, shown) } : conv;
  return (
    <Box data-testid="conversation-field" sx={{ px: 1, py: 1 }}>
      <ConversationThread
        conv={view}
        toolSource={value}
        compact
        failNote={failNote}
        replayTurn={failNote?.turn ?? conv.failingTurn ?? undefined}
      />
      {(shown < total || onOpenTab) && (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ mt: 1, flexWrap: "wrap" }}
        >
          {shown < total && (
            <Typography typography="s3" color="text.secondary">
              {`Showing turns 1–${shown} of ${total}`}
            </Typography>
          )}
          {shown < total && (
            <ButtonBase
              onClick={() => setAll(true)}
              data-testid="conversation-field-show-all"
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "primary.main",
                "&:hover": { textDecoration: "underline" },
              }}
            >
              Show all
            </ButtonBase>
          )}
          {onOpenTab && (
            <ButtonBase
              onClick={onOpenTab}
              sx={{
                typography: "s3",
                color: "text.secondary",
                ml: "auto",
                "&:hover": { textDecoration: "underline" },
              }}
            >
              Open the Conversation tab
            </ButtonBase>
          )}
        </Stack>
      )}
    </Box>
  );
};

ConversationField.propTypes = {
  value: PropTypes.any,
  production: PropTypes.object,
  onOpenTab: PropTypes.func,
};

export default ConversationField;
