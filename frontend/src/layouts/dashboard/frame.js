// The phone top bar (below md) is 56px and sits above the frame.
export const PHONE_BAR_HEIGHT = 56;

/**
 * The dashboard frame: the sidebar and the content panel side by side. <Main>
 * is the only scroll root; the frame itself never scrolls.
 * - overflow-x is `clip`, not `auto`: a clipped box is not a scroll container,
 *   so scrollIntoView/focus() calls from a page cannot slide the whole shell
 *   (sidebar and all) sideways.
 * - Below md the frame fills the viewport under the phone bar, so the bar
 *   plus the frame never exceed it and the document has nothing to scroll.
 * - `contain` (Skills routes) also makes the frame the containing block for
 *   absolutely positioned content that has no positioned ancestor (for
 *   example a visually hidden header), and clips it, so such content can
 *   never widen or lengthen the document.
 */
export function dashboardFrameSx({ horizontal = false, mini = false, phone = false, contain = false } = {}) {
  if (horizontal) return { display: "block" };
  return {
    minHeight: 1,
    minWidth: 0,
    width: 1,
    display: "flex",
    flexDirection: "row",
    // The frame: sidebar and page edges on the canvas; <Main> is the
    // inset content panel.
    backgroundColor: "background.default",
    ...(mini ? {} : { overflowX: "clip" }),
    ...(phone
      ? { height: `calc(100vh - ${PHONE_BAR_HEIGHT}px)`, minHeight: 0, overflowX: "clip", overflowY: "clip" }
      : {}),
    ...(contain
      ? { position: "relative", overflowX: "clip", overflowY: "clip", ...(phone ? {} : { height: "100vh", minHeight: 0 }) }
      : {}),
  };
}
