import { describe, expect, it } from "vitest";

import { readAdditionalRefusal } from "../managedRefusalRows";

// What this release does not serve says so in words that promise no later
// date: "in this release", never "yet". The codes are unchanged.
describe("managed refusal titles for what this release does not serve", () => {
  it.each([
    [
      400,
      "routing_field_not_yet_supported",
      "Routing field is not available in this release",
    ],
    [
      400,
      "managed_openrouter_streaming_not_yet",
      "OpenRouter streaming with credits is not available in this release",
    ],
  ])("%s %s reads %s", (status, code, title) => {
    expect(readAdditionalRefusal(status, code, {}).title).toBe(title);
  });
});
