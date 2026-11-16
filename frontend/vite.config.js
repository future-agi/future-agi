import { sentryVitePlugin } from "@sentry/vite-plugin";
/* eslint-env node */
import fs from "fs";
import { createRequire } from "module";
import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import checker from "vite-plugin-checker";

// ----------------------------------------------------------------------

const pdfjsVersion = createRequire(import.meta.url)(
  "pdfjs-dist/package.json",
).version;
const PDFJS_DECODER_FILES = new Set([
  "openjpeg.wasm",
  "openjpeg_nowasm_fallback.js",
]);

// Upload source maps to Sentry only when a build explicitly asks for it.
// Without an auth token the plugin still runs sentry-cli, which falls back to
// any credentials on the machine (~/.sentryclirc, or a .sentryclirc in any
// parent directory of the build), so a plain local `vite build` would upload.
export const shouldUploadSentrySourceMaps = (env = process.env) =>
  env.SENTRY_UPLOAD_SOURCEMAPS === "true";

// Long-lived vendor chunks, matched by exact package name.
//
// A manual chunk also takes in the dependencies of its modules that no other
// chunk claims. When a group holds a library that only some pages use (the
// markdown renderer, syntax highlighting, the rich-text editor, MUI X), a
// dependency it shares with the app shell lands in that chunk too, and the
// app entry then imports the whole chunk on every page just to reach that
// dependency. Only list packages the first page needs anyway, or packages
// with no dependencies in common with the app shell. Rollup splits everything
// else per route.
const VENDOR_CHUNKS = {
  // @babel/runtime helpers are shared by mui and utils packages; keeping them
  // here avoids a utils -> mui -> utils chunk cycle.
  vendor: ["react", "react-dom", "scheduler", "@babel/runtime"],
  mui: [
    "@mui/material",
    "@mui/system",
    "@mui/utils",
    "@mui/base",
    "@mui/private-theming",
    "@mui/styled-engine",
    "@emotion/react",
    "@emotion/styled",
    "@emotion/cache",
    "@emotion/serialize",
    "@emotion/utils",
    "@emotion/sheet",
    "@emotion/hash",
    "@emotion/unitless",
    "@emotion/memoize",
    "@emotion/weak-memoize",
    "@emotion/use-insertion-effect-with-fallbacks",
    "@emotion/is-prop-valid",
  ],
  utils: ["lodash", "lodash-es", "date-fns", "numeral"],
  sentry: [
    "@sentry/react",
    "@sentry/browser",
    "@sentry/core",
    "@sentry-internal/browser-utils",
    "@sentry-internal/feedback",
  ],
  // @sentry/browser re-exports Replay. Claim its modules separately so the
  // sentry group's automatic dependency collection cannot pull them into startup.
  "sentry-replay": ["@sentry-internal/replay", "@sentry-internal/replay-canvas"],
  grid: ["ag-grid-enterprise", "ag-grid-community", "ag-grid-react"],
};
const VENDOR_CHUNK_BY_PACKAGE = Object.fromEntries(
  Object.entries(VENDOR_CHUNKS).flatMap(([chunk, pkgs]) =>
    pkgs.map((pkg) => [pkg, chunk]),
  ),
);

// Last node_modules segment, so a copy nested inside a package
// (quill/node_modules/lodash-es) isn't counted as that package.
const NODE_MODULES_PACKAGE_RE =
  /.*[\\/]node_modules[\\/]((?:@[^\\/]+[\\/])?[^\\/]+)[\\/]/;

export const vendorChunkFor = (id) => {
  // Vite's lazy-import helper is needed by every page: keep it with React.
  if (id.includes("vite/preload-helper")) return "vendor";
  const match = id.match(NODE_MODULES_PACKAGE_RE);
  if (!match) return undefined;
  return VENDOR_CHUNK_BY_PACKAGE[match[1].replaceAll("\\", "/")];
};

