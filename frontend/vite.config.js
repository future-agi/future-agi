import { sentryVitePlugin } from "@sentry/vite-plugin";
/* eslint-env node */
import fs from "fs";
import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import checker from "vite-plugin-checker";
import { publicBundleIsolationPlugin } from "./scripts/public-bundle-plugin.mjs";
import { localMonacoPlugin } from "./scripts/local-monaco-plugin.mjs";
import { offlineVendorPlugin } from "./scripts/offline-vendor-plugin.mjs";
import { offlineIconPlugin } from "./scripts/offline-icon-plugin.mjs";

// ----------------------------------------------------------------------

// Upload source maps to Sentry only when a build explicitly asks for it.
// Without an auth token the plugin still runs sentry-cli, which falls back to
// any credentials on the machine (~/.sentryclirc, or a .sentryclirc in any
// parent directory of the build), so a plain local `vite build` would upload.
export const shouldUploadSentrySourceMaps = (env = process.env) =>
  env.SENTRY_UPLOAD_SOURCEMAPS === "true";

// Prototype mode's static files (fixture media, its service worker) live in
// public-prototype/, not public/: Vite copies public/ into every build, and
// these must never ship when prototype mode is off (tests F2). The plugin
// serves them in dev and emits them into dist only for prototype builds, at
// the same URLs (`/mock/multimodal/...`, `/prototype-mock-sw.js`).
export const PROTOTYPE_PUBLIC_DIR = "public-prototype";

export const prototypeBuildFromEnv = (env = {}) =>
  env.VITE_ENVIRONMENT === "local" && env.VITE_PROTOTYPE_MODE === "true";

// The legacy local mock's worker (public/mockServiceWorker.js; msw writes it
// there, so it stays in public/). main.jsx starts a mock worker only in local
// builds, and prototype-mock-sw.js imports it, so other builds drop it (HO S2).
export const MOCK_WORKER_FILE = "mockServiceWorker.js";

export const shipsMockWorker = (env = {}) => env.VITE_ENVIRONMENT === "local";

const STATIC_TYPES = {
  ".js": "text/javascript",
  ".png": "image/png",
  ".wav": "audio/wav",
  ".mp4": "video/mp4",
  ".pdf": "application/pdf",
};

/** Every file under `dir`, as `/`-separated paths relative to it (sorted). */
export function filesUnder(dir) {
  if (!fs.existsSync(dir)) return [];
  const out = [];
  const walk = (rel) => {
    for (const entry of fs.readdirSync(path.join(dir, rel), {
      withFileTypes: true,
    })) {
      const next = rel ? `${rel}/${entry.name}` : entry.name;
      if (entry.isDirectory()) walk(next);
      else if (entry.isFile()) out.push(next);
    }
  };
  walk("");
  return out.sort();
}

/**
 * The file a request URL names inside `dir`, or null (no traversal outside
 * `dir`, query and hash ignored).
 */
