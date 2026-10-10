import Quill from "quill";
import { MEDIA_BLOT_NAMES } from "../Blots/mediaValue";

/** Whole focused message, excluding Quill's structural terminal newline. */
export function selectAllRange(quill) {
  return { index: 0, length: Math.max(0, quill.getLength() - 1) };
}

/**
 * Media embeds whose index lies inside `range` (every embed is 1 index long).
 */
export function embedIndicesInRange(delta, range) {
  const ops = (Array.isArray(delta) ? delta : delta?.ops) || [];
  const start = range.index;
  const end = range.index + range.length;
  const found = [];
  let index = 0;
  for (const op of ops) {
    if (typeof op.insert === "string") {
      index += op.insert.length;
      continue;
    }
    const blotName = MEDIA_BLOT_NAMES.find(
      (n) => op.insert && Object.prototype.hasOwnProperty.call(op.insert, n),
    );
    if (blotName && index >= start && index < end) {
      found.push({ index, blotName, id: op.insert[blotName]?.id });
    }
    index += 1;
  }
  return found;
}

/** Quill index of the embed that owns `node`, or -1. */
export function embedIndexForNode(quill, node) {
  const blot = Quill.find(node, true);
  if (!blot || !blot.scroll || blot.scroll !== quill.scroll) return -1;
  return blot.offset(quill.scroll);
}

/** The media blot at `index`, or null when the leaf there is text/other. */
export function leafIsMediaEmbed(quill, index) {
  if (index < 0 || index >= quill.getLength()) return null;
  const [leaf] = quill.getLeaf(index);
  if (!leaf) return null;
  return MEDIA_BLOT_NAMES.includes(leaf.statics?.blotName) ? leaf : null;
}

export const EMBED_NODE_SELECTOR =
  "[data-image-data],[data-audio-data],[data-pdf-data]";

// Interactive children of a card keep their own behaviour on mousedown.
export const EMBED_CONTROL_SELECTOR =
  "button, a, input, audio, video, [role=button], [data-embed-control]";
