// Open a backend-supplied URL (e.g. a Linear issue link) in a new tab.
// Scheme-validated so a poisoned value can never run javascript:, and
// noopener/noreferrer so the target can't reach back into the app window.
export default function openExternal(url) {
  if (!url) return;
  try {
    const u = new URL(url, window.location.origin);
    if (u.protocol !== "https:" && u.protocol !== "http:") return;
    window.open(u.toString(), "_blank", "noopener,noreferrer");
  } catch {
    /* malformed URL — nothing to open */
  }
}
