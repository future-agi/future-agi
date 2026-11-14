import { logger } from "./logger";

export async function copyToClipboard(text: unknown) {
  // Serialize objects/arrays to JSON to avoid "[object Object]" from .toString();
  // pass primitives through unchanged.
  const value =
    typeof text === "object" && text !== null
      ? JSON.stringify(text, null, 2)
      : text;
  try {
    // navigator.clipboard is undefined on insecure (http) origins; guard with
    // optional chaining before touching .writeText so it never throws
    // "Cannot read properties of undefined (reading 'writeText')".
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(value as string);
      return true;
    }
    // Fallback for contexts without the async Clipboard API.
    const textarea = document.createElement("textarea");
    textarea.value = value == null ? "" : String(value);
    textarea.style.position = "fixed";
    textarea.style.top = "-9999px";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } finally {
      document.body.removeChild(textarea);
    }
    return ok;
  } catch (err) {
    // Expected on non-secure contexts / when clipboard is blocked.
    // Log as a warning (breadcrumb) rather than an error (Sentry issue).
    logger.warn("Failed to copy text: ", err);
    return false;
  }
}
