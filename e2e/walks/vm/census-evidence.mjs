// Land only value-free receipts and known assertion categories, never raw logs.
import fs from "node:fs";
import path from "node:path";
import { assertValueFreeReceipt } from "../../lib/walk/receipt-values.mjs";
const root = process.argv[2];
const out = path.join(root, "out/walk-evidence");
fs.mkdirSync(out, { recursive: true });
let receipts = 0;
function visit(dir) {
  if (!fs.existsSync(dir)) return;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) visit(file);
    else if (entry.name === "walk-receipt.json") {
      const text = fs.readFileSync(file, "utf8");
      assertValueFreeReceipt(text);
      fs.writeFileSync(path.join(out, `receipt-${++receipts}.json`), text);
    }
  }
}
visit(path.join(root, "walk-artifacts"));
visit(path.join(root, "red-artifacts"));
const log = fs
  .readFileSync(path.join(root, "census.log"), "utf8")
  .replace(/\u001b\[[0-9;]*m/g, "");
const failures = log
  .split("\n")
  .map((line) => line.trim())
  .filter(
    (line) =>
      /^Error: CAT2_OFFERED: (api|gateway): [a-zA-Z0-9_./~:, -]+$/.test(line) ||
      /^Error: CAT2_CREDITS_NOT_REFUSED: [a-zA-Z0-9_./~:, =-]+$/.test(line) ||
      /^Error: W00_RESULT_[A-Z_]+$/.test(line),
  );
const text = JSON.stringify({ receipts, failures });
assertValueFreeReceipt(text);
fs.writeFileSync(path.join(out, "assertions.json"), text + "\n");
console.log(`landed ${receipts} value-free walk receipts`);
