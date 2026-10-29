export const editorTheme = () =>
  document.body.getAttribute("data-theme") === "dark" ? "vs-dark" : "light";

export const EDITOR_OPTIONS = {
  readOnly: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  fontSize: 12,
  lineNumbersMinChars: 3,
  renderLineHighlight: "none",
  wordWrap: "on",
};
