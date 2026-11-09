/* eslint-env node */
// A stand-in for the EE Console in embed mode, speaking omega-embed/1 the way
// apps/console/src/shared/embed/childBridge.ts does:
// - it trusts only its own origin as the parent (the host serves it on the
//   same site, under /omega/console/);
// - it repeats embed.hello every 500 ms (20 times at most) until a key comes,
//   asking for `want` keys when the host's test sets one (CP P7);
// - it reads `left` on each key, and after the last key of the opening answer
//   reports embed.gate "ready"; refills report the route, not ready again;
// - a closed embed.probe from the exact parent gets a closed embed.proof with
//   this doc and only the last actual gate; probing never stops hello;
// - its router is the hash, with /chat as the default page;
// - its "Future AGI" product entry posts embed.navigate {path: "/dashboard"}.
// - it names its document (RL 1275A): one doc per page load, base64url of 16
//   bytes from crypto.getRandomValues, on every hello and gate; it uses only
//   a key that echoes that doc and counts any other in `foreignKeys`.
// It keeps the key in memory and exposes only its SHA-256, so a test can
// prove the key it holds is the assertion the host minted.

const NAV = [
  ["/chat", "Chat"],
  ["/inbox", "Inbox"],
  ["/explore", "Explore"],
  ["/agents", "Workspace Agents"],
  ["/templates", "Templates"],
  ["/integrations", "Integrations"],
  ["/skills", "Skills"],
  ["/get-started", "Get Started"],
  ["/usage", "Usage"],
  ["/settings", "Settings"],
];

const STYLE = `
  :root { color-scheme: light; --bg: #ffffff; --panel: #f7f7f8; --text: #1b1b1f;
    --muted: #6b6b76; --line: #e6e6ea; --accent: #7857fc; }
  :root[data-theme="dark"] { color-scheme: dark; --bg: #141416; --panel: #1b1b1f;
    --text: #f2f2f4; --muted: #a0a0ab; --line: #2a2a30; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text);
    font: 14px/1.5 Inter, system-ui, sans-serif; }
  .shell { display: flex; height: 100%; }
  nav { width: 232px; flex-shrink: 0; border-right: 1px solid var(--line);
    background: var(--panel); padding: 12px 8px; display: flex; flex-direction: column; gap: 2px; }
  nav .brand { display: flex; gap: 8px; align-items: center; padding: 6px 8px 12px; font-weight: 600; }
  nav .brand span { width: 18px; height: 18px; border-radius: 5px; background: var(--accent); }
  nav a, nav button { all: unset; cursor: pointer; padding: 6px 8px; border-radius: 6px; color: var(--text); }
  nav a[aria-current="page"] { background: color-mix(in srgb, var(--accent) 12%, transparent); color: var(--accent); }
  nav .spacer { flex: 1; }
  nav button { color: var(--muted); }
  main { flex: 1; min-width: 0; padding: 28px 32px; }
  main h1 { font-size: 20px; margin: 0 0 6px; font-weight: 600; }
  main p { color: var(--muted); margin: 0; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin-top: 20px;
    color: var(--muted); font-size: 12px; }
  dd { margin: 0; color: var(--text); font-family: ui-monospace, monospace; }
  @media (max-width: 600px) { nav { width: 148px; } main { padding: 20px 16px; } }
`;

