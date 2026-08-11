import { describe, expect, it } from "vitest";

import { buildConnectorSavePayload } from "./utils";

describe("buildConnectorSavePayload", () => {
  it("omits blank connector secrets on edit so hidden credentials are preserved", () => {
    expect(
      buildConnectorSavePayload(
        {
          name: "Linear",
          server_url: "https://mcp.example.com",
          auth_type: "api_key",
          auth_header_name: "X-API-Key",
          auth_header_value: "",
        },
        { preserveEmptySecret: true },
      ),
    ).toEqual({
      name: "Linear",
      server_url: "https://mcp.example.com",
      auth_type: "api_key",
      auth_header_name: "X-API-Key",
    });
  });

  it("keeps connector secrets on create", () => {
    expect(
      buildConnectorSavePayload(
        {
          name: "Linear",
          server_url: "https://mcp.example.com",
          auth_type: "api_key",
          auth_header_name: "X-API-Key",
          auth_header_value: "secret",
        },
        { preserveEmptySecret: false },
      ).auth_header_value,
    ).toBe("secret");
  });
});
