import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

// Same directories the generation script scans (scripts/generate-icon-manifest.mjs)
const ICON_DIRS = ["public/assets/icons", "public/icons"];

export default function iconManifestPlugin() {
  const root = process.cwd();
  const scriptPath = resolve(root, "scripts/generate-icon-manifest.mjs");

  function generate() {
    return new Promise((res, rej) => {
      execFile("node", [scriptPath], (err) => (err ? rej(err) : res()));
    });
  }

  return {
    name: "icon-manifest",
    apply: "serve",
    configureServer(server) {
      const { logger } = server.config;
      const dirsToWatch = ICON_DIRS.map((d) => resolve(root, d)).filter((d) =>
        existsSync(d),
      );

      if (dirsToWatch.length === 0) return;

      const isIconFile = (filePath) =>
        filePath.endsWith(".svg") &&
        dirsToWatch.some((dir) => filePath.startsWith(dir));

      let pending = null;
      const onChange = (filePath) => {
        if (!isIconFile(filePath)) return;
        clearTimeout(pending);
        pending = setTimeout(async () => {
          try {
            logger.info("[icon-manifest] SVG change detected, regenerating...");
            await generate();
            const send = server.hot?.send ?? server.ws?.send;
            send?.call(server.hot ?? server.ws, { type: "full-reload" });
          } catch (err) {
            logger.error(`[icon-manifest] Failed to regenerate: ${err.message}`);
          }
        }, 500);
      };

      server.watcher.add(dirsToWatch);
      server.watcher.on("add", onChange);
      server.watcher.on("unlink", onChange);
      server.watcher.on("change", onChange);
    },
  };
}
