// Row layouts (W9-4, B4): the documents a custom renderer runs in. Pure.
//
// Two frames. The app's iframe (sandbox `allow-scripts`, an opaque origin)
// holds a WRAPPER document of ours with no script of its own; the wrapper
// holds the RENDERER frame (also `allow-scripts` only) with the author's
// HTML. A frame's own CSP can't stop the frame navigating itself (location,
// meta refresh, a link): that is the embedding document's `frame-src`. So
// the wrapper's CSP has `frame-src 'none'`, and any navigation of the
// renderer away from its srcdoc is refused before a request is made. The
// renderer can't navigate the wrapper or the app (sandboxed navigation),
// and it posts to the app's window (`parent.parent`); the app accepts its
// messages only from the renderer's window (SandboxedRenderer.jsx). The
// wrapper's one script of ours reports what its frame-src refused (the
// renderer's own CSP events can't: its page is gone by then).
//
// Renderer document order: the Content-Security-Policy meta first
// (default-src 'none' covers fetch, XHR, WebSocket, images by URL, frames),
// then the bootstrap that defines `window.fa` and reports CSP violations,
// then the author's HTML. A srcdoc document also keeps the wrapper's policy,
// so the wrapper allows what the renderer may use (inline script and style,
// data: images and fonts) and nothing else.
//
// The author's API:
//   fa.row                      {id, number, input, expected, metadata, other, outputs}
//   fa.onRow(cb)                cb(row) now and whenever the row changes
//   fa.proposeChange({column, value})   asks the app for one pending change
//   fa.setHeight(px)            the frame's height (otherwise it follows the content,
//                               up to the layout's max height)

export const RENDERER_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'; base-uri 'none'";

/** The wrapper's policy: the renderer's, and no frame may navigate. */
export const WRAPPER_CSP = `${RENDERER_CSP}; frame-src 'none'; child-src 'none'`;

/** The only sandbox token either frame gets. */
export const RENDERER_SANDBOX = "allow-scripts";

/** Message types the frame may send, and the one it receives. */
export const MSG = Object.freeze({
  ready: "fa:ready",
  csp: "fa:csp",
  propose: "fa:propose",
  height: "fa:height",
  error: "fa:error",
  row: "fa:row",
});

/** JSON that is safe inside a <script> element. */
export function scriptJson(value) {
  return JSON.stringify(value ?? null)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .split(String.fromCharCode(0x2028))
    .join("\\u2028")
    .split(String.fromCharCode(0x2029))
    .join("\\u2029");
}

const escapeAttr = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");

/** The bootstrap script (runs before the author's code). */
export function bootstrapScript(row) {
  return `(function () {
  var app = parent.parent;
  var post = function (m) { try { app.postMessage(m, "*"); } catch (e) {} };
  var cbs = [];
  var manual = false;
  var fa = {
    row: ${scriptJson(row)},
    onRow: function (cb) {
      if (typeof cb !== "function") return;
      cbs.push(cb);
      if (fa.row) { try { cb(fa.row); } catch (err) { post({ type: "${MSG.error}", message: String((err && err.message) || err) }); } }
    },
    proposeChange: function (change) {
      post({ type: "${MSG.propose}", column: String(change && change.column), value: change ? change.value : undefined });
    },
    setHeight: function (px) { manual = true; post({ type: "${MSG.height}", px: Number(px) }); }
  };
  window.fa = fa;
  document.addEventListener("securitypolicyviolation", function (e) {
    post({ type: "${MSG.csp}", directive: String(e.effectiveDirective || e.violatedDirective || ""), blocked: String(e.blockedURI || "") });
  });
  window.addEventListener("error", function (e) {
    post({ type: "${MSG.error}", message: String(e.message || "error") });
  });
  window.addEventListener("message", function (e) {
    if (e.source !== app) return;
    var d = e.data;
    if (!d || d.type !== "${MSG.row}") return;
    fa.row = d.row;
    cbs.forEach(function (cb) { try { cb(d.row); } catch (err) { post({ type: "${MSG.error}", message: String((err && err.message) || err) }); } });
  });
  var size = function () { if (!manual) post({ type: "${MSG.height}", px: document.body.scrollHeight, auto: true }); };
  window.addEventListener("load", function () {
    size();
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(size).observe(document.body);
  });
  post({ type: "${MSG.ready}" });
})();`;
}

/** The renderer frame's `srcdoc`: CSP meta, then the bootstrap, then the author's HTML. */
export function buildSrcdoc(html, row) {
  return [
    "<!doctype html><html><head>",
    `<meta http-equiv="Content-Security-Policy" content="${escapeAttr(RENDERER_CSP)}">`,
    '<meta charset="utf-8">',
    `<script>${bootstrapScript(row)}</script>`,
    "<style>html,body{margin:0;font:14px/1.45 system-ui,sans-serif;}</style>",
    "</head><body>",
    String(html ?? ""),
    "</body></html>",
  ].join("");
}

/** The wrapper's only script: report what its policy refused to the app. */
export const WRAPPER_SCRIPT = `document.addEventListener("securitypolicyviolation", function (e) {
  try { parent.postMessage({ type: "${MSG.csp}", directive: String(e.effectiveDirective || e.violatedDirective || ""), blocked: String(e.blockedURI || "") }, "*"); } catch (err) {}
});`;

/**
 * The app frame's `srcdoc`: the wrapper (its CSP and its one reporting
 * script, no author code) around the renderer frame. `title` names the
 * renderer frame for assistive tech.
 */
export function buildFrameDoc(html, row, title = "Custom renderer") {
  return [
    "<!doctype html><html><head>",
    `<meta http-equiv="Content-Security-Policy" content="${escapeAttr(WRAPPER_CSP)}">`,
    '<meta charset="utf-8">',
    `<script>${WRAPPER_SCRIPT}</script>`,
    "<style>html,body{margin:0;height:100%;overflow:hidden;}iframe{display:block;border:0;width:100%;height:100%;}</style>",
    "</head><body>",
    `<iframe sandbox="${RENDERER_SANDBOX}" referrerpolicy="no-referrer" title="${escapeAttr(title)}" srcdoc="${escapeAttr(buildSrcdoc(html, row))}"></iframe>`,
    "</body></html>",
  ].join("");
}

/** A first renderer for the editor: reads the row and proposes one change. */
export const STARTER_RENDERER = `<div id="out"></div>
<script>
  fa.onRow(function (row) {
    var out = document.getElementById("out");
    out.innerHTML = "";
    Object.keys(row.input).forEach(function (name) {
      var p = document.createElement("p");
      p.textContent = name + ": " + row.input[name];
      out.appendChild(p);
    });
  });
</script>`;
