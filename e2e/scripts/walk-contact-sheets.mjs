#!/usr/bin/env node
// H1: contact sheets of a walk run's screenshots, so a reviewer reads one image
// per 16 shots instead of opening each (the 10-27 QA kit's reader,
// gw-qa-onekey/kit/contact-sheets.mjs, made path-independent).
// Usage: node scripts/walk-contact-sheets.mjs <walk results dir>
// Finds every .png under a `shots/<project>/` directory below it, writes
// <dir>/contact-sheets/<project>-NN.png and index.json (which originals each
// sheet holds). The sheets are rendered from inline data in a context that
// aborts every request, so nothing is fetched.
import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const dir = path.resolve(process.argv[2] ?? "");
if (!process.argv[2] || !fs.existsSync(dir)) {
  console.error("usage: walk-contact-sheets.mjs <walk results dir>");
  process.exit(2);
}
const shots = new Map();
const walk = (d) => {
  for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
    const full = path.join(d, entry.name);
    if (entry.isDirectory() && entry.name !== "contact-sheets") walk(full);
    else if (
      entry.isFile() &&
      entry.name.endsWith(".png") &&
      path.basename(path.dirname(path.dirname(full))) === "shots"
    ) {
      const project = path.basename(path.dirname(full));
      shots.set(project, [...(shots.get(project) ?? []), full]);
    }
  }
};
walk(dir);
const out = path.join(dir, "contact-sheets");
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1440 },
  serviceWorkers: "block",
});
await context.route("**/*", (route) => route.abort());
const page = await context.newPage();
const index = [];
const esc = (s) =>
  s.replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
for (const [project, files] of [...shots.entries()].sort()) {
  files.sort();
  for (let i = 0; i < files.length; i += 16) {
    const batch = files.slice(i, i + 16);
    const html =
      "<html><head><style>body{margin:0;background:#ddd;font:11px monospace}" +
      ".grid{display:grid;grid-template-columns:repeat(4,360px)}figure{margin:0;padding:5px;box-sizing:border-box;" +
      "height:360px;border:1px solid #888}img{display:block;width:350px;height:320px;object-fit:contain;background:#eee}" +
      'figcaption{overflow-wrap:anywhere;color:#111}</style></head><body><div class="grid">' +
      batch
        .map(
          (f) =>
            `<figure><img src="data:image/png;base64,${fs.readFileSync(f).toString("base64")}">` +
            `<figcaption>${esc(path.relative(dir, f))}</figcaption></figure>`,
        )
        .join("") +
      "</div></body></html>";
    await page.setContent(html);
    await page
      .locator("img")
      .evaluateAll((images) =>
        Promise.all(images.map((image) => image.decode())),
      );
    const file = `${project}-${String(i / 16 + 1).padStart(2, "0")}.png`;
    await page.screenshot({ path: path.join(out, file), fullPage: true });
    index.push({ file, originals: batch.map((f) => path.relative(dir, f)) });
  }
}
fs.writeFileSync(path.join(out, "index.json"), JSON.stringify(index, null, 2));
await browser.close();
console.log(
  `contact sheets: ${index.length}; originals: ${index.reduce((n, x) => n + x.originals.length, 0)}`,
);
