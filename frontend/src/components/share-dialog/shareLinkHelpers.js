// Map a transport error to safe, fixed copy. Server-supplied text is never
// rendered: it can carry HTML, stack traces, tokens or URLs.
export function describeLinkError(error, action) {
  const status = error?.response?.status ?? error?.status;
  if (status === 401) {
    return "Your session has expired. Sign in again to share this item.";
  }
  if (status === 403 || status === 404) {
    return "This item can't be shared from here.";
  }
  return action === "create"
    ? "Couldn't create a share link. Check your connection and retry."
    : "Couldn't load the share link. Check your connection and retry.";
}

export function readToken(link) {
  const token = link?.token;
  return typeof token === "string" ? token.trim() : "";
}

// Mirrors the server's validity rule: active and not past expires_at.
export function isLinkActive(link) {
  if (!link || link.is_active === false) return false;
  if (!link.expires_at) return true;
  const expiry = Date.parse(link.expires_at);
  return !Number.isNaN(expiry) && expiry > Date.now();
}

export function readAccessMode(link) {
  return link?.access_type ?? null;
}
