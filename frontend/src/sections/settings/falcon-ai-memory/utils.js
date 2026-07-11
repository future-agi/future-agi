// ---------------------------------------------------------------------------
// Source attribution badge (Phase 5A — agent writes must be attributable)
// ---------------------------------------------------------------------------
const SOURCE_BADGES = {
  user: { label: "You", color: "info" },
  agent: { label: "Falcon", color: "warning" },
  init: { label: "Init", color: "default" },
};

export function getSourceBadge(source) {
  return (
    SOURCE_BADGES[source] || { label: source || "unknown", color: "default" }
  );
}
