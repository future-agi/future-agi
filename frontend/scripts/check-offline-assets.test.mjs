import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { scanDist } from "./check-offline-assets.mjs";

const dnsControl = '<meta http-equiv="x-dns-prefetch-control" content="off">';
for (const [label, html] of [
  ["missing", "<head></head>"],
  ["enabled", dnsControl.replace('"off"', '"on"')],
  ["duplicated", dnsControl + dnsControl],
  ["comment only", `<!-- ${dnsControl} -->`],
  ["script string", `<script>const example='${dnsControl}'</script>`],
  ["in body", `<body>${dnsControl}</body>`],
  ["after resource", `<script src="/entry.js"></script>${dnsControl}`],
])
  test(`DNS prefetch emitted gate rejects control ${label}`, (t) => {
    const result = scanDist(fixture(t, "const x=1", html));
    assert.equal(result.dnsPrefetchControlGatePassed, false);
  });
test("DNS prefetch emitted gate requires exactly one early off control in every HTML entry", (t) => {
  const root = fixture(t, "const x=1", dnsControl);
  const result = scanDist(root);
  assert.equal(result.dnsPrefetchControlGatePassed, true);
  assert.deepEqual(result.dnsPrefetchControls, [
    { file: "index.html", count: 1, content: "off", early: true, passed: true },
  ]);
  fs.writeFileSync(path.join(root, "other.html"), "<html></html>");
  assert.equal(scanDist(root).dnsPrefetchControlGatePassed, false);
});

test("the application HTML disables speculative DNS before any resource", (t) => {
  const html = fs.readFileSync(
    new URL("../index.html", import.meta.url),
    "utf8",
  );
  assert.equal(
    scanDist(fixture(t, "const x=1", html)).dnsPrefetchControlGatePassed,
    true,
  );
});

function fixture(t, source, html = '<script src="/entry.js"></script>') {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "offline-dist-test-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "index.html"), html);
  fs.writeFileSync(path.join(root, "entry.js"), source);
  return root;
}
for (const source of [
  'image.src="https://new-assets.example.test/icon.png"',
  'fetch("https://docs.example.test/guide")',
  'fetch("https://" + host)',
  "fetch(`https://${host}/resource`)",
  'const nested = {loader: {src: "https://example.test/script"}}',
  'new XMLHttpRequest().open("GET","https://example.test/data")',
  'axios.get("https://example.test/data")',
  'import("https://example.test/module.js")',
  'importScripts("//example.test/worker.js")',
  'new Worker("https://example.test/worker.js")',
  'new EventSource("https://example.test/events")',
  'new WebSocket("wss://example.test/socket")',
  'jsx("img",{src:"//example.test/image"})',
  'jsx("img",{srcSet:"https://example.test/a 1x, //example.test/b 2x"})',
  'jsx("link",{rel:"stylesheet",href:"https://example.test/style"})',
  'jsx("iframe",{src:"https://example.test/embed"})',
  'const style="@import url(https://example.test/font)"',
  'const style="background:url(//example.test/image)"',
  'const loader={src:"https://example.test/script"}',
  'const unresolved="https://docs.example.test/guide"',
  'const fontHost="fonts.googleapis.com"',
  String.raw`image.src="https:\/\/example.test/icon"`,
])
  test(`fails resource or unknown context: ${source}`, (t) => {
    const result = scanDist(fixture(t, source));
    assert.equal(result.strictAllRuntimeResourcesGatePassed, false);
    assert.ok(result.remoteResources.length > 0);
  });
for (const html of [
  '<img src="https://example.test/i">',
  '<img srcset="//example.test/i 2x">',
  '<link rel="manifest" href="https://example.test/m">',
  '<link rel="preload" href="https://example.test/f">',
  '<iframe src="https://example.test/e"></iframe>',
  '<style>@import "https://example.test/f";</style>',
])
  test(`fails markup fetch: ${html}`, (t) => {
    assert.equal(
      scanDist(fixture(t, "const x=1", html))
        .strictAllRuntimeResourcesGatePassed,
      false,
    );
  });
