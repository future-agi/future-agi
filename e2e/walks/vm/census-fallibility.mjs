// Cloud-only one-sided live W00 negative control. Changes ONLY the API read
// input, not the expected ID set or validator. Restore exact bytes afterwards.
import fs from "node:fs";
import path from "node:path";
if (process.platform !== "linux") throw new Error("cloud only");
const [root, mode] = process.argv.slice(2);
const file = path.join(root, "e2e/flows/walks/default-off-census.spec.ts");
const backup = path.join(root, "w00-fallibility.original");
if (mode === "perturb") {
  const source = fs.readFileSync(file, "utf8");
  fs.writeFileSync(backup, source, { flag: "wx" });
  const anchor = "const gatewayCatalog = await admin.catalog();";
  if (source.split(anchor).length !== 2)
    throw new Error("fallibility anchor changed");
  fs.writeFileSync(
    file,
    source.replace(
      anchor,
      `${anchor}\n      apiCatalog.models.push({ id: 'google/lyria-3-pro-preview', state: 'available', billed_input_per_mtok: 1 });`,
    ),
  );
} else if (mode === "restore") {
  fs.writeFileSync(file, fs.readFileSync(backup));
} else throw new Error("unknown mode");
