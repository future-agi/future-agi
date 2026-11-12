#!/usr/bin/env node
/* eslint-env node */
/* eslint-disable no-console -- a test tool reports to the terminal */
// Fake framed children for the embed-host e2e, on two loopback origins.
//   CHILD (127.0.0.1:4191)  a well-behaved omega-embed/1 child, and under
//                           /studio-like (alias /studio-forgets) a child
//                           whose memory-only key is lost on reload/remount
//   THIRD (127.0.0.1:4192)  hostile pages that try to talk to the parent
// The child keeps the key in memory only and exposes its SHA-256 so the test
// can prove the key it received is the one from the opened link.
const http = require("node:http");

const { CONTROL, PARENT, PORTS } = require("./ports.cjs");
const { studioLikeBody } = require("./studio-like-child.cjs");

const CHILD_PORT = PORTS.child;
const THIRD_PORT = PORTS.third;
const ALLOWED_PARENTS = (
  process.env.EMBED_ALLOWED_PARENTS || `${PARENT},${CONTROL}`
).split(",");

const page = (title, body) =>
  `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>
<style>
  :root { color-scheme: light; font-family: Inter, system-ui, sans-serif; }
  :root[data-theme="dark"] { color-scheme: dark; background: #141414; color: #f2f2f2; }
  body { margin: 24px; } button { margin: 4px 8px 4px 0; }
</style></head><body>${body}</body></html>`;

// The child half of omega-embed/1, written the way Studio's embed.ts is.
const childScript = (parent, { gateAfterKey = "ready" } = {}) => `
<script>
(function () {
  var PARENT = ${JSON.stringify(parent)};
  var state = { keyHash: null, theme: null, route: null, received: [], rejected: [], navigations: [] };
  window.__embed = state;
  var applying = false;
  function route() { return (location.hash || "#/").slice(1) || "/"; }
  function post(message) { window.parent.postMessage(message, PARENT); }
  function render() {
    state.route = route();
    document.getElementById("route").textContent = state.route;
    document.getElementById("theme").textContent = state.theme || "unset";
    document.getElementById("key").textContent = state.keyHash ? "received" : "waiting";
  }
  async function sha256(text) {
    var bytes = new TextEncoder().encode(text);
    var digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest)).map(function (b) {
      return b.toString(16).padStart(2, "0");
    }).join("");
  }
  window.addEventListener("message", async function (event) {
    if (event.source !== window.parent || event.origin !== PARENT) {
      state.rejected.push(event.origin);
      return;
    }
    var message = event.data || {};
    state.received.push(message.type);
    if (message.type === "embed.key") {
      state.keyHash = await sha256(message.token);
      render();
      post({ type: "embed.gate", state: ${JSON.stringify(gateAfterKey)} });
      post({ type: "embed.route", path: route() });
    } else if (message.type === "embed.theme") {
      state.theme = message.mode;
      document.documentElement.dataset.theme = message.mode;
      render();
    } else if (message.type === "embed.navigate") {
      state.navigations.push(message.path);
      applying = true;
      history.replaceState(null, "", "#" + message.path);
      applying = false;
      render();
    }
  });
  window.addEventListener("hashchange", function () {
    render();
    if (!applying) post({ type: "embed.route", path: route() });
  });
  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-go],[data-open]");
    if (!target) return;
    if (target.dataset.go) location.hash = target.dataset.go;
    if (target.dataset.open) post({ type: "embed.open", href: target.dataset.open });
  });
  render();
  post({ type: "embed.hello", protocol: "omega-embed/1" });
})();
</script>`;

const childBody = (parent, options) => `
<h3>Fake Omega child</h3>
<p>Route: <code id="route"></code> &middot; Theme: <code id="theme"></code> &middot; Key: <code id="key"></code></p>
<button data-go="/runs/2">Go to run 2</button>
<button data-open="/dashboard/observe/p1?from=studio">Open trace in Future AGI</button>
<button data-open="https://evil.example/phish">Open evil link</button>
${childScript(parent, options)}`;

