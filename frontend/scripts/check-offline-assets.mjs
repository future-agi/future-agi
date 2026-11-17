import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { inspectDnsPrefetchControl } from "./dns-prefetch-control.mjs";
import {
  javascriptOccurrences,
  textOccurrences,
  ASSET_HOST_PATTERN,
} from "./fetch-context.mjs";
import {
  productNetworkContext,
  PRODUCT_NETWORK_ENABLEMENT,
} from "./product-network-context.mjs";

const digest = (bytes) =>
  `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
// Includes editor CDNs and asset extensions on any otherwise unknown host.
const isAssetUrl = ({ url, category }) =>
  ASSET_HOST_PATTERN.test(url) ||
  /\.(?:woff2?|ttf|otf|eot|svg|ico|png|jpe?g|webp|gif)(?:[?#]|$)/i.test(url) ||
  (category !== "PRODUCT_TELEMETRY_AND_ANTIBOT" &&
    /\.(?:m?js|css)(?:[?#]|$)/i.test(url));
export function normalizeUrls(text) {
  return text
    .replace(/&#(?:x([\da-f]+)|(\d+));/gi, (match, hex, decimal) => {
      const value = parseInt(hex || decimal, hex ? 16 : 10);
      return value <= 0x10ffff ? String.fromCodePoint(value) : match;
    })
    .replace(/&colon;/gi, ":")
    .replace(/&sol;/gi, "/");
}

// No approval JSON or digest pins: a changed use is reclassified structurally.
// Includes SVG/JSON in addition to emitted JS/CSS/HTML. Run on the VM so even
// raw files withheld by the evidence pull scanner are covered, never bypass it.
export function scanDist(root) {
  const absoluteUrls = [],
    files = [],
    dnsPrefetchControls = [],
    errors = [];
  function walk(dir) {
    for (const entry of fs
      .readdirSync(dir, { withFileTypes: true })
      .sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(dir, entry.name);
      if (entry.isSymbolicLink()) {
        errors.push(`Symlink in dist: ${file}`);
        continue;
      }
      if (entry.isDirectory()) {
        walk(file);
        continue;
      }
      if (!/\.(?:js|mjs|cjs|css|html|svg|json)$/i.test(file)) continue;
      const bytes = fs.readFileSync(file);
      const fileDigest = digest(bytes);
      const relative = path.relative(root, file).split(path.sep).join("/");
      const record = {
        file: relative,
        bytes: bytes.length,
        digest: fileDigest,
      };
      files.push(record);
      const raw = bytes.toString("utf8");
      if (/\.html$/i.test(file))
        dnsPrefetchControls.push({
          file: relative,
          ...inspectDnsPrefetchControl(raw),
        });
      const isJs = /\.(?:js|mjs|cjs)$/.test(file);
      const text = isJs ? raw : normalizeUrls(raw);
      try {
        const rows = isJs
          ? javascriptOccurrences(text)
          : textOccurrences(text, path.extname(file));
        // The generated Iconify licence index is a non-executable sidecar. Only
        // the reviewed metadata shape qualifies; extra resource fields fail.
        if (relative === "assets/licences/icons/sets.json") {
          const sets = Object.values(JSON.parse(text));
          const shape = (obj, keys) =>
            obj &&
            typeof obj === "object" &&
            Object.keys(obj).every((k) => keys.includes(k));
          if (
            sets.length &&
            sets.every(
              (s) =>
                shape(s, ["name", "author", "license", "source", "icons"]) &&
                shape(s.author, ["name", "url"]) &&
                shape(s.license, ["title", "spdx", "url"]) &&
                typeof s.source === "string" &&
                s.source.startsWith("@iconify/json@") &&
                Array.isArray(s.icons) &&
                s.icons.every((i) => /^[a-z0-9-]+:[a-z0-9-]+$/.test(i)),
            )
          )
            rows.forEach((r) =>
              Object.assign(r, {
                category: "PROVENANCE_METADATA",
                reason:
                  "Generated licence-only JSON sidecar; validated metadata schema",
                fail: false,
              }),
            );
        }
        if (relative === "assets/licences/icons/notice-provenance.json") {
          const notices = JSON.parse(text);
          const fields = [
            "prefix",
            "iconSource",
            "package",
            "packageIconsDigest",
            "pinnedIconsDigest",
            "geometryMatchesPinned",
            "packageFiles",
            "upstreamRevision",
            "collectionModified",
            "upstreamLicencePaths",
            "noticeSource",
            "noticeDigest",
            "copyright",
            "ok",
            "upstreamPackage",
            "evidenceScope",
          ];
          if (
            Array.isArray(notices) &&
            notices.length &&
            notices.every(
              (notice) =>
                notice &&
                Object.keys(notice).every((key) => fields.includes(key)) &&
                /^[a-z0-9-]+$/.test(notice.prefix) &&
                /^@iconify\/json@/.test(notice.iconSource) &&
                /^git:[a-f0-9]{40}$/.test(notice.upstreamRevision) &&
                /^sha256:[a-f0-9]{64}$/.test(notice.noticeDigest) &&
                typeof notice.noticeSource === "string" &&
                Array.isArray(notice.copyright) &&
                notice.copyright.every((line) => typeof line === "string"),
            )
          )
            rows.forEach((row) =>
              Object.assign(row, {
                category: "PROVENANCE_METADATA",
                reason:
                  "Non-executable upstream licence evidence sidecar; validated provenance schema (including CSS-header notice citations)",
                fail: false,
              }),
            );
        }
        record.categoryCounts = {};
        for (const rawRow of rows) {
          const row = productNetworkContext(rawRow);
          record.categoryCounts[row.category] =
            (record.categoryCounts[row.category] || 0) + 1;
          absoluteUrls.push({
            ...row,
            file: relative,
            fileDigest,
            line: text.slice(0, row.offset).split("\n").length,
            context: text.slice(
              Math.max(0, row.offset - 100),
              row.offset + row.url.length + 100,
            ),
          });
        }
        record.passed = !rows.some((r) => r.fail);
      } catch (error) {
        errors.push(`${relative}: context parse failed: ${error.message}`);
        record.passed = false;
      }
    }
  }
  walk(root);
  if (
    !files.some(({ file }) => file === "index.html") ||
    !files.some(
      ({ file, bytes }) => /\.(?:js|mjs|cjs)$/.test(file) && bytes > 0,
    )
  )
    errors.push(
      "Not a production dist: index.html and emitted resource files are required",
    );
  const categoryCounts = {};
  for (const row of absoluteUrls)
    categoryCounts[row.category] = (categoryCounts[row.category] || 0) + 1;
  const remoteResources = absoluteUrls.filter((r) => r.fail);
  const assetViolations = remoteResources.filter(isAssetUrl);
  return {
    policy: "fetch-context-fail-closed-v2",
    productNetworkEnablement: PRODUCT_NETWORK_ENABLEMENT,
    dnsPrefetchControls,
    dnsPrefetchControlGatePassed:
      !errors.length &&
      dnsPrefetchControls.length > 0 &&
      dnsPrefetchControls.every((entry) => entry.passed),
    fontIconEditorAssetGatePassed: !errors.length && !assetViolations.length,
    scope:
      "Literal external URL contexts; unknown data flow fails. Dynamic API resources require browser/fixture proof separately.",
    fontIconGatePassed: !errors.length && !assetViolations.length,
    strictAllRuntimeResourcesGatePassed:
      !errors.length && !remoteResources.length,
    categoryCounts,
    errors,
    files,
    assetViolations,
    remoteResources,
    absoluteUrls,
  };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv[3])
    throw new Error(
      "Occurrence exceptions are not accepted by the fetch-context gate",
    );
  const result = scanDist(path.resolve(process.argv[2] || "dist"));
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (!result.strictAllRuntimeResourcesGatePassed) process.exitCode = 1;
}
