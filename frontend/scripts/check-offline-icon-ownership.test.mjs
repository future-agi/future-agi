import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { assertOfflineIconOwnership } from "./check-offline-icon-ownership.mjs";

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "offline-ownership-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "src"));
  const files = ["src/picker.jsx", "src/picker.stories.jsx"];
  for (const file of files)
    fs.writeFileSync(path.join(root, file), 'const icon = "mdi:star";');
  return {
    root,
    inventory: {
      backend: { names: ["mdi:star"] },
      sources: files.map((file) => ({ file, icon: "mdi:star" })),
      unresolved: [],
    },
    consumers: { "mdi:star": [files[0]] },
    owner: (_root, _file, text) => {
      assert.equal(text, 'const icon = "mdi:star";');
      return ["mdi:star", "mdi:plus"];
    },
  };
}

const check = ({ root, inventory, consumers, owner }) =>
  assertOfflineIconOwnership(root, inventory, consumers, owner);

test("accepts live ownership and allows a consumer to own additional icons", (t) => {
  assert.equal(check(fixture(t)), 2);
});

const controls = [
  [
    "unmapped backend default",
    ({ inventory }) => inventory.backend.names.push("mdi:unmapped"),
    /explicit owner/,
  ],
  [
    "extra backend mapping",
    ({ consumers }) => {
      consumers["mdi:extra"] = ["src/picker.jsx"];
    },
    /explicit owner/,
  ],
  [
    "missing backend consumer file",
    ({ consumers }) => consumers["mdi:star"].push("src/missing.jsx"),
    /Missing owner/,
  ],
  [
    "missing source file",
    ({ inventory }) =>
      inventory.sources.push({ file: "src/missing.jsx", icon: "mdi:star" }),
    /ENOENT/,
  ],
  [
    "missing owned glyph",
    (data) => {
      data.owner = () => [];
    },
    /Missing owned icon/,
  ],
  [
    "second glyph omitted from the same source",
    ({ inventory }) =>
      inventory.sources.push({ file: "src/picker.jsx", icon: "mdi:second" }),
    /Missing owned icon/,
  ],
  [
    "story consumers omitted",
    ({ inventory }) => {
      inventory.sources = inventory.sources.filter(
        ({ file }) => !file.includes(".stories."),
      );
    },
    /Story consumers/,
  ],
  [
    "unresolved computed icon",
    ({ inventory }) => inventory.unresolved.push({ file: "src/picker.jsx" }),
    /Unresolved/,
  ],
];

for (const [name, mutate, message] of controls) {
  test(`rejects ${name}`, (t) => {
    const data = fixture(t);
    mutate(data);
    assert.throws(() => check(data), message);
  });
}
