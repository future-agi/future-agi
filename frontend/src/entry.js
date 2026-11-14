// A mixed initial code URL must never fall through to the legacy consumers
// when the browser cannot replace it. A successful fallback navigation loads
// a clean document. Existing stored credentials are deliberately untouched.
if (!window.__authHandoffScrubFailed) {
  import("./main.jsx");
}
