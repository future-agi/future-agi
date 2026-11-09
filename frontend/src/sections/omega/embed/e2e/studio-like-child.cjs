/* eslint-env node */
// RC1/A06 child double: the key lives only in this document's memory. A frame
// reload or remount starts keyless; no Web Storage read or write can recover
// it. Repeated hellos in the same document can race with a pending key check.
// window.__studio exposes only counts and hashes, never the key.
const KEY_CHECK_MS = 800;
const KEY_WAIT_MS = 3000;

const studioLikeScript = (parent) => `
<script>
(function () {
  var PARENT = ${JSON.stringify(parent)};
  var key = null;
  var state = {
    keyHash: null, keys: 0,
    hellos: 0, gates: [], theme: null, route: null
  };
  window.__studio = state;
  var done = false, tries = 0, lastGate = null, checking = false, timer;
  function route() { return (location.hash || "#/").slice(1) || "/"; }
  function post(message) { window.parent.postMessage(message, PARENT); }
  async function sha256(text) {
    var digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest)).map(function (b) {
      return b.toString(16).padStart(2, "0");
    }).join("");
  }
  function stopHello() { done = true; clearInterval(timer); }
  function hello() {
    if (done || tries >= 40) { stopHello(); return; }
    tries += 1;
    state.hellos += 1;
    post({ type: "embed.hello", protocol: "omega-embed/1" });
  }
  function gate(next) {
    if (next === "ready") stopHello();
    if (next === lastGate) return;
    lastGate = next;
    state.gates.push(next);
    document.getElementById("gate").textContent = next;
    post({ type: "embed.gate", state: next });
  }
  function checkKey() {
    if (checking || !key) return;
    checking = true;
    setTimeout(function () {
      gate("ready");
      state.route = route();
      post({ type: "embed.route", path: state.route });
    }, ${KEY_CHECK_MS});
  }
  window.addEventListener("message", async function (event) {
    if (event.source !== window.parent || event.origin !== PARENT) return;
    var message = event.data || {};
    if (message.type === "embed.key") {
      if (typeof message.token !== "string" || !message.token) return;
      stopHello();
      key = message.token;
      state.keys += 1;
      state.keyHash = await sha256(key);
      checkKey();
    } else if (message.type === "embed.theme") {
      state.theme = message.mode;
      document.documentElement.dataset.theme = message.mode;
    } else if (message.type === "embed.navigate") {
      history.replaceState(null, "", "#" + message.path);
      state.route = route();
    }
  });
  window.addEventListener("hashchange", function () {
    state.route = route();
    post({ type: "embed.route", path: state.route });
  });
  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-open]");
    if (target) post({ type: "embed.open", href: target.dataset.open });
  });
  timer = setInterval(hello, 250);
  hello();
  setTimeout(function () {
    if (!key) gate("needs-key");
  }, ${KEY_WAIT_MS});
})();
</script>`;

const studioLikeBody = (parent) => `
<h3>Studio-like child</h3>
<p>Gate: <code id="gate">checking</code></p>
<button data-open="/dashboard/observe/p1?from=studio">Open trace in Future AGI</button>
${studioLikeScript(parent)}`;

module.exports = { KEY_CHECK_MS, KEY_WAIT_MS, studioLikeBody };
