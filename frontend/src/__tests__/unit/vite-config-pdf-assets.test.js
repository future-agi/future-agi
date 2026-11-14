import { describe, expect, it, vi } from "vitest";
import pdfjsPackage from "pdfjs-dist/package.json";

// Vite and its compiler plugins load esbuild / swc, which cannot run under the
// jsdom test environment; only the output file names matter here.
vi.mock("vite", () => ({ defineConfig: (config) => config }));
vi.mock("@vitejs/plugin-react-swc", () => ({
  default: () => ({ name: "vite:react-swc" }),
}));
vi.mock("vite-plugin-checker", () => ({
  default: () => ({ name: "vite-plugin-checker" }),
}));

const loadAssetFileNames = async () => {
  const { default: config } = await import("../../../vite.config.js");
  return config.build.rollupOptions.output.assetFileNames;
};

describe("vite config PDF.js asset names", () => {
  it("emits the JPEG 2000 decoder files together under their own names", async () => {
    const assetFileNames = await loadAssetFileNames();
    // PDF.js loads both files by name from the single `wasmUrl` directory.
    for (const name of ["openjpeg.wasm", "openjpeg_nowasm_fallback.js"]) {
      expect(assetFileNames({ name })).toBe(
        `assets/pdfjs-${pdfjsPackage.version}/[name].[ext]`,
      );
    }
  });

  it("keeps content hashes for the worker and every other asset", async () => {
    const assetFileNames = await loadAssetFileNames();
    expect(assetFileNames({ name: "pdf.worker.min.mjs" })).toBe(
      "assets/[name]-[hash].js",
    );
    expect(assetFileNames({ name: "logo.svg" })).toBe(
      "assets/[name]-[hash].[ext]",
    );
  });
});
