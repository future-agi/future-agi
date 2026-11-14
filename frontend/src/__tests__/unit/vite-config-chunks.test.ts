import { describe, expect, it, vi } from "vitest";

// Vite and its compiler plugins load esbuild / swc, which cannot run under the
// jsdom test environment; only the chunking function matters here.
vi.mock("vite", () => ({ defineConfig: (config: unknown) => config }));
vi.mock("@vitejs/plugin-react-swc", () => ({
  default: () => ({ name: "vite:react-swc" }),
}));
vi.mock("vite-plugin-checker", () => ({
  default: () => ({ name: "vite-plugin-checker" }),
}));

const loadConfig = async () => import("../../../vite.config.js");

const moduleId = (pkg: string, file = "index.js") =>
  `/app/frontend/node_modules/${pkg}/${file}`;

describe("vite config vendor chunks", () => {
  it("is the manualChunks function of the build", async () => {
    const { default: config, vendorChunkFor } = await loadConfig();
    expect(config.build.rollupOptions.output.manualChunks).toBe(vendorChunkFor);
  });

  it.each([
    ["react", "vendor"],
    ["react-dom", "vendor"],
    ["scheduler", "vendor"],
    ["@babel/runtime", "vendor"],
    ["lodash", "utils"],
    ["lodash-es", "utils"],
    ["date-fns", "utils"],
    ["@mui/material", "mui"],
    ["@mui/system", "mui"],
    ["@emotion/react", "mui"],
    ["@emotion/styled", "mui"],
    ["@sentry/react", "sentry"],
    ["@sentry/core", "sentry"],
    ["@sentry-internal/replay", "sentry-replay"],
    ["@sentry-internal/replay-canvas", "sentry-replay"],
    ["ag-grid-community", "grid"],
    ["ag-grid-enterprise", "grid"],
    ["ag-grid-react", "grid"],
  ])("puts %s in the %s chunk", async (pkg, chunk) => {
    const { vendorChunkFor } = await loadConfig();
    expect(vendorChunkFor(moduleId(pkg))).toBe(chunk);
  });

  // Libraries that only some pages use, and helpers shared with the app shell,
  // must stay out of every named chunk so they never load with the first page.
  it.each([
    "react-markdown",
    "remark-gfm",
    "react-syntax-highlighter",
    "highlight.js",
    "refractor",
    "quill",
    "react-quill",
    "@mui/x-data-grid",
    "@mui/x-date-pickers",
    "@mui/lab",
    "@xyflow/react",
    "apexcharts",
    "pdfjs-dist",
    "three",
    "@react-pdf-viewer/core",
    "swiper",
  ])("leaves %s to the default per-route split", async (pkg) => {
    const { vendorChunkFor } = await loadConfig();
    expect(vendorChunkFor(moduleId(pkg))).toBeUndefined();
  });

  it("leaves app source to the default split", async () => {
    const { vendorChunkFor } = await loadConfig();
    expect(vendorChunkFor("/app/frontend/src/app.jsx")).toBeUndefined();
  });

  it("keeps the lazy-import helper with React, since every page needs it", async () => {
    const { vendorChunkFor } = await loadConfig();
    expect(vendorChunkFor("\0vite/preload-helper.js")).toBe("vendor");
  });

  it("matches the innermost package of a nested dependency", async () => {
    const { vendorChunkFor } = await loadConfig();
    expect(
      vendorChunkFor(moduleId("react-markdown", "node_modules/react/index.js")),
    ).toBe("vendor");
    expect(
      vendorChunkFor(
        moduleId(
          "@mui/material",
          "node_modules/@babel/runtime/helpers/extends.js",
        ),
      ),
    ).toBe("vendor");
    expect(
      vendorChunkFor(moduleId("quill", "node_modules/eventemitter3/index.js")),
    ).toBeUndefined();
  });

  it("matches the innermost scoped package in Windows paths", async () => {
    const { vendorChunkFor } = await loadConfig();
    expect(
      vendorChunkFor(
        "C:\\app\\node_modules\\@sentry\\react\\node_modules\\@sentry\\core\\build\\esm\\index.js",
      ),
    ).toBe("sentry");
    expect(
      vendorChunkFor(
        "C:\\app\\node_modules\\@sentry-internal\\replay\\build\\esm\\index.js",
      ),
    ).toBe("sentry-replay");
  });

  it("matches Windows paths", async () => {
    const { vendorChunkFor } = await loadConfig();
    expect(
      vendorChunkFor(
        "C:\\app\\frontend\\node_modules\\@mui\\material\\Button\\index.js",
      ),
    ).toBe("mui");
  });
});
