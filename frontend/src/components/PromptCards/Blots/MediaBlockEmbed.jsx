// TH-150: shared base for the image/audio/PDF block embeds.
//
// Behaviour common to all three media blots:
// - create(value) accepts both the flat PromptEditor shape and the nested
//   value() shape (history replay / paste), via normalizeMediaValue.
// - The React card is rendered in attach(), when the blot already belongs to
//   a Quill instance, so delete/magnify/replace callbacks come from the owning
//   editor's registry (embedCallbacks) and never from serialized data.
// - detach() unmounts the React root (previously leaked on every removal).
// - html() returns "" so getSemanticHTML never serializes card markup.
import Quill from "quill";
import { createRoot } from "react-dom/client";
import "../PromptCardEditor.css";
import { getEmbedCallbacks } from "./embedCallbacks";
import { mediaDataAttribute, normalizeMediaValue } from "./mediaValue";
import { getRandomId } from "src/utils/utils";

const BlockEmbed = Quill.import("blots/block/embed");

export const MEDIA_EMBED_CLASS = "prompt-media-embed";

const VALUE_KEY = "__promptMediaValue";

export default class MediaBlockEmbed extends BlockEmbed {
  // Subclasses set: static mediaKind ("image" | "audio" | "pdf"),
  // static dataAttribute ("data-image-data" | ...), and implement
  // static renderCard(normalized, callbacks) -> React element.

  static create(value) {
    const normalized = normalizeMediaValue(this.mediaKind, value, getRandomId);
    const node = super.create();
    node.setAttribute("contenteditable", false);
    node.setAttribute("id", normalized.id);
    node.classList.add(MEDIA_EMBED_CLASS);
    node.setAttribute(
      this.dataAttribute,
      JSON.stringify(mediaDataAttribute(this.mediaKind, normalized)),
    );
    node[VALUE_KEY] = normalized;
    return node;
  }

  static formats() {
    return null;
  }

  attach() {
    super.attach();
    this.renderCard();
  }

  detach() {
    this.unmountCard();
    super.detach();
  }

  /** Unmounts the React card without touching the Quill document. */
  unmountCard() {
    const root = this.reactRoot;
    this.reactRoot = null;
    if (root) {
      // React warns when unmount() runs synchronously inside another commit.
      queueMicrotask(() => root.unmount());
    }
  }

  /** Re-renders the card with the owning editor's current callbacks. */
  renderCard() {
    const normalized = this.domNode[VALUE_KEY];
    if (!normalized) return;
    // The owning editor's registry wins: it carries the read-only flag and
    // the user-sourced removal (undo-able, caret placed). Callbacks that were
    // serialised into the value only fill keys the registry does not define.
    const callbacks = {
      ...normalized.callbacks,
      ...getEmbedCallbacks(this),
    };
    if (!this.reactRoot) {
      this.reactRoot = createRoot(this.domNode);
    }
    this.reactRoot.render(this.statics.renderCard(normalized, callbacks));
  }

  html() {
    return "";
  }
}
