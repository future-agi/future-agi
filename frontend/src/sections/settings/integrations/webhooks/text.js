// Text helpers of the Webhooks section: pretty JSON, "Copy as curl", inert
// receiver text, and the .env download of a new signing secret.
import { secretEnvLine } from "./copy";

export function prettyJson(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

const SIG = /^webhook-signature$/i;

// One shell word in single quotes. Every value is quoted this way, the URL and
// the header names included: a URL path may hold any printable character (a
// "'" too), and the person pastes this into a shell.
export const shellQuote = (value) =>
  `'${String(value).replace(/'/g, "'\\''")}'`;

// "Copy as curl", with the signature replaced by <signature> (section 11.2).
export function curlOf(url, headers, body) {
  const lines = [`curl -X POST ${shellQuote(url || "<your endpoint URL>")}`];
  for (const [k, v] of Object.entries(headers || {})) {
    lines.push(
      `  -H ${shellQuote(`${k}: ${SIG.test(k) ? "<signature>" : String(v)}`)}`,
    );
  }
  if (body != null) lines.push(`  --data-raw ${shellQuote(body)}`);
  return lines.join(" \\\n");
}

// Bidi overrides and other control characters (an RTLO echo, WT-15c) are shown as
// visible code points, so a receiver's answer cannot reorder what is on screen.
/* eslint-disable no-control-regex */
const CONTROL =
  /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;
/* eslint-enable no-control-regex */
export const visibleControls = (text) =>
  String(text).replace(
    CONTROL,
    (c) =>
      `<U+${c.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}>`,
  );

// The object URL lives this long after the click: Safari starts the download
// after the click returns, and a URL revoked at once cancels it (WH-3 review S9).
export const ENV_URL_LIFETIME_MS = 60_000;

export function downloadEnvLine(secret) {
  const blob = new Blob([secretEnvLine(secret)], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "futureagi-webhook.env";
  a.style.display = "none";
  // In the document while it is clicked: some browsers ignore a click on a
  // detached anchor.
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), ENV_URL_LIFETIME_MS);
}
