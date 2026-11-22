import Quill from "quill";

// Formula and video blots export their value as text: html() escapes it, so
// getSemanticHTML() and copy/cut output show the value exactly as stored. The
// subclasses are registered on the shared Quill class used by PromptEditor and
// InputPromptBoxV3; their custom blot registrations and the Delta values are
// left unchanged.
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
