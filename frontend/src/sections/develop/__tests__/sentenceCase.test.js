import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// DL#48, DG#41: sentence case for every label this lane writes. Scans
// visible string literals in the R3 files for Title Case runs.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FILES = [
  "develop/DevelopView.jsx",
  "develop/datasetListColumns.jsx",
  "develop/DatasetListCells.jsx",
  "develop/ListSelectionBar.jsx",
  "develop/importOptions.js",
  "develop/AddDatasetDrawer/AddDatasetDrawer.jsx",
  "develop/AddDatasetDrawer/ManuallyCreateDataset.jsx",
  "develop/AddDatasetDrawer/UploadFileModal.jsx",
  "develop/AddDatasetDrawer/ImportPreviewPane.jsx",
  "develop/AddRowDrawer/AddRowDrawer.jsx",
  "develop-detail/DevelopBar/DatasetHeaderBar.jsx",
  "develop-detail/DevelopBar/ShareDatasetDialog.jsx",
  "develop-detail/DataTab/DataGrid/ToolbarControls.jsx",
  "develop-detail/DataTab/DataGrid/GoToRowPopover.jsx",
];
// Proper nouns and product names that keep their capitals.
const ALLOW =
  /Hugging Face|Future AGI|Falcon|Observe|Python|TypeScript|SDK|CSV|JSON|JSONL|API|URL|Esc|Enter|Undo|Advanced|Simple|ID|MB|KB/g;
const titleCase = (s) => {
  const words = s
    .replace(ALLOW, "x")
    .split(/\s+/)
    .filter((w) => /^[A-Za-z]/.test(w));
  if (words.length < 2) return false;
  return (
    words.slice(1).filter((w) => /^[A-Z][a-z]/.test(w)).length >= 1 &&
    words.slice(1).every((w) => /^[A-Z]/.test(w) || w.length <= 3)
  );
};

describe("sentence case in P1 labels", () => {
  it.each(FILES)("%s has no Title Case labels", (file) => {
    const src = readFileSync(join(ROOT, file), "utf8");
    const labels = [
      ...src.matchAll(
        /(?:label|title|placeholder|header|tooltip)[:=]\s*"([^"]{3,60})"/g,
      ),
      ...src.matchAll(/>\s*([A-Z][a-z]+(?: [A-Za-z]+)+)\s*</g),
    ].map((m) => m[1]);
    const bad = labels.filter(titleCase);
    expect(bad).toEqual([]);
  });
  it("finds the files", () => {
    FILES.forEach((f) => expect(statSync(join(ROOT, f)).isFile()).toBe(true));
    expect(readdirSync(join(ROOT, "develop")).length).toBeGreaterThan(3);
  });
});
