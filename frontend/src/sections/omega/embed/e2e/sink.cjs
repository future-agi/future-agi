/* eslint-env node */
// The analytics sink for the embed-host e2e. Every request the browser makes
// is recorded (URL, headers, body, which frame). Requests to any non-loopback
// host (GTM, PostHog, Mixpanel, Sentry, New Relic, icon CDNs) are answered
// here, so nothing leaves the machine and every analytics payload is captured.
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");

const FRONTEND = path.resolve(__dirname, "../../../../..");
const LOOPBACK = /^(?:localhost|127\.0\.0\.1|\[::1\])$/;

const isLoopbackUrl = (url) => {
  try {
    return LOOPBACK.test(new URL(url).hostname);
  } catch {
    return true;
  }
};

const readLocal = (relative) => {
  try {
    return fs.readFileSync(path.join(FRONTEND, "node_modules", relative));
  } catch {
    return null;
  }
};

// GTM stand-in: sends a GA4-style page_view with the page URL on load and on
// every history change, the way a GTM page-view tag does.
const GTM_STUB = `(function(){
  function hit(name){
    var u='https://www.google-analytics.com/g/collect?v=2&tid=G-OMEGATEST&en='+name+
      '&dl='+encodeURIComponent(location.href)+'&dt='+encodeURIComponent(document.title);
    try{fetch(u,{method:'POST',mode:'no-cors',keepalive:true,body:JSON.stringify(window.dataLayer||[])});}catch(e){}
  }
  hit('page_view');
  var push=history.pushState, replace=history.replaceState;
  history.pushState=function(){var r=push.apply(this,arguments);hit('page_view');return r;};
  history.replaceState=function(){var r=replace.apply(this,arguments);hit('history_change');return r;};
  window.addEventListener('popstate',function(){hit('page_view');});
})();`;

function fulfillment(url) {
  const { hostname, pathname } = new URL(url);
  const js = (body) => ({
    status: 200,
    contentType: "application/javascript",
    body,
  });
  const json = (body) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  if (hostname === "www.googletagmanager.com") return js(GTM_STUB);
  if (hostname.endsWith("posthog.com")) {
    if (/\/(flags|decide)\/?/.test(pathname)) {
      return json({
        featureFlags: {},
        featureFlagPayloads: {},
        sessionRecording: { endpoint: "/s/" },
        supportedCompression: [],
      });
    }
    if (/recorder|posthog-recorder/.test(pathname)) {
      const file = readLocal("posthog-js/dist/posthog-recorder.js");
      if (file)
        return {
          status: 200,
          contentType: "application/javascript",
          body: file,
        };
    }
    if (pathname.endsWith(".js")) return js("");
    return json({ status: 1 });
  }
  if (hostname.endsWith("mxpnl.com") && /recorder/.test(pathname)) {
    const file = readLocal("mixpanel-browser/dist/mixpanel-recorder.min.js");
    if (file)
      return { status: 200, contentType: "application/javascript", body: file };
  }
  if (hostname.endsWith("iconify.design")) return json({});
  if (hostname.endsWith("mixpanel.com"))
    return { status: 200, contentType: "text/plain", body: "1" };
  return { status: 200, contentType: "application/json", body: "{}" };
}

// All readable forms of a body: text, URL-decoded, base64 `data=` fields,
// and gzip/deflate streams (whole body or after an envelope header line).
function bodyVariants(buffer) {
  if (!buffer || buffer.length === 0) return [];
  const out = [buffer.toString("utf8")];
  const tryPush = (fn) => {
    try {
      const value = fn();
      if (value) out.push(value.toString("utf8"));
    } catch {
      /* not this encoding */
    }
  };
  const inflateAll = (buf) => {
    tryPush(() => zlib.gunzipSync(buf));
    tryPush(() => zlib.inflateSync(buf));
    tryPush(() => zlib.inflateRawSync(buf));
  };
  inflateAll(buffer);
  let offset = 0;
  for (let i = 0; i < 4; i += 1) {
    offset = buffer.indexOf(0x0a, offset) + 1;
    if (offset <= 0) break;
    inflateAll(buffer.subarray(offset));
  }
  const text = out[0];
  tryPush(() => decodeURIComponent(text.replace(/\+/g, " ")));
  const data = /(?:^|&)data=([^&]*)/.exec(text);
  if (data) {
    tryPush(() => Buffer.from(decodeURIComponent(data[1]), "base64"));
    tryPush(() =>
      zlib.gunzipSync(Buffer.from(decodeURIComponent(data[1]), "base64")),
    );
  }
  return out;
}

const urlVariants = (url) => {
  const out = [url];
  try {
    out.push(decodeURIComponent(url));
    out.push(decodeURIComponent(decodeURIComponent(url)));
  } catch {
    /* keep raw */
  }
  return out;
};

// Analytics SDKs drop events from automated browsers (PostHog checks the user
// agent, its client hints and navigator.webdriver). Present as a normal
// browser so the SDKs send what they would send for a person.
async function presentAsPerson(context) {
  await context.addInitScript(() => {
    const strip = (text) => String(text).replace(/Headless/g, "");
    try {
      Object.defineProperty(Navigator.prototype, "webdriver", {
        get: () => false,
        configurable: true,
      });
      const ua = navigator.userAgent;
      Object.defineProperty(Navigator.prototype, "userAgent", {
        get: () => strip(ua),
        configurable: true,
      });
      const data = navigator.userAgentData;
      if (data) {
        const brands = data.brands.map((b) => ({
          ...b,
          brand: strip(b.brand),
        }));
        Object.defineProperty(Navigator.prototype, "userAgentData", {
          get: () => ({ brands, mobile: data.mobile, platform: data.platform }),
          configurable: true,
        });
      }
    } catch {
      /* best effort */
    }
  });
}

async function attachSink(context, { mainPage } = {}) {
  await presentAsPerson(context);
  const records = [];
  context.on("request", (request) => {
    let frameUrl = "";
    let isMainFrame = false;
    try {
      const frame = request.frame();
      frameUrl = frame.url();
      isMainFrame = mainPage
        ? frame === mainPage().mainFrame()
        : !frame.parentFrame();
    } catch {
      /* service worker or detached frame */
    }
    records.push({
      url: request.url(),
      method: request.method(),
      headers: request.headers(),
      body: request.postDataBuffer(),
      resourceType: request.resourceType(),
      isNavigation: request.isNavigationRequest(),
      frameUrl,
      isMainFrame,
      external: !isLoopbackUrl(request.url()),
    });
  });
  await context.route(
    (url) => !isLoopbackUrl(url.toString()),
    (route) => route.fulfill(fulfillment(route.request().url())),
  );
  return records;
}

// Every place a record can carry text: URL, headers, decoded bodies.
const recordTexts = (record) => [
  ...urlVariants(record.url),
  ...Object.values(record.headers || {}),
  ...bodyVariants(record.body),
];

const recordContains = (record, needle) =>
  recordTexts(record).some((text) => text.includes(needle));

const analyticsRecords = (records) => records.filter((r) => r.external);

module.exports = {
  attachSink,
  analyticsRecords,
  bodyVariants,
  isLoopbackUrl,
  recordContains,
};
