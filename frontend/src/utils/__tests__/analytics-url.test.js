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

  it.each([
    "/saml2_auth/github/callback/",
    "/saml2_auth/microsoft/callback/",
    "/saml2_auth/auth/callback/",
    "/integrations/slack/callback/",
    "/falcon-ai/mcp-connectors/2b0c5e1e-0000-4000-8000-000000000001/oauth/callback/",
    "/mcp/authorize",
  ])("removes code and state on the sign-in step %s", (path) => {
    expect(
      analyticsUrl(
        `https://app.example.test${path}?code=${CODE}&state=${CODE}&lang=en`,
      ),
    ).toBe(`https://app.example.test${path}?lang=en`);
  });

  it("removes the c parameter on the SAML completion step only", () => {
    expect(
      analyticsUrl(`https://api.example.test/saml2_auth/complete/?c=${CODE}`),
    ).toBe("https://api.example.test/saml2_auth/complete/");
    expect(analyticsUrl("/dashboard/charts?c=3")).toBe("/dashboard/charts?c=3");
  });

  it("removes request_id, code and state from the authorization page address", () => {
    expect(
      analyticsUrl(
        `https://app.example.test/mcp/authorize?request_id=${CODE}&client_id=synthetic-client&state=${CODE}`,
      ),
    ).toBe("https://app.example.test/mcp/authorize?client_id=synthetic-client");
    expect(analyticsUrl(`/mcp/authorize/?request_id=${CODE}`)).toBe(
      "/mcp/authorize/",
    );
  });

  it.each([
    "https://app.example.test/dashboard/gateway/logs?request_id=req-synthetic-01&page=2",
    "/dashboard/gateway/logs?request_id=req-synthetic-01",
    "https://app.example.test/dashboard/develop?code=python&state=open",
    "https://app.example.test/dashboard/settings?access=read&refresh=daily",
  ])("sends the ordinary page address %s unchanged", (href) => {
    expect(analyticsUrl(href)).toBe(href);
  });

  it("removes access and refresh from the support sign-in page address", () => {
    expect(
      analyticsUrl(
        `https://app.example.test/sos?access=${CODE}&refresh=${CODE}`,
      ),
    ).toBe("https://app.example.test/sos");
    expect(analyticsUrl(`/SOS/?refresh=${CODE}&access=${CODE}&x=1`)).toBe(
      "/SOS/?x=1",
    );
  });

  it("cleans a hashbang route and a query inside a plain fragment", () => {
    expect(analyticsUrl(`https://app.example.test/#!/shared/${CODE}`)).toBe(
      "https://app.example.test/#!/shared/:token",
    );
    expect(
      analyticsUrl(`https://app.example.test/dashboard#tab?sso_token=${CODE}`),
    ).toBe("https://app.example.test/dashboard#tab");
    expect(
      analyticsUrl(
        `https://app.example.test/dashboard#tab?sso_token=${CODE}&view=grid`,
      ),
    ).toBe("https://app.example.test/dashboard#tab?view=grid");
  });

  it("removes a parameter fragment whose later value holds a question mark", () => {
    expect(
      analyticsUrl(
        `https://app.example.test/dashboard#sso_token=${CODE}&next=/dashboard?tab=1`,
      ),
    ).toBe("https://app.example.test/dashboard");
  });

  it.each([
    "https://docs.example.test/reference/callback?code=python&state=open&c=3",
    "/dashboard/settings/callback?code=python&state=open",
  ])("keeps code and state on the other callback page %s", (href) => {
    expect(analyticsUrl(href)).toBe(href);
  });

  it("cleans a link that was percent-encoded twice inside a parameter", () => {
    const twice = encodeURIComponent(
      encodeURIComponent(`/shared/${CODE}?sso_token=${CODE}`),
    );
    const result = analyticsUrl(`/auth/jwt/login?returnTo=${twice}`);

    expect(result).not.toContain(CODE);
    expect(decodeURIComponent(decodeURIComponent(result))).toContain(
      "/shared/:token",
    );
  });

  it("drops a nested link value deeper than the cleaning limit", () => {
    let nested = `/shared/${CODE}`;
    for (let level = 0; level < 8; level += 1) {
      nested = `/auth/jwt/login?returnTo=${encodeURIComponent(nested)}`;
    }

    expect(analyticsUrl(nested)).not.toContain(CODE);
    expect(analyticsUrl(`https://app.example.test${nested}`)).not.toContain(
      CODE,
    );
  });

  it.each([
    "shared/platform",
    "auth/jwt/verify/a/b",
    "Synthetic Org ?sso_token=1",
    "notes#sso_token=x",
  ])("leaves the text %s that is not a URL unchanged", (text) => {
    expect(analyticsUrl(text)).toBe(text);
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
