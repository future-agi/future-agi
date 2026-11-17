import React, { Suspense, lazy, useEffect } from "react";
import { useLocation } from "react-router-dom";
import ShortcutSheet from "src/sections/common/RowPanel/ShortcutSheet";
import { isTextInput, isSafeForSingleKey } from "src/utils/keyboard-guards";
import { useShortcutStore } from "./useShortcuts";
import { focusPageSearch } from "./pageSearch";

// IT-R5 G3 (P4): the Test-a-fix panel on the side by side page (loaded on
// first use; renders nothing elsewhere).
const IterateDockHost = lazy(
  () => import("src/sections/experiment-detail/iterate/IterateDockHost"),
);

/**
 * GlobalKeyboard — mounted once in the dashboard layout. Owns the global
 * `?` sheet and the `/` fallback (a surface that binds `search` wins: its
 * handler runs first on `document` and prevents default; this listener is
 * on `window`, so it always runs last).
 */
export default function GlobalKeyboard() {
  const sheet = useShortcutStore((s) => s.sheet);
  const closeSheet = useShortcutStore((s) => s.closeSheet);
  const { pathname } = useLocation();

  useEffect(() => {
    const onKey = (event) => {
      if (event.defaultPrevented || event.isComposing) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const st = useShortcutStore.getState();
      if (st.paletteOpen || st.sheet.open) return;
      const focused = document.activeElement;
      if (isTextInput(focused) || !isSafeForSingleKey(focused)) return;
      if (event.key === "?") {
        event.preventDefault();
        st.openSheet();
      } else if (event.key === "/") {
        if (focusPageSearch()) event.preventDefault();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <ShortcutSheet
        open={sheet.open}
        onClose={closeSheet}
        surface={sheet.surface ?? "global"}
        tab={sheet.tab}
        query={sheet.query}
      />
      {/\/compare\/tasks\/?$/.test(pathname) ? (
        <Suspense fallback={null}>
          <IterateDockHost />
        </Suspense>
      ) : null}
    </>
  );
}