export function prototypeStaticFileOf(dir, url) {
  let pathname;
  try {
    pathname = decodeURIComponent(String(url ?? "").split(/[?#]/)[0]);
  } catch {
    return null;
  }
  if (!pathname.startsWith("/")) return null;
  const root = path.resolve(dir);
  const file = path.resolve(root, `.${pathname}`);
  if (!file.startsWith(`${root}${path.sep}`)) return null;
  return file;
}

/** `bytes=a-b` → {start, end} within `size`, or null (whole file). */
export function byteRangeOf(header, size) {
  const m = /^bytes=(\d*)-(\d*)$/.exec(String(header ?? "").trim());
  if (!m || (m[1] === "" && m[2] === "")) return null;
  let start;
  let end;
  if (m[1] === "") {
    start = Math.max(0, size - Number(m[2]));
    end = size - 1;
  } else {
    start = Number(m[1]);
    end = m[2] === "" ? size - 1 : Math.min(Number(m[2]), size - 1);
  }
  if (start > end || start >= size) return null;
  return { start, end };
}

export function prototypeStaticsPlugin() {
  let enabled = false;
  let mockWorker = false;
  let dir = PROTOTYPE_PUBLIC_DIR;
  return {
    name: "prototype-statics",
    configResolved(config) {
      enabled = prototypeBuildFromEnv(config.env);
      mockWorker = shipsMockWorker(config.env);
      dir = path.resolve(config.root, PROTOTYPE_PUBLIC_DIR);
    },
    // Vite copies public/ into the out dir before it writes the bundle, so
    // the worker is removed here, after the copy.
    writeBundle(options) {
      if (mockWorker || !options?.dir) return;
      fs.rmSync(path.join(options.dir, MOCK_WORKER_FILE), { force: true });
    },
    configureServer(server) {
      if (!enabled) return;
      server.middlewares.use((req, res, next) => {
        const file = prototypeStaticFileOf(dir, req.url);
        if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
          next();
          return;
        }
        const { size } = fs.statSync(file);
        const type =
          STATIC_TYPES[path.extname(file).toLowerCase()] ||
          "application/octet-stream";
        res.setHeader("Content-Type", type);
        res.setHeader("Accept-Ranges", "bytes");
        res.setHeader("Cache-Control", "no-cache");
        const range = byteRangeOf(req.headers.range, size);
        if (range) {
          res.statusCode = 206;
          res.setHeader(
            "Content-Range",
            `bytes ${range.start}-${range.end}/${size}`,
          );
          res.setHeader("Content-Length", range.end - range.start + 1);
          fs.createReadStream(file, range).pipe(res);
          return;
        }
        res.setHeader("Content-Length", size);
        fs.createReadStream(file).pipe(res);
      });
    },
    generateBundle() {
      if (!enabled) return;
      for (const rel of filesUnder(dir)) {
        this.emitFile({
          type: "asset",
          fileName: rel,
          source: fs.readFileSync(path.join(dir, rel)),
        });
      }
    },
  };
}

// Every `notistack` import (static, dynamic, and the snackbar barrel's
// re-export) resolves to the app's snackbar gate, so one shared check sees
// every toast; the gate's own import reaches the package unchanged, the same
// file a build and vitest load today. Like the @iconify/react alias below.
function notistackGatePlugin() {
  const gate = path.join(
    process.cwd(),
    "src/components/snackbar/notistack-gate.js",
  );
  return {
    name: "fa-notistack-gate",
    enforce: "pre",
    resolveId(source, importer) {
      if (source !== "notistack") return null;
      if (importer && path.resolve(importer.split("?")[0]) === gate)
        return null;
      return gate;
    },
  };
}

export default defineConfig({
  // Never environment-controlled: every app build compiles the census bypass out.
  // Only vitest.config.js enables it for source-level unit scenarios.
  define: { __DSX_CENSUS_B_TEST_BUILD__: "false" },
  base: "/",
  plugins: [
    notistackGatePlugin(),
    react(),
    prototypeStaticsPlugin(),
    publicBundleIsolationPlugin(),
    localMonacoPlugin(),
    offlineVendorPlugin(),
    offlineIconPlugin(),
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
      // All callers, including direct Icon imports, use the offline-only build.
      // Exact match keeps @iconify/react/offline available to our adapter.
      {
        find: /^@iconify\/react$/,
        replacement: path.join(
          process.cwd(),
          "src/components/iconify/offline.jsx",
        ),
      },
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
        assetFileNames: "assets/[name]-[hash].[ext]",
        // Split vendor chunks to reduce memory usage
        manualChunks: {
          // Vite's dynamic-import helper is claimed here, before `markdown`:
          // a manual chunk takes the static dependencies of its packages, and
          // react-syntax-highlighter's lazy languages depend on the helper, so
          // every page downloaded the 1.8 MB markdown chunk for 2 kB (perf #1).
          vendor: ["react", "react-dom", "\0vite/preload-helper.js"],
          mui: [
            "@mui/material",
            "@mui/lab",
            "@mui/x-data-grid",
            "@mui/x-date-pickers",
          ],
          utils: ["lodash", "date-fns", "numeral"],
          charts: ["apexcharts", "react-apexcharts"],
          // recharts + victory only used on 2-3 niche pages — let Vite split them
          // into separate chunks so they don't load for everyone.
          // Quill (react-quill, quill-mention) is split the same way: as a
          // manual chunk it also took react-dom's CommonJS proxy, which made
          // the whole editor an eager import of every page (perf #1).
          grid: ["ag-grid-enterprise", "ag-grid-react"],
          threejs: ["three", "scatter-gl"],
          flow: ["@xyflow/react", "@dagrejs/dagre"],
          sentry: ["@sentry/react"],
          media: [
            "swiper",
            "wavesurfer.js",
            "wavesurfer-multitrack",
            "react-player",
          ],
          pdf: [
            "pdfjs-dist",
            "@react-pdf-viewer/core",
            "@react-pdf-viewer/default-layout",
          ],
          markdown: [
            "react-markdown",
            "react-syntax-highlighter",
            "rehype-raw",
            "rehype-sanitize",
            "remark-gfm",
          ],
        },
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
