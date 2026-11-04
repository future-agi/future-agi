/*
  Two page-wide asks, from anywhere on the environment page: open Review
  changes (a file's proposal was just added to the unsaved changes), and show
  the builder chat message an edit came from.
*/
const OPEN = "fagi:open-review-changes";
const FOCUS = "fagi:focus-chat";

export const openReviewChanges = () =>
  window.dispatchEvent(new CustomEvent(OPEN));
export const onOpenReviewChanges = (fn) => {
  window.addEventListener(OPEN, fn);
  return () => window.removeEventListener(OPEN, fn);
};

/** Show the chat message closest before `at` (an ISO time). */
export const focusChatAt = (at) =>
  window.dispatchEvent(new CustomEvent(FOCUS, { detail: { at } }));
export const onFocusChat = (fn) => {
  const h = (e) => fn(e.detail?.at);
  window.addEventListener(FOCUS, h);
  return () => window.removeEventListener(FOCUS, h);
};