export default defineConfig({
  base: "/",
  plugins: [
    react(),
    checker({
      overlay: false,
      eslint: {
        lintCommand: 'eslint "./src/**/*.{js,jsx,ts,tsx}"',
      },
    }),
    sentryVitePlugin({
      org: "future-agi",
      project: "frontend",
      disable: !shouldUploadSentrySourceMaps(),
    }),
  ],
  resolve: {
    alias: [
      {
        find: /^~(.+)/,
        replacement: path.join(process.cwd(), "node_modules/$1"),
      },
      {
        find: /^src(.+)/,
        replacement: path.join(process.cwd(), "src/$1"),
      },
    ],
  },
  build: {
    // Don't preload all chunks — let the browser fetch them on demand.
    // Vite's default injects <link rel="modulepreload"> for every chunk in the
    // entry graph, which forces the browser to download 5MB+ of JS upfront
    // (including AG Grid 2.1MB, charts 543KB) even if the page doesn't need them.
    modulePreload: false,
    // Enable file hashing for better caching
    rollupOptions: {
      output: {
        // Ensure consistent file hashing for cache busting
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        // Serve the module worker as JavaScript on nginx installations whose
        // mime.types does not include .mjs. The pinned worker bytes stay unchanged.
        assetFileNames: (asset) => {
          if (asset.name === "pdf.worker.min.mjs")
            return "assets/[name]-[hash].js";
          // PDF.js loads its JPEG 2000 decoder files by name from one directory,
          // so they keep their names in a directory versioned like the worker.
          if (PDFJS_DECODER_FILES.has(asset.name))
            return `assets/pdfjs-${pdfjsVersion}/[name].[ext]`;
          return "assets/[name]-[hash].[ext]";
        },
        // See VENDOR_CHUNKS above for which libraries get their own chunk.
        manualChunks: vendorChunkFor,
      },
      // Reduce memory usage during build
      maxParallelFileOps: 2,
    },
    // Disable source maps in production to save memory unless needed for debugging
    sourcemap: false,
    // Optimize chunk size
    chunkSizeWarningLimit: 1000,
    // Enable minification
    minify: true,
    // Enable tree shaking
    target: "esnext",
  },
  optimizeDeps: {
    include: ["apexcharts", "react-apexcharts"],
  },
  server: {
    port: 3031,
    // In a git worktree, node_modules is a symlink to the main tree.
    // Resolve the real path so Vite's FS security allows serving from it.
    fs: {
      allow: [
        ".",
        ...(fs.existsSync("node_modules") &&
        fs.lstatSync("node_modules").isSymbolicLink()
          ? [fs.realpathSync("node_modules")]
          : []),
      ],
    },
    hmr: {
      overlay: false,
    },
    // Polling watcher — Docker bind mounts on macOS/Windows don't deliver
    // inotify events to the container, so chokidar's default (FS events)
    // misses host edits. Enabled only when VITE_USE_POLLING is set so
    // host-native dev keeps the cheap event-based watcher.
    watch: process.env.VITE_USE_POLLING
      ? { usePolling: true, interval: 100 }
      : undefined,
    headers: {
      // Prevent Clickjacking
      "X-Frame-Options": "DENY",

      // Comprehensive CSP - relaxed for development
      // "Content-Security-Policy": [
      //   "default-src 'self'",
      //   "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      //   "style-src 'self' 'unsafe-inline'",
      //   "img-src 'self' data: https:",
      //   "font-src 'self'",
      //   "frame-ancestors 'none'",
      //   "connect-src 'self' ws: wss:",
      //   "base-uri 'self'",
      //   "form-action 'self'",
      // ].join("; "),

      // Enable HSTS
      "Strict-Transport-Security":
        "max-age=31536000; includeSubDomains; preload",

      // Enable XSS Protection
      "X-XSS-Protection": "1; mode=block",

      // Prevent MIME type sniffing
      "X-Content-Type-Options": "nosniff",

      // Control referrer information
      // "Referrer-Policy": "strict-origin-when-cross-origin",

      // Additional recommended security headers
      "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    },
  },
  preview: {
    port: 3031,
  },
});
