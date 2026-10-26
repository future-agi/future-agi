// Buttons on these pages take their size, type and neutral colours from the theme, not from sx overrides.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const sourceFiles = (dir) =>
  fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory())
      return entry.name === "__tests__" ? [] : sourceFiles(full);
    return entry.name.endsWith(".jsx") ? [full] : [];
  });

/* Each <Button …> opening tag, props included (props may hold nested JSX such as startIcon). */
const buttonTags = (source) => {
  const tags = [];
  const re = /<Button\b/g;
  let match;
  while ((match = re.exec(source))) {
    let depth = 0;
    let i = match.index + 1;
    for (; i < source.length; i += 1) {
      const ch = source[i];
      if (ch === "{") depth += 1;
      else if (ch === "}") depth -= 1;
      else if (ch === ">" && depth === 0) break;
    }
    tags.push({
      tag: source.slice(match.index, i + 1),
      line: source.slice(0, match.index).split("\n").length,
    });
  }
  return tags;
};

const offenders = (test) =>
  sourceFiles(root).flatMap((file) =>
    buttonTags(fs.readFileSync(file, "utf8"))
      .filter(({ tag }) => test(tag))
      .map(({ line }) => `${path.relative(root, file)}:${line}`),
  );

describe("integration page buttons", () => {
  it("don't override the theme's font size, weight or height", () => {
    expect(
      offenders((tag) => /typography:|fontWeight:|\bheight:/.test(tag)),
    ).toEqual([]);
  });

  it('use color="inherit" for neutral outlined buttons instead of hand-set border colours', () => {
    expect(offenders((tag) => /borderColor:/.test(tag))).toEqual([]);
  });

  it("use IconButton for icon-only buttons", () => {
    expect(offenders((tag) => /minWidth: 0/.test(tag))).toEqual([]);
  });
});
