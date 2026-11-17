import { describe, it, expect } from "vitest";

import { toolActionErrorMessage } from "../connectorTools";
import {
  CONNECTOR_AUTH_REQUIRED,
  CONNECTOR_AUTH_REQUIRED_MESSAGE,
} from "../connectorErrors";

// The connector API refuses a connector action (Re-authenticate, Test
// Connection, Discover Tools, a tool switch) when the credential saved for
// the connector can't be read here. The refusal carries the code, both as its
// code and as its message, and no words.
const BODY = {
  status: false,
  message: CONNECTOR_AUTH_REQUIRED,
  code: CONNECTOR_AUTH_REQUIRED,
};

describe("a connector action refused because its saved credential can't be read here", () => {
  it.each([
    // The shared client rejects with the response body spread flat.
    ["from the shared client", { ...BODY, statusCode: 409 }],
    [
      "as a raw response",
      {
        message: "Request failed with status code 409",
        response: { status: 409, data: BODY },
      },
    ],
    ["with the code as its only message", { message: CONNECTOR_AUTH_REQUIRED }],
  ])("says what to do in words, %s", (_shape, error) => {
    const text = toolActionErrorMessage(error, "Authentication failed.");

    expect(text).toBe(CONNECTOR_AUTH_REQUIRED_MESSAGE);
    expect(text).not.toContain(CONNECTOR_AUTH_REQUIRED);
  });

  it.each([
    [
      "the server's reason",
      { message: "Connector not found", code: "not_found", statusCode: 404 },
      "Connector not found",
    ],
    [
      "a raw response's detail",
      { response: { data: { detail: "Connection refused by the server" } } },
      "Connection refused by the server",
    ],
    ["the fallback when there is none", {}, "Authentication failed."],
  ])("keeps %s as it is written", (_label, error, shown) => {
    expect(toolActionErrorMessage(error, "Authentication failed.")).toBe(shown);
  });
});
