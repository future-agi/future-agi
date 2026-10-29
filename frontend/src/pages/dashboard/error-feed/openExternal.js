// Open a backend-supplied URL (e.g. a Linear issue link) in a new tab.
// Scheme-validated so a poisoned value can never run javascript:, and
// noopener/noreferrer so the target can't reach back into the app window.
export default function openExternal(url) {
  const safe = safeExternalUrl(url);
  if (safe) window.open(safe, "_blank", "noopener,noreferrer");
}

// The URL as an http(s) string, or null for anything else (javascript:,
// data:, malformed) so it can be rendered as a link.
export function safeExternalUrl(url) {
  if (!url) return null;
  try {
    const u = new URL(url, window.location.origin);
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
    return u.toString();
  } catch {
    return null;
  }
}
