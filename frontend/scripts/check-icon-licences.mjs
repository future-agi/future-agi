import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

// Presence is not licence clearance. In particular, an SPDX template is NOT
// the upstream copyright notice. Keep this gate red until acquisition is proven.
export function checkIconLicences(root) {
  const dir = path.join(root, "public/assets/licences/icons");
  const sets = JSON.parse(fs.readFileSync(path.join(dir, "sets.json"), "utf8"));
  const errors = [];
  for (const [prefix, info] of Object.entries(sets)) {
    const file = path.join(dir, `${prefix}.txt`);
    const text = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
    if (
      !text ||
      /<year>|<copyright holders>|LICENCE TEXT NOT ACQUIRED/i.test(text)
    ) {
      errors.push({
        prefix,
        problem: "Missing actual upstream copyright notice",
      });
    }
    if (/^(?:GPL-|CC-BY-SA)/.test(info.license?.spdx || "")) {
      errors.push({
        prefix,
        problem:
          "Copyleft artwork requires replacement or explicit redistribution review",
      });
    }
    if (!text.includes(info.source) || !text.includes(info.license?.spdx)) {
      errors.push({
        prefix,
        problem: "Set metadata does not match the shipped notice",
      });
    }
  }
  for (const prefix of [
    "gridicons",
    "dashicons",
    "entypo",
    "mdi-light",
    "uil",
  ]) {
    if (sets[prefix])
      errors.push({
        prefix,
        problem: "Superseded artwork must not re-enter the subset",
      });
  }
  return {
    passed: errors.length === 0,
    sets: Object.keys(sets).length,
    errors,
    scope:
      "Structural notice checks only; exact upstream version/provenance still requires review.",
  };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const result = checkIconLicences(path.resolve(process.argv[2] || "."));
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (!result.passed) process.exitCode = 1;
}
