// Text projection of a Delta for the OS clipboard.
//
// Rules mirror getBlocks' text assembly (common.js): string inserts verbatim,
// an EditVariable embed that replaced a "}" contributes "}", a Jinja
// `fromBlock` EditVariable contributes nothing, and media embeds (image, audio,
// PDF) contribute nothing — card labels are never prompt text.
import { mediaItemFromInsert } from "../Blots/mediaValue";

const opsOf = (delta) => (Array.isArray(delta) ? delta : delta?.ops) || [];

function variableCloseOp(op) {
  const ev = op?.insert?.EditVariable;
  return !!ev && typeof op.insert === "object" && !ev.fromBlock;
}

export function buildTextProjection(delta) {
  let out = "";
  for (const op of opsOf(delta)) {
    if (typeof op.insert === "string") out += op.insert;
    else if (variableCloseOp(op)) out += "}";
  }
  return out;
}

/**
 * Ordered clipboard items mirroring the Delta:
 *   { kind: "text", text } | { kind: "variableClose" } |
 *   { kind: "image"|"audio"|"pdf", url, name, size, mimeType? }
 * Adjacent text runs are merged; unknown embeds and URL-less media are dropped.
 */
export function buildClipboardItems(delta) {
  const items = [];
  const pushText = (text) => {
    const last = items[items.length - 1];
    if (last && last.kind === "text") last.text += text;
    else items.push({ kind: "text", text });
  };
  for (const op of opsOf(delta)) {
    if (typeof op.insert === "string") {
      pushText(op.insert);
    } else if (variableCloseOp(op)) {
      items.push({ kind: "variableClose" });
    } else {
      const media = mediaItemFromInsert(op);
      if (media) items.push(media);
    }
  }
  return items;
}

export function itemsContainMedia(items) {
  return (items || []).some(
    (i) => i && i.kind !== "text" && i.kind !== "variableClose",
  );
}
