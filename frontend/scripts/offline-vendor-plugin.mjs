// Vendor runtime configuration does not tree-shake these embedded defaults.
// AG Grid 35's ESM entry includes the Google-font loader even with
// loadThemeGoogleFonts:false. Monaco loader 1.4's config() merges over its CDN
// default. Replace only these reviewed module fragments, before minification.
// Fail on upstream drift rather than silently restoring network fallbacks.
const gridModule = "/node_modules/ag-grid-community/dist/package/main.esm.mjs";
const monacoModule =
  "/node_modules/@monaco-editor/loader/lib/es/config/index.js";
const gridLoader = `var loadGoogleFont = async (font, nonce) => {
  const css = \`@import url('https://\${googleFontsDomain}/css2?family=\${encodeURIComponent(font)}:wght@100;200;300;400;500;600;700;800;900&display=swap');
\`;
  _injectGlobalCSS(css, document.head, \`googleFont:\${font}\`, void 0, 0, nonce);
};
var googleFontsDomain = "fonts.googleapis.com";`;
const monacoDefault =
  "'https://cdn.jsdelivr.net/npm/monaco-editor@0.43.0/min/vs'";

function replaceOnce(code, expected, replacement, label) {
  if (code.split(expected).length !== 2)
    throw new Error(
      `Offline ${label} vendor contract changed; review before upgrading`,
    );
  return { code: code.replace(expected, replacement), map: null };
}

export function offlineVendorPlugin() {
  return {
    name: "offline-vendor-asset-defaults",
    enforce: "pre",
    // Development uses the same transform instead of an unmodified prebundle.
    config() {
      return {
        optimizeDeps: {
          exclude: ["ag-grid-community", "@monaco-editor/loader"],
        },
      };
    },
    transform(code, id) {
      const moduleId = id.replaceAll("\\", "/").split("?")[0];
      if (moduleId.endsWith(gridModule))
        return replaceOnce(
          code,
          gridLoader,
          "var loadGoogleFont = async () => {};",
          "AG Grid",
        );
      if (moduleId.endsWith(monacoModule))
        return replaceOnce(
          code,
          monacoDefault,
          "'/assets/monaco/vs'",
          "Monaco",
        );
      return null;
    },
  };
}
