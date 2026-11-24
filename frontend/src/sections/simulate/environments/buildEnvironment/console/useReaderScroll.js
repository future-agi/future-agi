import { useCallback, useEffect, useRef, useState } from "react";

const UP_KEYS = new Set(["ArrowUp", "PageUp", "Home"]);
const DOWN_KEYS = new Set(["ArrowDown", "PageDown", "End"]);
const isField = (node) => node.closest?.("input, textarea, [contenteditable]");

/**
 * Tells the reader moving the list from the console moving it. `movedUp` is
 * true once the reader last moved it up, and false again when the console
 * scrolls it. A wheel, touch or key counts even where the list is too short to
 * scroll; a dragged scrollbar counts by the scroll it makes.
 */
export default function useReaderScroll(listRef, onMove) {
  const [movedUp, setMovedUp] = useState(false);
  const ownScroll = useRef(false);
  const touchY = useRef(null);

  useEffect(() => {
    const list = listRef.current;
    const ended = () => {
      ownScroll.current = false;
    };
    list?.addEventListener("scrollend", ended);
    return () => list?.removeEventListener("scrollend", ended);
  }, [listRef]);

  const startOwnScroll = useCallback(() => {
    ownScroll.current = true;
    setMovedUp(false);
  }, []);
  const moved = (up) => {
    ownScroll.current = false;
    if (up) setMovedUp(true);
    onMove();
  };

  return {
    movedUp,
    startOwnScroll,
    // The list scrolled up: by the reader, unless the console is scrolling it.
    scrolledUp: () => {
      if (!ownScroll.current) moved(true);
    },
    listProps: {
      onWheel: ({ deltaY }) => deltaY && moved(deltaY < 0),
      onTouchStart: ({ touches }) => {
        touchY.current = touches[0].clientY;
      },
      onTouchMove: ({ touches }) => {
        const { clientY } = touches[0];
        if (clientY !== touchY.current) moved(clientY > touchY.current);
        touchY.current = clientY;
      },
      onKeyDown: ({ key, target }) => {
        if (isField(target)) return;
        if (UP_KEYS.has(key)) moved(true);
        else if (DOWN_KEYS.has(key)) moved(false);
      },
      // On the list itself rather than in it: its scrollbar.
      onPointerDown: ({ target, currentTarget }) => {
        if (target === currentTarget) ownScroll.current = false;
      },
    },
  };
}
