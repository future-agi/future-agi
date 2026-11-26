import { describe, expect, it } from "vitest";
import {
  RESOURCE_FORBIDDEN,
  RESOURCE_NOT_FOUND,
  getResourceUnavailableKind,
} from "../resourceUnavailableKind";

// A refusal as the app's axios rejects it: the server's error envelope with
// its message under detail, message, error and result, plus the status.
const envelope = (statusCode, message, code = "invalid") => ({
  status: false,
  type: "api_error",
  code,
  detail: message,
  message,
  error: message,
  result: message,
  statusCode,
});

describe("getResourceUnavailableKind", () => {
  it("reads any 404 as not found", () => {
    expect(
      getResourceUnavailableKind(
        envelope(
          404,
          "The specified dataset does not exist or has been deleted.",
          "not_found",
        ),
      ),
    ).toBe(RESOURCE_NOT_FOUND);
    expect(
      getResourceUnavailableKind({ statusCode: 404, detail: "Not found." }),
    ).toBe(RESOURCE_NOT_FOUND);
  });

  it("reads the current 400 for a missing dataset as not found", () => {
    expect(
      getResourceUnavailableKind(
        envelope(
          400,
          "The specified dataset does not exist or has been deleted.",
        ),
      ),
    ).toBe(RESOURCE_NOT_FOUND);
  });

  it("reads the current 400 for a missing project as not found", () => {
    expect(
      getResourceUnavailableKind(envelope(400, "Project Not Found")),
    ).toBe(RESOURCE_NOT_FOUND);
  });

  it("reads a 400 with the not_found code as not found", () => {
    expect(
      getResourceUnavailableKind(envelope(400, "Gone", "not_found")),
    ).toBe(RESOURCE_NOT_FOUND);
  });

  it("leaves a 400 with any other message alone", () => {
    expect(
      getResourceUnavailableKind(envelope(400, "Invalid filters")),
    ).toBeNull();
  });

  it("reads a 403 as no access, except the app-wide 2FA block", () => {
    expect(
      getResourceUnavailableKind(
        envelope(403, "Permission denied", "permission_denied"),
      ),
    ).toBe(RESOURCE_FORBIDDEN);
    expect(
      getResourceUnavailableKind(
        envelope(403, "Two-factor required", "2fa_required"),
      ),
    ).toBeNull();
  });

  it("leaves server faults, network errors and empty values alone", () => {
    expect(
      getResourceUnavailableKind(envelope(500, "Something went wrong")),
    ).toBeNull();
    expect(
      getResourceUnavailableKind({
        message: "Network Error",
        transportCode: "ERR_NETWORK",
      }),
    ).toBeNull();
    expect(getResourceUnavailableKind(null)).toBeNull();
    expect(getResourceUnavailableKind(undefined)).toBeNull();
    expect(getResourceUnavailableKind("Project Not Found")).toBeNull();
  });

  it("reads a raw Axios error's status too", () => {
    expect(getResourceUnavailableKind({ response: { status: 404 } })).toBe(
      RESOURCE_NOT_FOUND,
    );
  });
});
