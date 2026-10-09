import { MEDIA_EMBED_CLASS } from "./Blots/MediaBlockEmbed";
import {
  EMBED_CONTROL_SELECTOR,
  EMBED_NODE_SELECTOR,
  embedIndexForNode,
  embedIndicesInRange,
  leafIsMediaEmbed,
  selectAllRange,
} from "./clipboard/selectionHelpers";

// A user-sourced delete so it is one undo step; the stored file is untouched.
function removeEmbedAt(quill, index) {
  quill.deleteText(index, 1, "user");
  quill.setSelection(index, 0, "silent");
}

export function removeMediaEmbedById(quill, blotName, id) {
  if (!quill || !quill.isEnabled()) return;
  let index = 0;
  for (const op of quill.getContents().ops) {
    const value = op.insert?.[blotName];
    if (value && value.id === id) {
      removeEmbedAt(quill, index);
      return;
    }
    index += typeof op.insert === "string" ? op.insert.length : 1;
  }
}

// Quill calls binding handlers with the keyboard module as `this`, so they
// must stay plain functions. They run before Quill's defaults for the same
// key; returning true falls through to them.
export function createKeyboardBindings() {
  return {
    selectAll: {
      key: "a",
      shortKey: true,
      handler() {
        const range = selectAllRange(this.quill);
        this.quill.setSelection(range.index, range.length, "user");
        return false;
      },
    },
    deleteEmbedBackward: {
      key: "Backspace",
      collapsed: true,
      handler(range) {
        if (!this.quill.isEnabled()) return false;
        if (range.index === 0) return true;
        let target = -1;
        if (leafIsMediaEmbed(this.quill, range.index - 1)) {
          target = range.index - 1;
        } else if (leafIsMediaEmbed(this.quill, range.index)) {
          // Caret on a card's own line: Quill would delete the previous
          // line's newline, which cannot merge into a card and is a no-op.
          // An empty previous line is still removed the way Quill does it.
          const [prev] = this.quill.getLine(range.index - 1);
          const prevIsEmptyLine =
            prev?.statics.blotName === "block" && prev.length() <= 1;
          if (!prevIsEmptyLine) target = range.index;
        }
        if (target < 0) return true;
        removeEmbedAt(this.quill, target);
        return false;
      },
    },
    deleteEmbedForward: {
      key: "Delete",
      collapsed: true,
      handler(range) {
        if (!this.quill.isEnabled()) return false;
        let target = -1;
        if (leafIsMediaEmbed(this.quill, range.index)) {
          target = range.index;
        } else {
          // Caret at the end of the text line just before a card: the next
          // character is that line's newline, so Quill's own handler would
          // try to merge the line into the card. Remove the card instead.
          const [line, offset] = this.quill.getLine(range.index);
          if (
            line &&
            offset === line.length() - 1 &&
            leafIsMediaEmbed(this.quill, range.index + 1)
          ) {
            target = range.index + 1;
          }
        }
        if (target < 0) return true;
        this.quill.deleteText(target, 1, "user");
        this.quill.setSelection(range.index, 0, "silent");
        return false;
      },
    },
  };
}

// Clicking a card body (not its buttons) selects it as one block and keeps
// focus in the editor, so a following Cmd/Ctrl+A, Backspace or Cmd/Ctrl+C acts
// on this editor rather than the page. Returns the detach function.
export function attachCardSelection(quill) {
  const onMouseDown = (event) => {
    const target = event.target;
    if (!target || typeof target.closest !== "function") return;
    const embedNode = target.closest(EMBED_NODE_SELECTOR);
    if (!embedNode || !quill.root.contains(embedNode)) return;
    if (target.closest(EMBED_CONTROL_SELECTOR)) return;
    const index = embedIndexForNode(quill, embedNode);
    if (index < 0) return;
    event.preventDefault();
    quill.focus();
    quill.setSelection(index, 1, "user");
  };
  quill.root.addEventListener("mousedown", onMouseDown);
  return () => quill.root.removeEventListener("mousedown", onMouseDown);
}

// A card's text is not selectable, so a card inside the selection is marked
// with a class instead.
export function syncSelectedEmbeds(quill, range) {
  quill.root
    .querySelectorAll(`.${MEDIA_EMBED_CLASS}.is-selected`)
    .forEach((node) => node.classList.remove("is-selected"));
  if (!range || range.length === 0) return;
  embedIndicesInRange(quill.getContents(), range).forEach(({ index }) => {
    const [leaf] = quill.getLeaf(index);
    leaf?.domNode?.classList?.add("is-selected");
  });
}
