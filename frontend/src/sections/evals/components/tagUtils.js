// A tag typed into the box but never committed with Enter is still what the user
// meant, so both save paths fold it in rather than dropping it.
export const withPendingTag = (tags, pendingInput) => {
  const pending = (pendingInput || "").trim();
  return pending && !tags.includes(pending) ? [...tags, pending] : tags;
};
