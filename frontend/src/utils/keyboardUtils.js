/**
 * Returns true if the keyboard event target is an editable element.
 * Used to guard keyboard shortcuts from firing while the user is typing.
 *
 * Covers: INPUT, TEXTAREA, SELECT, and any element with contentEditable.
 */
export function isEditableElement(e) {
  const tag = e.target?.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    e.target?.isContentEditable
  );
}
