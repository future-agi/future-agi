import Quill from "quill";

// Quill 2.0.3's formula and video blots build their html() output by string
// interpolation, so quotes, angle brackets and entities in the stored value
// end up as markup in getSemanticHTML() and in Quill's copy/cut clipboard
// HTML. Register escaping subclasses on the shared Quill class used by
// PromptEditor and InputPromptBoxV3; their custom blot registrations and the
// Delta values are left unchanged.
const Formula = Quill.import("formats/formula");
const Video = Quill.import("formats/video");
const HTML_ENTITIES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (character) => HTML_ENTITIES[character],
  );

class EscapedFormula extends Formula {
  html() {
    return `<span>${escapeHtml(Formula.value(this.domNode))}</span>`;
  }
}

class EscapedVideo extends Video {
  html() {
    const video = escapeHtml(Video.value(this.domNode));
    return `<a href="${video}">${video}</a>`;
  }
}

Quill.register(
  { "formats/formula": EscapedFormula, "formats/video": EscapedVideo },
  true,
);

export default Quill;
