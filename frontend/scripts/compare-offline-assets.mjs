// CLOUD ONLY. Run matched browser-offline-assets.mjs captures first.
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { Buffer } from "node:buffer";
import { createRequire } from "node:module";
const [baseline, candidate, browserPackage, out, baselineSha, candidateSha] =
  process.argv.slice(2);
if (
  ![baselineSha, candidateSha].every((sha) => /^[a-f0-9]{40}$/.test(sha || ""))
)
  throw new Error(
    "Pass baseline/candidate directories, browser package, output JSON, and both exact SHAs",
  );
const { chromium } = createRequire(path.join(browserPackage, "package.json"))(
  "playwright",
);
const read = (dir) =>
  JSON.parse(fs.readFileSync(path.join(dir, "summary.json"), "utf8")).results;
const before = read(baseline);
const after = read(candidate);
const comparisons = [];
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.route("**/*", (route) => route.abort());
  const beforeRoutes = before.filter((row) => row.route);
  const afterRoutes = after.filter((row) => row.route);
  if (beforeRoutes.length !== afterRoutes.length)
    throw new Error("Route coverage differs between captures");
  for (const row of afterRoutes) {
    const old = beforeRoutes.find(
      (v) => v.browser === row.browser && v.route === row.route,
    );
    if (!old?.screenshot || !row.screenshot) {
      comparisons.push({
        browser: row.browser,
        route: row.route,
        pass: false,
        error: "Missing matched capture; visual equivalence is unproven",
      });
      continue;
    }
    const images = [
      path.join(baseline, old.screenshot),
      path.join(candidate, row.screenshot),
    ].map((file) => fs.readFileSync(file).toString("base64"));
    const pixels = await page.evaluate(async ([a, b]) => {
      const decode = async (base64) => {
        const img = new Image();
        img.src = `data:image/png;base64,${base64}`;
        await img.decode();
        const canvas = document.createElement("canvas");
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(img, 0, 0);
        return {
          width: img.width,
          height: img.height,
          pixels: ctx.getImageData(0, 0, img.width, img.height).data,
        };
      };
      const x = await decode(a);
      const y = await decode(b);
      if (x.width !== y.width || x.height !== y.height)
        return { sameDimensions: false };
      const diffCanvas = document.createElement("canvas");
      diffCanvas.width = x.width;
      diffCanvas.height = x.height;
      const diffContext = diffCanvas.getContext("2d");
      const diff = diffContext.createImageData(x.width, x.height);
      let changed = 0;
      let maxChannelDelta = 0;
      for (let i = 0; i < x.pixels.length; i += 4) {
        let different = false;
        for (let channel = 0; channel < 4; channel++) {
          const delta = Math.abs(x.pixels[i + channel] - y.pixels[i + channel]);
          if (delta) different = true;
          maxChannelDelta = Math.max(maxChannelDelta, delta);
        }
        if (different) changed++;
        diff.data[i] = different ? 220 : 245;
        diff.data[i + 1] = different ? 20 : 245;
        diff.data[i + 2] = different ? 60 : 245;
        diff.data[i + 3] = 255;
      }
      diffContext.putImageData(diff, 0, 0);
      return {
        diffPng: diffCanvas.toDataURL("image/png").split(",")[1],
        sameDimensions: true,
        width: x.width,
        height: x.height,
        changedPixels: changed,
        changedRatio: changed / (x.width * x.height),
        maxChannelDelta,
      };
    }, images);
    const semanticFields = [
      "font",
      "interLoaded",
      "iconGeometry",
      "gridValues",
      "cellTypography",
    ];
    const semantic = Object.fromEntries(
      semanticFields.map((key) => [
        key,
        old.render[key] !== undefined &&
          row.render[key] !== undefined &&
          JSON.stringify(old.render[key]) === JSON.stringify(row.render[key]),
      ]),
    );
    const firefoxBaselineFontUnavailable =
      row.browser === "firefox" &&
      old.render.interLoaded === false &&
      row.render.interLoaded === true &&
      !old.error;
    const baselineOtherwiseReady =
      old.render.text.includes("Offline asset fixture") &&
      old.render.svgCount > 0 &&
      old.render.missing.length === 0 &&
      (!row.route.includes("?tab=data") ||
        (old.render.grids > 0 && old.render.gridValues.length >= 2));
    const diffFile = `${row.screenshot.replace(/\.png$/, "")}-diff.png`;
    if (pixels.diffPng) {
      fs.writeFileSync(
        path.join(path.dirname(out), diffFile),
        Buffer.from(pixels.diffPng, "base64"),
      );
      delete pixels.diffPng;
    }
    comparisons.push({
      browser: row.browser,
      route: row.route,
      pixels,
      semantic,
      diffFile,
      firefoxBaselineFontUnavailable,
      baselineCapturePassed: old.pass,
      candidateCapturePassed: row.pass,
      pass:
        (old.pass ||
          (firefoxBaselineFontUnavailable && baselineOtherwiseReady)) &&
        row.pass &&
        pixels.sameDimensions &&
        pixels.changedRatio <= 0.005 &&
        Object.entries(semantic).every(
          ([key, equal]) =>
            equal || (key === "interLoaded" && firefoxBaselineFontUnavailable),
        ),
    });
  }
} finally {
  await browser.close();
}
const passed = comparisons.length > 0 && comparisons.every(({ pass }) => pass);
fs.writeFileSync(
  out,
  `${JSON.stringify(
    {
      baselineRevision: `git:${baselineSha}`,
      candidateRevision: `git:${candidateSha}`,
      passed,
      note: "Round-four policy: every measured semantic equal and changed pixels <= 0.5% per screenshot. Firefox baseline cannot load Google Fonts offline, so its loaded-font boolean is recorded but excluded only when baseline=false/candidate=true; all other semantics and pixels still gate. Every diff image still requires human/model visual review and attribution; numeric pass alone is not visual acceptance.",
      comparisons,
    },
    null,
    2,
  )}\n`,
);
if (!passed) process.exitCode = 1;
