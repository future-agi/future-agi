import { describe, expect, it } from "vitest";

import { buildChatRequestSnapshot } from "./requestSnapshot";

describe("submitted chat request snapshot", () => {
  it("retains zero, sampling settings, stream and the public model ID", () => {
    const input = {
      model: "example-provider/example-model",
      messages: [{ role: "user", content: 'A "quoted"\nrequest' }],
      temperature: 0,
      max_tokens: 64,
      top_p: 0,
      stream: true,
    };
    expect(buildChatRequestSnapshot(input)).toEqual(input);
  });

  it("copies nested message values so later edits cannot change the submission", () => {
    const input = {
      model: "example-provider/example-model",
      messages: [{ role: "user", content: [{ type: "text", text: "Submitted" }] }],
    };
    const snapshot = buildChatRequestSnapshot(input);
    input.messages[0].content[0].text = "Edited";
    input.messages.push({ role: "user", content: "Another message" });
    expect(snapshot.messages).toEqual([
      { role: "user", content: [{ type: "text", text: "Submitted" }] },
    ]);
    expect(JSON.parse(JSON.stringify(snapshot))).toEqual(snapshot);
  });

  it("omits absent sampling values and console-only credentials/endpoints", () => {
    const snapshot = buildChatRequestSnapshot({
      model: "example-provider/example-model",
      messages: [],
      temperature: undefined,
      top_p: null,
      temporaryKey: "synthetic-console-key",
      endpoint: "/admin/test",
    });
    expect(snapshot).toEqual({
      model: "example-provider/example-model", messages: [], stream: false,
    });
  });
});