const script = ({ want }) => `
(function () {
  var PARENT = window.location.origin;
  var WANT = ${Number.isInteger(want) ? want : "null"};
  var DEFAULT = "/chat";
  var LABELS = ${JSON.stringify(Object.fromEntries(NAV))};
  var state = { keyHash: null, keys: 0, theme: null, route: null,
    hellos: 0, received: [], navigations: [], rejected: [], keyLog: [],
    foreignKeys: 0 };
  // This document's doc. Never exposed on the page or in state.
  var DOC = (function () {
    var bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    var text = "";
    bytes.forEach(function (b) { text += String.fromCharCode(b); });
    return btoa(text).split("+").join("-").split("/").join("_").replace(/=+$/, "");
  })();
  window.__console = state;
  var ready = false;
  var lastGate;
  var applying = false;

  function helloMessage(want) {
    var message = { type: "embed.hello", protocol: "omega-embed/1", doc: DOC, build: "fake-console-1" };
    if (Number.isInteger(want) && want >= 2 && want <= 8) message.want = want;
    return message;
  }
  function isNonce(value) {
    return typeof value === "string" && value.length === 22 && /^[A-Za-z0-9_-]{21}[AQgw]$/.test(value);
  }

  function route() { return (location.hash || "").slice(1) || DEFAULT; }
  function post(message) { window.parent.postMessage(message, PARENT); }
  function render() {
    state.route = route();
    var base = state.route.split("?")[0];
    var top = "/" + (base.split("/")[1] || "");
    document.getElementById("title").textContent = LABELS[top] || "Not found";
    document.getElementById("route").textContent = state.route;
    document.getElementById("theme").textContent = state.theme || "unset";
    document.getElementById("key").textContent = state.keyHash ? "received" : "waiting";
    document.querySelectorAll("nav a").forEach(function (link) {
      if (link.getAttribute("href") === "#" + top) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  }
  async function sha256(text) {
    var digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest)).map(function (b) {
      return b.toString(16).padStart(2, "0");
    }).join("");
  }

  window.addEventListener("message", async function (event) {
    if (event.source !== window.parent || event.origin !== PARENT) {
      state.rejected.push(event.origin);
      return;
    }
    var message = event.data;
    if (!message || typeof message !== "object" || Array.isArray(message)) return;
    state.received.push(message.type);
    if (message.type === "embed.probe") {
      if (!Object.keys(message).every(function (field) { return field === "type" || field === "challenge"; }) || !isNonce(message.challenge)) return;
      var response = helloMessage(WANT);
      response.type = "embed.proof";
      response.challenge = message.challenge;
      if (lastGate !== undefined) response.state = lastGate;
      // Reply only to this validated requesting origin. No auth, key or
      // document/challenge state is inferred or exposed by the probe.
      window.parent.postMessage(response, event.origin);
      return;
    }
    if (message.type === "embed.key") {
      if (!Object.keys(message).every(function (field) { return ["type", "token", "doc", "left"].includes(field); })) return;
      if (!isNonce(message.doc) || typeof message.token !== "string") return;
      var token = message.token.trim();
      if (!token || token.length > 8192 || /\\s/.test(token)) return;
      // A key for another document (or none) is not this page's.
      if (message.doc !== DOC) {
        state.foreignKeys += 1;
        return;
      }
      state.keys += 1;
      stopHello();
      var left = Number.isInteger(message.left) && message.left > 0 && message.left < 8 ? message.left : undefined;
      // Logged in arrival order; the digest fills in later (it is async, and
      // digests of back-to-back keys can finish in any order).
      var entry = { hash: null, left: left === undefined ? null : left };
      state.keyLog.push(entry);
      var hash = await sha256(token);
      entry.hash = hash;
      state.keyHash = hash;
      if (left !== undefined && left > 0) return;
      if (!ready) {
        ready = true;
        lastGate = "ready";
        post({ type: "embed.gate", state: lastGate, doc: DOC });
      }
      render();
      post({ type: "embed.route", path: route() });
    } else if (message.type === "embed.theme") {
      state.theme = message.mode;
      if (message.mode === "dark") document.documentElement.dataset.theme = "dark";
      else delete document.documentElement.dataset.theme;
      render();
    } else if (message.type === "embed.navigate") {
      state.navigations.push(message.path);
      applying = true;
      history.replaceState(null, "", location.pathname + location.search + "#" + message.path);
      applying = false;
      render();
    }
  });

  window.addEventListener("hashchange", function () {
    render();
    if (ready && !applying) post({ type: "embed.route", path: route() });
  });

  document.addEventListener("click", function (event) {
    var home = event.target.closest("[data-future-agi]");
    if (home) post({ type: "embed.navigate", path: "/dashboard" });
  });

  var helloTimer = null;
  function stopHello() { if (helloTimer) clearInterval(helloTimer); helloTimer = null; }
  function hello() {
    state.hellos += 1;
    post(helloMessage(WANT));
    if (state.hellos >= 20) stopHello();
  }
  // A later hello for more keys, as the EE Console's key pool sends it.
  window.__askForKeys = function (want) {
    post(helloMessage(want));
  };
  render();
  hello();
  helloTimer = setInterval(hello, 500);
})();
`;

function fakeConsoleHtml({ want = null } = {}) {
  const links = NAV.map(
    ([path, label]) => `<a href="#${path}" data-nav="${path}">${label}</a>`,
  ).join("");
  return `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Omega Console (fake)</title><style>${STYLE}</style></head>
<body><div class="shell">
<nav aria-label="Console">
  <div class="brand"><span></span>Omega Console</div>
  ${links}
  <div class="spacer"></div>
  <button type="button" data-future-agi>Future AGI</button>
</nav>
<main>
  <h1 id="title"></h1>
  <p>Stand-in for the EE Console, speaking omega-embed/1.</p>
  <dl>
    <dt>Route</dt><dd id="route"></dd>
    <dt>Theme</dt><dd id="theme"></dd>
    <dt>Key</dt><dd id="key"></dd>
  </dl>
</main>
</div><script>${script({ want })}</script></body></html>`;
}

module.exports = { fakeConsoleHtml };
