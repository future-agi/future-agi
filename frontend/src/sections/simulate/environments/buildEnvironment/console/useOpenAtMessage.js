import { useCallback, useEffect, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";

import { CONSOLE_COPY } from "../build.constants";
import { subscribeBuilderChatFocus } from "./builderChatFocusBus";

/**
 * Opens the chat at a message another part of the workspace asks for, reading
 * the messages around it when it isn't held. `onFound` gets its node; without
 * one the chat opens at the latest, and says why when a message was named.
 */
export default function useOpenAtMessage({
  active,
  listRef,
  count,
  older,
  onFound,
  onLatest,
}) {
  // { messageId, reading, gaveUp }: `gaveUp` is why it can't be shown.
  const [asked, setAsked] = useState(null);
  const handlers = useRef();
  handlers.current = { onFound, onLatest, asked };

  useEffect(
    () => subscribeBuilderChatFocus((messageId) => setAsked({ messageId })),
    [],
  );

  useEffect(() => {
    if (!active || !asked) return;
    const id = asked.messageId && CSS.escape(asked.messageId);
    const target =
      id &&
      listRef.current?.querySelector(
        `[data-message-id="${id}"], [data-answer-id="${id}"]`,
      );
    if (target) {
      setAsked(null);
      handlers.current.onFound(target);
      return;
    }
    const openAtLatest = (why) => {
      setAsked(null);
      handlers.current.onLatest();
      if (why) enqueueSnackbar(why, { variant: "warning" });
    };
    const { failed, missing } = CONSOLE_COPY.search;
    if (!id || !count) openAtLatest();
    else if (asked.gaveUp) openAtLatest(asked.gaveUp);
    // All that is left is read, or it is held and the chat doesn't show it.
    else if (!older?.hasOlder || older.holds(asked.messageId)) {
      openAtLatest(missing);
    } else if (!asked.reading) {
      const next = { ...asked, reading: true };
      setAsked(next);
      const giveUp = (why) =>
        setAsked((now) => (now === next ? { ...now, gaveUp: why } : now));
      older
        .open(asked.messageId, () => handlers.current.asked === next)
        .then((found) => !found && giveUp(missing))
        .catch(() => giveUp(failed));
    }
  }, [active, asked, count, listRef, older]);

  return {
    asked: Boolean(asked),
    looking: Boolean(active && asked?.reading),
    stop: useCallback(() => setAsked(null), []),
  };
}
