// Browser-side boundary for provider authorization handoffs. Only server-issued
// public HTTPS URLs are eligible for a new tab; credentials and local targets
// are never opened from this console.
export function safeAuthorizeUrl(value) {
  if (typeof value !== "string" || /[\s\\]/u.test(value)) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/u, "");
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      (url.port && url.port !== "443") ||
      !host.includes(".") ||
      host.includes(":") ||
      /^\d+(\.\d+){3}$/u.test(host) ||
      /(^|\.)(localhost|local|internal|test)$/u.test(host)
    )
      return null;
    return url.href;
  } catch {
    return null;
  }
}
