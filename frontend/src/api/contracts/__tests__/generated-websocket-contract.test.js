import { describe, expect, it } from "vitest";

import { CallWebsocketCreateBody } from "src/generated/api-contracts/api.zod";

import { validateContractedRequestConfig } from "../openapi-contract";

const message = {
  alert_title: "Synthetic limit reached",
  streaming_status: "running",
  progress: 0,
  optional: null,
  nested: { values: ["chunk", 1, true, null] },
};

const runtimeAccepts = (data) =>
  validateContractedRequestConfig({
    url: "/call-websocket/",
    method: "post",
    data,
  }).ok;

for (const [name, accepts] of [
  ["generated Zod", (data) => CallWebsocketCreateBody.safeParse(data).success],
  ["OpenAPI runtime", runtimeAccepts],
]) {
  describe(`${name} websocket relay request`, () => {
    it.each([{}, { uuid: null }, { send_to_uuid: false, uuid: null }])(
      "accepts object broadcasts with omitted or null UUID (%j)",
      (extra) => expect(accepts({ message, ...extra })).toBe(true),
    );

    it.each(["", "generate_", "improve_"])(
      "accepts targeted streaming with prefix %s",
      (prefix) => {
        expect(
          accepts({
            message,
            send_to_uuid: true,
            uuid: `${prefix}00000000-0000-4000-8000-000000000003`,
          }),
        ).toBe(true);
      },
    );

    it.each([
      {},
      { uuid: null },
      { uuid: "" },
      { uuid: "not-a-uuid" },
      { uuid: "generate_" },
      { uuid: "improve_not-a-uuid" },
      { uuid: "unknown_00000000-0000-4000-8000-000000000003" },
      { uuid: "generate_00000000-0000-4000-8000-000000000003\n" },
    ])("rejects targeted delivery without a valid UUID (%j)", (extra) =>
      expect(accepts({ message, send_to_uuid: true, ...extra })).toBe(false),
    );

    it.each([null, [], [1], "text", 42, true])(
      "rejects a non-object message (%j)",
      (value) => expect(accepts({ message: value })).toBe(false),
    );
  });
}

it("generated relay parser preserves heterogeneous message values", () => {
  expect(
    CallWebsocketCreateBody.parse({ message, uuid: null }).message,
  ).toEqual(message);
});
