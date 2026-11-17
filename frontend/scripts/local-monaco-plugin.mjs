import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const prefix = "assets/monaco/vs/";

// @monaco-editor/react does NOT guarantee the editor package is installed.
// Never silently fall back to its CDN. Acquire/lock monaco-editor on the build VM.
function localEditorRoot() {
  let manifest;
  try {
    manifest = require.resolve("monaco-editor/package.json");
  } catch {
    throw new Error(
      "Offline Monaco requires a cloud-installed, lockfile-pinned monaco-editor dependency; CDN fallback is forbidden.",
    );
  }
  return path.join(path.dirname(manifest), "min/vs");
}

export function localMonacoPlugin() {
  let command;
  return {
    name: "local-monaco-assets",
    configResolved(config) {
      command = config.command;
    },
    buildStart() {
      if (command !== "build") return;
      const root = localEditorRoot();
      const packageRoot = path.resolve(root, "../..");
      this.emitFile({
        type: "asset",
        fileName: "assets/licences/monaco-editor.txt",
        source: fs.readFileSync(path.join(packageRoot, "LICENSE")),
      });
      this.emitFile({
        type: "asset",
        fileName: "assets/licences/monaco-third-party.txt",
        source: fs.readFileSync(
          path.join(packageRoot, "ThirdPartyNotices.txt"),
        ),
      });
      this.emitFile({
        type: "asset",
        fileName: "assets/monaco/package.json",
        source: fs.readFileSync(path.join(packageRoot, "package.json")),
      });
      const emit = (dir) => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
          const file = path.join(dir, entry.name);
          if (entry.isDirectory()) emit(file);
          else if (entry.isFile())
            this.emitFile({
              type: "asset",
              fileName:
                prefix + path.relative(root, file).split(path.sep).join("/"),
              source: fs.readFileSync(file),
            });
        }
      };
      emit(root);
    },
    configureServer(server) {
      const root = localEditorRoot();
      server.middlewares.use((req, res, next) => {
        const pathname = new URL(req.url, "http://localhost").pathname;
        const base = server.config.base.replace(/\/$/, "");
        const route = `${base}/${prefix}`;
        if (!pathname.startsWith(route)) return next();
        const file = path.resolve(root, pathname.slice(route.length));
        if (
          !file.startsWith(`${root}${path.sep}`) ||
          !fs.existsSync(file) ||
          !fs.statSync(file).isFile()
        ) {
          res.statusCode = 404;
          res.end();
          return;
        }
        const contentTypes = {
          ".js": "text/javascript",
          ".css": "text/css",
          ".ttf": "font/ttf",
          ".svg": "image/svg+xml",
        };
        res.setHeader(
          "Content-Type",
          contentTypes[path.extname(file)] || "application/octet-stream",
        );
        fs.createReadStream(file).pipe(res);
      });
    },
  };
}
