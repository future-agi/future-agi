import { parseReport, toPrintHtml } from "./falconReport";

const GLYPH = "/favicon/logo.svg";
const WORDMARK = "/logo/future_agi_text.svg";

async function svg(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`brand asset ${path} ${res.status}`);
  return res.text();
}

const toDataUri = (raw) =>
  `data:image/svg+xml;base64,${window.btoa(unescape(encodeURIComponent(raw)))}`;

function frameFor(markup) {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
  document.body.appendChild(frame);
  return new Promise((resolve) => {
    frame.addEventListener("load", () => resolve(frame), { once: true });
    frame.srcdoc = markup;
  });
}

/**
 * Print one answer as the report pack.
 * The brand marks are inlined so the frame has nothing left to fetch when print fires.
 */
export default async function downloadReportPdf(content, { badge } = {}) {
  const doc = parseReport(content);
  if (!doc.pages.length) return false;

  const [glyphSvg, wordmark] = await Promise.all([svg(GLYPH), svg(WORDMARK)]);
  const markup = toPrintHtml(doc, {
    glyphSvg: glyphSvg.trim(),
    wordmarkDataUri: toDataUri(wordmark),
    ...(badge === undefined ? null : { badge }),
  });

  const frame = await frameFor(markup);
  const win = frame.contentWindow;
  if (frame.contentDocument?.fonts?.ready) await frame.contentDocument.fonts.ready;

  const drop = () => frame.remove();
  win.addEventListener("afterprint", drop, { once: true });
  setTimeout(drop, 60000);

  win.focus();
  win.print();
  return true;
}