// A page on the wrong origin that does everything a child would.
const hostileBody = `
<p>Hostile page</p>
<script>
  window.__received = [];
  window.addEventListener("message", function (event) {
    window.__received.push({ origin: event.origin, data: event.data });
  });
  function shout() {
    var target = window.parent;
    target.postMessage({ type: "embed.hello", protocol: "omega-embed/1" }, "*");
    target.postMessage({ type: "embed.route", path: "/pwned" }, "*");
    target.postMessage({ type: "embed.open", href: "/dashboard/pwned" }, "*");
    target.postMessage({ type: "embed.gate", state: "ready" }, "*");
  }
  shout();
  setTimeout(shout, 300);
  setTimeout(shout, 900);
</script>`;

const send = (res, status, body, headers = {}) => {
  res.writeHead(status, { "Cache-Control": "no-store", ...headers });
  res.end(body);
};

const html = (res, body, headers = {}) =>
  send(res, 200, body, {
    "Content-Type": "text/html; charset=utf-8",
    ...headers,
  });

function childHandler(req, res) {
  const url = new URL(req.url, `http://127.0.0.1:${CHILD_PORT}`);
  const requested = url.searchParams.get("parent") || "";
  const parent = ALLOWED_PARENTS.includes(requested)
    ? requested
    : ALLOWED_PARENTS[0];
  const ancestors = {
    "Content-Security-Policy": `frame-ancestors ${ALLOWED_PARENTS.join(" ")}`,
  };
  switch (url.pathname) {
    case "/probe.json":
      return send(res, 200, "{}", { "Content-Type": "application/json" });
    case "/child.html":
      return html(res, page("Fake child", childBody(parent)), ancestors);
    case "/refused/child.html":
      return html(
        res,
        page("Fake child", childBody(parent, { gateAfterKey: "refused" })),
        ancestors,
      );
    case "/needs-key/child.html":
      return html(
        res,
        page("Fake child", childBody(parent, { gateAfterKey: "needs-key" })),
        ancestors,
      );
    case "/studio-like/child.html":
      return html(
        res,
        page("Studio-like child", studioLikeBody(parent)),
        ancestors,
      );
    case "/studio-forgets/child.html":
      return html(
        res,
        page("Studio-like child", studioLikeBody(parent)),
        ancestors,
      );
    case "/silent/child.html":
      // Loads fine but never says hello (an old child, or one still starting).
      return html(res, page("Silent child", "<p>Starting…</p>"), ancestors);
    case "/framing-blocked/child.html":
      // Like today's omega dev: nobody may frame it.
      return html(res, page("Fake child", childBody(parent)), {
        "Content-Security-Policy": "frame-ancestors 'none'",
        "X-Frame-Options": "DENY",
      });
    case "/evil-redirect/child.html":
      // Same frame window, but the document that answers is on another origin.
      return send(res, 302, "", {
        Location: `http://127.0.0.1:${THIRD_PORT}/evil.html`,
      });
    default:
      return send(res, 404, "not found");
  }
}

function thirdHandler(req, res) {
  const url = new URL(req.url, `http://127.0.0.1:${THIRD_PORT}`);
  if (url.pathname === "/evil.html" || url.pathname === "/third.html") {
    return html(res, page("Hostile", hostileBody));
  }
  return send(res, 404, "not found");
}

const servers = [
  http.createServer(childHandler).listen(CHILD_PORT, "127.0.0.1"),
  http.createServer(thirdHandler).listen(THIRD_PORT, "127.0.0.1"),
];
console.log(
  `embed children on 127.0.0.1:${CHILD_PORT} and 127.0.0.1:${THIRD_PORT}`,
);

const stop = () => {
  servers.forEach((server) => server.close());
  process.exit(0);
};
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
