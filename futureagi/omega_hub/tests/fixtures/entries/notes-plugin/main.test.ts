// The keyless test `omega hub check` runs: the manifest matches the server, and notes persist to the
// file the plugin config names.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readManifest, useNotes } from "./main.ts";

test("the manifest declares the tools the server serves", async () => {
  const { manifest, tools } = await useNotes(["probe"]);
  assert.deepEqual(tools, manifest.contributes.mcpServers[0]!.tools);
  assert.equal(readManifest().config[0]?.name, "NOTES_FILE");
});

test("notes are saved to the configured file and read back", async () => {
  const { saved, listed, onDisk } = await useNotes(["one", "two"]);
  assert.deepEqual(saved, ["saved note 1", "saved note 2"]);
  assert.deepEqual(listed, ["one", "two"]);
  assert.deepEqual(onDisk, ["one", "two"]);
});
