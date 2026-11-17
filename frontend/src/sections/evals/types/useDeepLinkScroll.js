// W7 A1/A2: bring a deep-linked report into view (`?view=jury|paths`, the
// results drawer's links) and keep it there while the page above it is still
// laying out (the editor's children load after the report's own data). The
// person's first wheel, touch, key or click ends it, and so does a short
// settle time (a display choice).
import { useEffect } from "react";

const SETTLE_MS = 4000;
const INPUTS = ["wheel", "touchstart", "keydown", "mousedown"];

export default function useDeepLinkScroll(ref, active) {
  useEffect(() => {
    const el = ref.current;
    if (!active || !el) return undefined;
    const bring = () => el.scrollIntoView({ block: "start" });
    bring();
    const mo =
      typeof MutationObserver === "undefined"
        ? null
        : new MutationObserver(bring);
    mo?.observe(document.body, { childList: true, subtree: true });
    let timer = null;
    const stop = () => {
      mo?.disconnect();
      clearTimeout(timer);
      INPUTS.forEach((e) => window.removeEventListener(e, stop, true));
    };
    INPUTS.forEach((e) => window.addEventListener(e, stop, true));
    timer = setTimeout(stop, SETTLE_MS);
    return stop;
  }, [ref, active]);
}
