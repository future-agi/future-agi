#!/usr/bin/env node

import { readdir, writeFile, mkdir } from "node:fs/promises";
import { join, relative, dirname, basename, extname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const PUBLIC = join(ROOT, "public");
const OUTPUT_DIR = join(ROOT, "src", "components", "icon-gallery");

// Directories to scan for local SVGs
const ICON_DIRS = ["assets/icons", "icons"];

// ─── Local SVG Manifest ─────────────────────────────────────────────

async function collectSvgFiles() {
  const icons = [];

  for (const iconDir of ICON_DIRS) {
    const absDir = join(PUBLIC, iconDir);
    let entries;
    try {
      entries = await readdir(absDir, { recursive: true, withFileTypes: true });
    } catch {
      continue; // directory doesn't exist, skip
    }

    for (const entry of entries) {
      if (!entry.isFile() || extname(entry.name) !== ".svg") continue;

      const entryPath = join(entry.parentPath ?? entry.path, entry.name);
      const relFromPublic = "/" + relative(PUBLIC, entryPath);
      const relFromIconDir = relative(absDir, entryPath);
      const parts = relFromIconDir.split("/");
      const category = parts.length > 1 ? parts[0] : "root";
      const fileName = basename(entry.name, ".svg");

      // Extract keywords from filename
      const keywords = fileName
        .replace(/^ic_/, "")
        .split(/[_\-]/)
        .filter((k) => k.length > 0)
        .map((k) => k.toLowerCase());

      icons.push({
        filePath: relFromPublic,
        fileName,
        category,
        keywords,
        sourceDir: iconDir,
      });
    }
  }

  return icons;
}

function flagDuplicates(icons) {
  const byName = new Map();
  for (const icon of icons) {
    const list = byName.get(icon.fileName) || [];
    list.push(icon);
    byName.set(icon.fileName, list);
  }

  const duplicateNames = new Set();
  for (const [name, list] of byName) {
    if (list.length > 1) {
      duplicateNames.add(name);
    }
  }

  return icons.map((icon) => ({
    ...icon,
    isDuplicate: duplicateNames.has(icon.fileName),
    duplicateCount: duplicateNames.has(icon.fileName)
      ? byName.get(icon.fileName).length
      : 0,
    duplicatePaths: duplicateNames.has(icon.fileName)
      ? byName
          .get(icon.fileName)
          .map((d) => ({ filePath: d.filePath, category: d.category }))
      : [],
  }));
}

// ─── Main ────────────────────────────────────────────────────────────

async function main() {
  console.log("Generating icon manifests...\n");

  // Ensure output directory exists
  await mkdir(OUTPUT_DIR, { recursive: true });

  // 1. Local SVG manifest
  const rawIcons = await collectSvgFiles();
  const icons = flagDuplicates(rawIcons);

  const categories = [...new Set(icons.map((i) => i.category))].sort();
  const duplicates = icons.filter((i) => i.isDuplicate);

  const localManifest = {
    generatedAt: new Date().toISOString(),
    total: icons.length,
    categories,
    duplicateCount: duplicates.length,
    icons,
  };

  await writeFile(
    join(OUTPUT_DIR, "icon-manifest.json"),
    JSON.stringify(localManifest, null, 2) + "\n"
  );

  console.log(`  Local SVGs: ${icons.length} icons in ${categories.length} categories`);
  if (duplicates.length > 0) {
    const uniqueDupNames = [...new Set(duplicates.map((d) => d.fileName))];
    console.log(`  Duplicates: ${duplicates.length} files (${uniqueDupNames.length} unique names)`);
  }

  console.log("\nManifest written to src/components/icon-gallery/");
}

main().catch((err) => {
  console.error("Error generating icon manifests:", err);
  process.exit(1);
});
