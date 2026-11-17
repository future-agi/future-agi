import { useEffect } from "react";
// Focus guards live in utils/keyboard-guards.js (one keymap, PRODUCT-SPEC §4.1).
import { isSafeForSingleKey } from "src/utils/keyboard-guards";

/**
 * iterate r3 G3 merge: ↓ / ↑ are next / previous on every keymap surface,
 * review included (the `?` sheet shows J / ↓), so they move items here too;
 * they stay with a widget that handled them already (a tree) or uses them
 * itself (a radio group, listbox, menu, tree or grid).
 */
const ownsVerticalArrows = (e, focused) =>
  e.defaultPrevented ||
  (focused?.tagName === "INPUT" && focused.type === "radio") ||
  !!focused?.closest?.(
    '[role="radiogroup"], [role="tree"], [role="treegrid"], [role="grid"], [role="listbox"], [role="menu"]',
  );

/**
 * Review workspace keys (§4.1): ⌘↵ / ⌘S submit, S skip, ←/→, ↑/↓ and K/J
 * previous / next, Esc exit, ? the shortcut sheet. Single keys only fire
 * from safe contexts (never while typing).
 */
export default function useKeyboardShortcuts({
  onSubmit,
  onSkip,
  onPrev,
  onNext,
  onEscape,
  onHelp,
  canPrev = true,
  canNext = true,
}) {
  useEffect(() => {
    const handler = (e) => {
      const focused = document.activeElement;

      // Ctrl/Cmd+Enter or Ctrl/Cmd+S -> Submit
      if ((e.ctrlKey || e.metaKey) && (e.key === "Enter" || e.key === "s")) {
        e.preventDefault();
        onSubmit?.();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      // Remaining single-key shortcuts only fire from safe contexts
      if (!isSafeForSingleKey(focused)) return;

      switch (e.key) {
        case "s":
        case "S":
          e.preventDefault();
          onSkip?.();
          break;
        case "ArrowLeft":
        case "k":
        case "K":
          // Suppress native arrow scroll even at the boundary; only gate the nav.
          e.preventDefault();
          if (canPrev) onPrev?.();
          break;
        case "ArrowRight":
        case "j":
        case "J":
          e.preventDefault();
          if (canNext) onNext?.();
          break;
        case "ArrowUp":
        case "ArrowDown":
          if (ownsVerticalArrows(e, focused)) break;
          // Like ← / →: no native scroll, even at the boundary.
          e.preventDefault();
          if (e.key === "ArrowUp") {
            if (canPrev) onPrev?.();
          } else if (canNext) onNext?.();
          break;
        case "?":
          if (!onHelp) break;
          e.preventDefault();
          onHelp();
          break;
        case "Escape":
          if (!onEscape) break;
          e.preventDefault();
          onEscape();
          break;
        default:
          break;
      }
    };

    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onSubmit, onSkip, onPrev, onNext, onEscape, onHelp, canPrev, canNext]);
}
