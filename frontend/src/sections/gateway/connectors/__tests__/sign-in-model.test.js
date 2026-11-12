import { describe, expect, it } from "vitest";

import {
  APP_STATUS,
  appStatus,
  authLabel,
  authOptions,
  connectableApps,
  filterApps,
  placementText,
} from "../catalog-model";
import { connectTarget, CONNECT_PREFIX } from "../connect-flow";
import {
  SLATE,
  catalogWith,
  slateApp,
  slateProvider,
  slateServer,
} from "../__fixtures__/sign-in";

const ids = (apps) => apps.map((app) => app.id);
const API = "https://api.example.test";
const UPSTREAM_ON = { upstreamOAuth: true };

// These are the explicit ON cases; subfeature-flags.test.js pins default OFF.
// Even with upstream OAuth enabled, a sign-in provider is shown only where
// the API serves its sign-in end to end (connect.served on its card).
describe("a sign-in provider is listed only when the API says its sign-in is served", () => {
  it("keeps a sign-in provider whose card says its sign-in is served", () => {
    const listed = connectableApps(
      catalogWith(slateApp()).results,
      UPSTREAM_ON,
    );
    expect(ids(listed)).toContain(SLATE);
    const slate = listed.find((app) => app.id === SLATE);
    expect(slate.providers.map((provider) => provider.id)).toEqual([
      "slate-oauth",
    ]);
  });

  it("leaves out a sign-in provider whose card does not say so, whatever else it carries", () => {
    const shapes = [
      undefined,
      null,
      true,
      "served",
      [],
      {},
      { served: "true" },
      { served: 1 },
      { served: false },
    ];
    for (const connect of shapes) {
      const provider = slateProvider({ served: false });
      if (connect !== undefined) provider.connect = connect;
      const listed = connectableApps(
        [slateApp({ providers: [provider] })],
        UPSTREAM_ON,
      );
      expect(ids(listed), JSON.stringify(connect)).toEqual([]);
    }
  });

  it("never lists a provider whose credential is neither a key, nothing, nor a served sign-in", () => {
    const provider = slateProvider();
    provider.credential = { ...provider.credential, kind: "mtls" };
    expect(
      ids(connectableApps([slateApp({ providers: [provider] })], UPSTREAM_ON)),
    ).toEqual([]);
  });
});

describe("a listed sign-in app", () => {
  it("is available until added, then not connected until this user's account is in", () => {
    const [available] = connectableApps([slateApp()], UPSTREAM_ON);
    expect(appStatus(available)).toBe(APP_STATUS.AVAILABLE);
    const [added] = connectableApps(
      [slateApp({ servers: [slateServer()] })],
      UPSTREAM_ON,
    );
    expect(appStatus(added)).toBe(APP_STATUS.NOT_CONNECTED);
    const [mine] = connectableApps(
      [slateApp({ servers: [slateServer({ connected: true })] })],
      UPSTREAM_ON,
    );
    expect(appStatus(mine)).toBe(APP_STATUS.CONNECTED);
  });

  it("names its account as the catalog does, else as a sign-in, and says whose account it is", () => {
    const provider = slateProvider();
    expect(authLabel(provider.credential)).toBe("Slate account");
    expect(authLabel({ ...provider.credential, label: "" })).toBe("Sign-in");
    expect(placementText(provider.credential)).toBe(
      "Each person signs in with their own account",
    );
    expect(placementText({ ...provider.credential, mode: "shared" })).toBe(
      "One account for the organization, signed in by an Owner or Admin",
    );
  });

  it("can be filtered on as a sign-in", () => {
    const apps = connectableApps(catalogWith(slateApp()).results, UPSTREAM_ON);
    expect(authOptions(apps).map((option) => option.id)).toContain("oauth");
    expect(ids(filterApps(apps, { view: "all", auth: "oauth" }))).toEqual([
      SLATE,
    ]);
  });
});

// The page builds no sign-in URL of its own. A URL from the API is followed
// only when it resolves to the API's own origin and to a path under the
// connect routes; anything else is treated as absent.
describe("the page follows only the API's own sign-in URLs", () => {
  const own = [
    `${CONNECT_PREFIX}slate-mail/start/?binding=abc`,
    `${API}${CONNECT_PREFIX}slate-mail/start/`,
    `${CONNECT_PREFIX}slate-mail/`,
  ];
  const notOwn = [
    "https://other.example.test/agentcc/mcp/connect/slate-mail/start/",
    "http://api.example.test/agentcc/mcp/connect/slate-mail/start/",
    "/agentcc/mcp/credentials/",
    "/agentcc/mcp/connect",
    `${CONNECT_PREFIX}../credentials/`,
    `${CONNECT_PREFIX}slate-mail/%2e%2e/%2e%2e/credentials/`,
    "//other.example.test/agentcc/mcp/connect/x/",
    "https://user@api.example.test/agentcc/mcp/connect/x/",
    "agentcc/mcp/connect/x/",
    "",
    null,
    undefined,
    42,
    {},
  ];

  it("resolves the API's own sign-in URLs against the API", () => {
    for (const url of own) {
      const target = connectTarget(url, API);
      expect(target, String(url)).not.toBeNull();
      const resolved = new URL(target);
      expect(resolved.origin).toBe(API);
      expect(resolved.pathname.startsWith(CONNECT_PREFIX)).toBe(true);
    }
  });

  it("treats every other URL as absent", () => {
    for (const url of notOwn) {
      expect(connectTarget(url, API), JSON.stringify(url)).toBeNull();
    }
  });
});
