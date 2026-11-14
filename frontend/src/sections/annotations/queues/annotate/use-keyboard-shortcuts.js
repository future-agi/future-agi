import { useEffect } from "react";

const isTextInput = (el) => {
  const tag = el?.tagName?.toLowerCase();
  return tag === "input" || tag === "textarea" || el?.isContentEditable;
};

/** Returns true when the focused element is safe for single-key shortcuts
 *  (body only — avoids accidental triggers on MUI Sliders, Ratings, etc.). */
const isSafeForSingleKey = (el) => {
  if (!el || el === document.body) return true;
  // Block all interactive or focusable elements
  const tag = el.tagName?.toLowerCase();
  if (tag === "button" || tag === "a" || tag === "select") return false;
  if (isTextInput(el)) return false;
  // Block elements with role or tabindex (MUI Slider, Rating, etc.)
  if (el.getAttribute("role") || el.hasAttribute("tabindex")) return false;
  return true;
};

export default function useKeyboardShortcuts({
  onSubmit,
  onSkip,
  onPrev,
  onNext,
  onEscape,
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

      // Remaining single-key shortcuts only fire from safe contexts
      if (!isSafeForSingleKey(focused)) return;

      switch (e.key) {
        case "s":
        case "S":
          e.preventDefault();
          onSkip?.();
          break;
        case "ArrowLeft":
          // Suppress native arrow scroll even at the boundary; only gate the nav.
          e.preventDefault();
          if (canPrev) onPrev?.();
          break;
        case "ArrowRight":
          e.preventDefault();
          if (canNext) onNext?.();
          break;
        case "Escape":
          e.preventDefault();
          onEscape?.();
          break;
        default:
          break;
      }
    };

    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onSubmit, onSkip, onPrev, onNext, onEscape, canPrev, canNext]);
}
