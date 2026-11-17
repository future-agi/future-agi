import assert from "node:assert/strict";
import { test } from "node:test";
import { callServer } from "./main.ts";

test("the server lists its tool and answers a call", async () => {
  const { tools, text } = await callServer();
  assert.deepEqual(tools, ["greet"]);
  assert.equal(text, "Hello, Ada.");
});