test("reviewed non-fetch categories are counted; same URLs in fetch contexts fail", (t) => {
  const source = `
    // Licence https://example.test/legal
    jsx("a",{href:"https://example.test/help"});
    window.open("https://example.test/help");
    document.createElementNS("http://www.w3.org/2000/svg","svg");
    throw new Error("See documentation https://example.test/error");
  `;
  const root = fixture(
    t,
    source,
    '<a href="https://example.test/help">Help</a><script src="/entry.js"></script>',
  );
  const result = scanDist(root);
  assert.equal(result.strictAllRuntimeResourcesGatePassed, true);
  assert.equal(result.categoryCounts.USER_CLICKED_LINK, 3);
  assert.equal(result.categoryCounts.XML_NAMESPACE, 1);
  assert.equal(result.categoryCounts.COMMENT_OR_LICENCE, 1);
  assert.equal(result.categoryCounts.ERROR_MESSAGE, 1);
  fs.appendFileSync(
    path.join(root, "entry.js"),
    ';fetch("https://example.test/help")',
  );
  assert.equal(scanDist(root).strictAllRuntimeResourcesGatePassed, false);
});
test("same-origin resources need no exception", (t) => {
  assert.equal(
    scanDist(fixture(t, 'image.src="/assets/icon.svg"'))
      .strictAllRuntimeResourcesGatePassed,
    true,
  );
});
test("an index without a nonempty emitted script is not a production build", (t) => {
  assert.equal(
    scanDist(fixture(t, "")).strictAllRuntimeResourcesGatePassed,
    false,
  );
});
test("parse failures are errors, never silent skips", (t) => {
  assert.equal(scanDist(fixture(t, "invalid ?")).errors.length, 1);
});
for (const url of [
  "https://cdn.jsdelivr.net/editor.js",
  "https://unpkg.com/editor",
  "https://cdnjs.cloudflare.com/icon.js",
  "https://new.example.test/font.woff2",
  "https://new.example.test/editor.js",
  "https://new.example.test/icons.css",
  "https://new.example.test/icon.png",
  "cdn.jsdelivr.net",
]) {
  test(`asset gate includes editor CDNs and unknown font hosts: ${url}`, (t) => {
    const result = scanDist(
      fixture(t, `const unresolved=${JSON.stringify(url)}`),
    );
    assert.equal(result.fontIconEditorAssetGatePassed, false);
  });
}
test("telemetry is classified but continues to fail the strict inventory", (t) => {
  const result = scanDist(
    fixture(
      t,
      'const config={api_host:"https://us.i.posthog.com",recorder_src:"https://cdn.mxpnl.com/libs/mixpanel-recorder.min.js"}; fetch("https://www.google.com/recaptcha/api.js")',
      '<script src="https://www.googletagmanager.com/gtm.js"></script>',
    ),
  );
  assert.equal(result.categoryCounts.PRODUCT_TELEMETRY_AND_ANTIBOT, 4);
  assert.equal(result.strictAllRuntimeResourcesGatePassed, false);
  assert.equal(result.fontIconEditorAssetGatePassed, true);
  assert.ok(
    result.remoteResources.every((r) => r.fail && r.enablement.config.length),
  );
});
test("documentation structure is not a hostname exemption", (t) => {
  const source =
    'const x={references:[{name:"MDN",url:"https://example.test/help"}],description:{kind:"markdown",value:"Read [here](https://example.test/help)"}}; jsx("input",{placeholder:"https://example.test/help"});';
  assert.equal(
    scanDist(fixture(t, source)).categoryCounts.DOCUMENTATION_TEXT,
    3,
  );
  assert.equal(
    scanDist(fixture(t, 'const x={url:"https://example.test/help"}'))
      .strictAllRuntimeResourcesGatePassed,
    false,
  );
  assert.equal(
    scanDist(fixture(t, 'jsx("img",{src:"https://example.test/help"})'))
      .strictAllRuntimeResourcesGatePassed,
    false,
  );
});
test("bare asset hosts in inline HTML fail the emitted gate", (t) => {
  const result = scanDist(
    fixture(
      t,
      "const x=1",
      '<script>const host="cdn.jsdelivr.net";fetch("https://"+host+"/editor.js")</script>',
    ),
  );
  assert.equal(result.fontIconEditorAssetGatePassed, false);
});
test("licence sidecar schema does not exempt added resource properties", (t) => {
  const root = fixture(t, "const x=1");
  const folder = path.join(root, "assets/licences/icons");
  fs.mkdirSync(folder, { recursive: true });
  const set = {
    name: "Test",
    author: { name: "Author", url: "https://example.test/author" },
    license: { title: "MIT", spdx: "MIT", url: "https://example.test/license" },
    source: "@iconify/json@1/json/test.json",
    icons: ["test:one"],
  };
  const file = path.join(folder, "sets.json");
  fs.writeFileSync(file, JSON.stringify({ test: set }));
  assert.equal(scanDist(root).categoryCounts.PROVENANCE_METADATA, 2);
  fs.writeFileSync(
    file,
    JSON.stringify({
      test: { ...set, src: "https://cdn.jsdelivr.net/icon.svg" },
    }),
  );
  assert.equal(scanDist(root).fontIconEditorAssetGatePassed, false);
});
test("CSS-header licence provenance is metadata, not a stylesheet exemption", (t) => {
  const root = fixture(t, "const x=1"),
    folder = path.join(root, "assets/licences/icons");
  fs.mkdirSync(folder, { recursive: true });
  const file = path.join(folder, "notice-provenance.json"),
    url = "https://raw.example.test/icons/add.css";
  const notice = {
    prefix: "test",
    iconSource: "@iconify/json@1/json/test.json",
    upstreamRevision: `git:${"a".repeat(40)}`,
    noticeDigest: `sha256:${"b".repeat(64)}`,
    noticeSource: url,
    copyright: ["Example copyright"],
  };
  fs.writeFileSync(file, JSON.stringify([notice]));
  assert.equal(scanDist(root).categoryCounts.PROVENANCE_METADATA, 1);
  assert.equal(scanDist(root).fontIconEditorAssetGatePassed, true);
  fs.appendFileSync(
    path.join(root, "entry.js"),
    `;fetch(${JSON.stringify(url)})`,
  );
  assert.equal(scanDist(root).fontIconEditorAssetGatePassed, false);
  fs.writeFileSync(path.join(root, "entry.js"), "const x=1");
  fs.writeFileSync(file, JSON.stringify([{ ...notice, src: url }]));
  assert.equal(scanDist(root).fontIconEditorAssetGatePassed, false);
});
test("provenance constant must terminate exclusively in the mark metadata argument", (t) => {
  const source =
    'const mark=(mode,source,licence,extra={})=>({mode,source,licence,...extra}); const base="https://cdn.jsdelivr.net/icons"; const x=mark("mono",`${base}/one.svg`,"MIT");';
  const result = scanDist(fixture(t, source));
  assert.equal(result.categoryCounts.PROVENANCE_METADATA, 1);
  assert.equal(result.fontIconEditorAssetGatePassed, true);
  for (const consumer of [
    "fetch(x.source)",
    'fetch(x["source"])',
    "const {source: url}=x;fetch(url)",
  ])
    assert.equal(
      scanDist(fixture(t, source + consumer)).fontIconEditorAssetGatePassed,
      false,
    );
  assert.equal(
    scanDist(fixture(t, source + "image.src=base;"))
      .fontIconEditorAssetGatePassed,
    false,
  );
  assert.equal(
    scanDist(
      fixture(
        t,
        'const mark=(mode,src,licence)=>({mode,src,licence}); mark("mono","https://cdn.jsdelivr.net/icon.svg","MIT")',
      ),
    ).fontIconEditorAssetGatePassed,
    false,
  );
  assert.equal(
    scanDist(
      fixture(
        t,
        source +
          'function other(mark){mark("mono","https://cdn.jsdelivr.net/x","MIT")}',
      ),
    ).fontIconEditorAssetGatePassed,
    false,
  );
  assert.equal(
    scanDist(
      fixture(
        t,
        source.replace("const mark=", "let mark=") +
          'mark=fetch;mark("mono","https://cdn.jsdelivr.net/x","MIT");',
      ),
    ).fontIconEditorAssetGatePassed,
    false,
  );
});
