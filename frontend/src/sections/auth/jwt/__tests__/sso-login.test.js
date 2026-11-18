import { describe, expect, it } from "vitest";
import { getSsoRedirectUrl } from "../sso-redirect";

describe("SSO login response handling", () => {
  it("returns the IdP URL from the API response", () => {
    expect(
      getSsoRedirectUrl({ result: { url: "https://idp.example/sso" } }),
    ).toBe("https://idp.example/sso");
  });

  it("does not invent a redirect when the API did not return one", () => {
    expect(getSsoRedirectUrl({ message: "SSO unavailable" })).toBeNull();
  });
});
