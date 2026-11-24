import { useCallback, useEffect, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";

import { CONSOLE_COPY } from "../build.constants";
import { subscribeBuilderChatFocus } from "./builderChatFocusBus";

// shortcut: looks back at most this many pages (200 messages each) for the
// message, since every page read is drawn; a server read around a message id
// would reach any of them.
const SEARCH_PAGES = 5;

/**
 * Opens the chat at a message another part of the workspace asks for, reading
 * older pages until it is there. `onFound` gets its node; without one the chat
 * opens at the latest, and says why when a message was named.
 */
export default function useOpenAtMessage({
  active,
  listRef,
  count,
  older,
  onFound,
  onLatest,
}) {
  // { messageId, pages, from, failed }: `from` is where the messages started
  // when the last page was asked for.
  const [asked, setAsked] = useState(null);
  const handlers = useRef();
  handlers.current = { onFound, onLatest };

  useEffect(
    () =>
      subscribeBuilderChatFocus((messageId) =>
        setAsked({ messageId, pages: 0 }),
      ),
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
      if (why) enqueueSnackbar(why, { variant: "info" });
    };
    if (!id || !count) openAtLatest();
    else if (asked.failed) openAtLatest(CONSOLE_COPY.search.failed);
    else if (!older?.hasOlder) openAtLatest(CONSOLE_COPY.search.missing);
    // A page is on its way, or landed and not yet drawn.
    else if (older.loading || asked.from === older.oldest) return;
    else if (asked.pages >= SEARCH_PAGES) {
      openAtLatest(CONSOLE_COPY.search.tooFar);
    } else {
      const next = { ...asked, pages: asked.pages + 1, from: older.oldest };
      setAsked(next);
      older
        .onLoad()
        .catch(() =>
          setAsked((now) => (now === next ? { ...now, failed: true } : now)),
        );
    }
  }, [active, asked, count, listRef, older]);

  return {
    asked: Boolean(asked),
    looking: Boolean(active && asked?.pages),
    stop: useCallback(() => setAsked(null), []),
  };
}
