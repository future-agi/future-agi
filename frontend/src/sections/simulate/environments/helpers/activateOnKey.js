// Fire a click-style handler when a role="button" element is activated by
// keyboard (Enter/Space), matching native button semantics.
export const activateOnKey = (handler) => (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    handler?.(e);
  }
};
