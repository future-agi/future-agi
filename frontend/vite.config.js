import { sentryVitePlugin } from "@sentry/vite-plugin";
/* eslint-env node */
import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import checker from "vite-plugin-checker";

// ----------------------------------------------------------------------

const apiTarget = process.env.VITE_HOST_API || "http://localhost:8000";

export default defineConfig({
  base: "/",
  plugins: [react(), checker({
    overlay: false,
    eslint: {
      lintCommand: 'eslint "./src/**/*.{js,jsx,ts,tsx}"',
    },
  }), sentryVitePlugin({
    org: "future-agi",
    project: "frontend"
  })],
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
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash].[ext]',
        // Split vendor chunks to reduce memory usage
        manualChunks: {
          vendor: ['react', 'react-dom'],
          mui: ['@mui/material', '@mui/lab', '@mui/x-data-grid', '@mui/x-date-pickers'],
          utils: ['lodash', 'date-fns', 'numeral'],
          charts: ['apexcharts', 'react-apexcharts'],
          // recharts + victory only used on 2-3 niche pages — let Vite split them
          // into separate chunks so they don't load for everyone
          editor: ['quill', 'react-quill', 'quill-mention'],
          grid: ['ag-grid-enterprise', 'ag-grid-react'],
          threejs: ['three', 'scatter-gl'],
          flow: ['@xyflow/react', '@dagrejs/dagre'],
          sentry: ['@sentry/react'],
          media: ['swiper', 'wavesurfer.js', 'wavesurfer-multitrack', 'react-player'],
          pdf: ['pdfjs-dist', '@react-pdf-viewer/core', '@react-pdf-viewer/default-layout'],
          markdown: ['react-markdown', 'react-syntax-highlighter', 'rehype-raw', 'rehype-sanitize', 'remark-gfm']
        }
      },
      // Reduce memory usage during build
      maxParallelFileOps: 2
    },
    // Disable source maps in production to save memory unless needed for debugging
    sourcemap: false,
    // Optimize chunk size
    chunkSizeWarningLimit: 1000,
    // Enable minification
    minify: true,
    // Enable tree shaking
    target: 'esnext'
  },
  optimizeDeps: {
    include: ['apexcharts', 'react-apexcharts'],
  },
  server: {
    port: 3031,
    hmr: {
      overlay: false,
    },
    // Local Falcon chat uses backend WebSockets. Forward /ws through the Vite
    // dev server so frontend requests on :3031 reach the backend API target.
    proxy: {
      "/ws": {
        target: apiTarget,
        ws: true,
        changeOrigin: true,
      },
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
