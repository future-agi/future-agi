import { describe, expect, it } from "vitest";
import { analyticsUrl, pageViewPath } from "../analytics-url";

const USER = "synthetic-user-3a90";
const CODE = "SYNTHETIC-LINK-CODE-c57e";

describe("analyticsUrl", () => {
  it.each([
    [
      `https://app.example.test/auth/jwt/verify/${USER}/${CODE}`,
      "https://app.example.test/auth/jwt/verify/:uuid/:token",
    ],
    [
      `https://app.example.test/auth/jwt/invitation/accept/${USER}/${CODE}?ref=mail`,
      "https://app.example.test/auth/jwt/invitation/accept/:uuid/:token?ref=mail",
    ],
    [
      `/auth/jwt/invitation/set-password/${USER}/${CODE}`,
      "/auth/jwt/invitation/set-password/:uuid/:token",
    ],
    [`/shared/${CODE}`, "/shared/:token"],
  ])("reports the link %s by its route pattern", (href, expected) => {
    expect(analyticsUrl(href)).toBe(expected);
  });

  it.each([
    "sso_token",
    "access_token",
    "refresh_token",
    "is_new_user",
    "saml_code",
    "oauth_code",
    "onboarding_token",
    "onboarding_gcp_token",
    "code",
    "state",
    "request_id",
    "code_challenge",
    "SAMLResponse",
    "SAMLRequest",
    "RelayState",
  ])("removes the %s query parameter and keeps the others", (name) => {
    expect(
      analyticsUrl(
        `https://app.example.test/dashboard/develop?tab=all&${name}=${CODE}&page=2`,
      ),
    ).toBe("https://app.example.test/dashboard/develop?tab=all&page=2");
    expect(analyticsUrl(`/dashboard?${name}=${CODE}`)).toBe("/dashboard");
  });

  it("removes a fragment that carries a sign-in parameter", () => {
    expect(
      analyticsUrl(`https://app.example.test/dashboard#sso_token=${CODE}`),
    ).toBe("https://app.example.test/dashboard");
    expect(analyticsUrl("https://app.example.test/docs#section-2")).toBe(
      "https://app.example.test/docs#section-2",
    );
  });

  it("reports a link carried in another parameter's value the same way", () => {
    const inner = encodeURIComponent(`/auth/jwt/verify/${USER}/${CODE}`);
    const result = analyticsUrl(`/auth/jwt/login?returnTo=${inner}`);

    expect(result).not.toContain(CODE);
    expect(new URLSearchParams(result.split("?")[1]).get("returnTo")).toBe(
      "/auth/jwt/verify/:uuid/:token",
    );
  });

  it("matches link paths written with percent-encoded letters", () => {
    expect(analyticsUrl(`https://app.example.test/%73hared/${CODE}`)).toBe(
      "https://app.example.test/shared/:token",
    );
    expect(analyticsUrl(`/auth/jwt/%76erify/${USER}/${CODE}`)).toBe(
      "/auth/jwt/verify/:uuid/:token",
    );
  });

  it("cleans a hash route's path and query", () => {
    expect(
      analyticsUrl(
        `https://app.example.test/#/dashboard?sso_token=${CODE}&tab=1`,
      ),
    ).toBe("https://app.example.test/#/dashboard?tab=1");
    expect(analyticsUrl(`https://app.example.test/#/shared/${CODE}`)).toBe(
      "https://app.example.test/#/shared/:token",
    );
  });

  it("cleans links nested in parameter values more than one level deep", () => {
    const inner = encodeURIComponent(`/shared/${CODE}`);
    const middle = encodeURIComponent(`/auth/jwt/login?returnTo=${inner}`);
    const sso = encodeURIComponent(`/dashboard?sso_token=${CODE}`);
    const middleSso = encodeURIComponent(`/auth/jwt/login?returnTo=${sso}`);

    expect(analyticsUrl(`/auth/jwt/login?returnTo=${middle}`)).not.toContain(
      CODE,
    );
    expect(
      analyticsUrl(`https://app.example.test/x?returnTo=${middleSso}`),
    ).not.toContain(CODE);
  });

  it.each([
    "https://app.example.test/dashboard/develop/dataset-1?tab=data&page=2",
    "https://docs.example.test/guide?step=3&q=a+b%20c",
    "/dashboard/develop",
    "/dashboard/develop?search=a%2Fb",
    "",
    "$direct",
  ])("returns the ordinary URL %s unchanged", (href) => {
    expect(analyticsUrl(href)).toBe(href);
  });

  it("returns values that are not strings unchanged", () => {
    expect(analyticsUrl(undefined)).toBeUndefined();
    expect(analyticsUrl(null)).toBeNull();
  });
});

describe("pageViewPath", () => {
  it("maps link paths to their route pattern and keeps other paths", () => {
    expect(pageViewPath(`/auth/jwt/verify/${USER}/${CODE}`)).toBe(
      "/auth/jwt/verify/:uuid/:token",
    );
    expect(pageViewPath("/dashboard/develop")).toBe("/dashboard/develop");
  });
});
