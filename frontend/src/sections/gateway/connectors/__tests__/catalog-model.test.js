import { describe, expect, it } from "vitest";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import mixed from "../__fixtures__/catalog-acme-connected.json";
import {
  APP_STATUS,
  AUTH_TYPES,
  CATEGORIES,
  appStatus,
  authOptions,
  authType,
  connectableApps,
  currentProvider,
  denialText,
  filterApps,
  isConnected,
  placementText,
  tierBadge,
  toolSummary,
  vendorStatusText,
} from "../catalog-model";

const app = (catalog, id) => catalog.results.find((entry) => entry.id === id);
const ids = (apps) => apps.map((entry) => entry.id);
const ALL = { view: "all", search: "", category: null, auth: null };

describe("categories", () => {
  it("are the manifest's five eeCategory values, in the page's order", () => {
    expect(CATEGORIES.map((c) => c.id)).toEqual([
      "productivity",
      "developer",
      "communication",
      "data",
      "research",
    ]);
  });

  it("filter on eeCategory or an open category facet, as the API does", () => {
    const apps = noneAdded.results;
    expect(ids(filterApps(apps, { ...ALL, category: "productivity" }))).toEqual(
      ["acme-docs", "fern-files"],
    );
    // iris-code is filed under developer and tagged code-hosting.
    expect(ids(filterApps(apps, { ...ALL, category: "code-hosting" }))).toEqual(
      ["iris-code"],
    );
  });
});

describe("search", () => {
  const apps = noneAdded.results;

  it("matches name, description, tags and vendor, ignoring case", () => {
    expect(ids(filterApps(apps, { ...ALL, search: "WIKI" }))).toEqual([
      "acme-docs",
    ]);
    expect(ids(filterApps(apps, { ...ALL, search: "iris labs" }))).toEqual([
      "iris-code",
    ]);
    expect(ids(filterApps(apps, { ...ALL, search: "cited sources" }))).toEqual([
      "cobalt-search",
    ]);
    expect(ids(filterApps(apps, { ...ALL, search: "bolt" }))).toEqual([
      "bolt-crm",
    ]);
  });

  it("trims the query and finds nothing for an unknown word", () => {
    expect(ids(filterApps(apps, { ...ALL, search: "  acme  " }))).toEqual([
      "acme-docs",
    ]);
    expect(filterApps(apps, { ...ALL, search: "zzz-nothing" })).toEqual([]);
  });
});

describe("Connected", () => {
  it("is an app with a server that names it by catalog_ref and is connected", () => {
    expect(isConnected(app(mixed, "acme-docs"))).toBe(true);
    // Added, but its key is not stored: listed under All, not Connected.
    expect(isConnected(app(mixed, "iris-code"))).toBe(false);
    expect(isConnected(app(mixed, "bolt-crm"))).toBe(false);
  });

  it("ignores a connected flag on a server with no catalog_ref", () => {
    const forged = {
      ...app(mixed, "bolt-crm"),
      connected: true,
      servers: [{ server_id: "bolt-crm", catalog_ref: "", connected: true }],
    };
    expect(isConnected(forged)).toBe(false);
  });

  it("the Connected view keeps only connected apps", () => {
    expect(
      ids(filterApps(mixed.results, { ...ALL, view: "connected" })),
    ).toEqual(["acme-docs"]);
    expect(
      filterApps(noneAdded.results, { ...ALL, view: "connected" }),
    ).toEqual([]);
  });
});

describe("status", () => {
  it("tells connected, added without its key, and not added apart", () => {
    expect(appStatus(app(mixed, "acme-docs"))).toBe(APP_STATUS.CONNECTED);
    expect(appStatus(app(mixed, "iris-code"))).toBe(APP_STATUS.NEEDS_KEY);
    expect(appStatus(app(mixed, "bolt-crm"))).toBe(APP_STATUS.AVAILABLE);
  });

  it("reports a server turned off as off, not as needing a key", () => {
    const iris = app(mixed, "iris-code");
    const off = {
      ...iris,
      servers: [{ ...iris.servers[0], enabled: false }],
    };
    expect(appStatus(off)).toBe(APP_STATUS.OFF);
  });
});

describe("providers and auth", () => {
  it("uses the added provider, else the default, else the first", () => {
    expect(currentProvider(app(mixed, "acme-docs")).id).toBe("acme-mcp");
    expect(currentProvider(app(noneAdded, "fern-files")).id).toBe("fern-mcp");
    const noDefault = {
      ...app(noneAdded, "fern-files"),
      default_provider: null,
    };
    expect(currentProvider(noDefault).id).toBe("fern-mcp");
  });

  it("names each auth type from the credential the catalog declares", () => {
    const provider = (id) => currentProvider(app(noneAdded, id));
    expect(authType(provider("acme-docs").credential)).toBe("api_key");
    expect(authType(provider("iris-code").credential)).toBe("bearer");
    expect(authType(provider("cobalt-search").credential)).toBe("none");
    expect(AUTH_TYPES.none).toBe("No key");
  });

  it("offers only auth filters some app has, and filters on them", () => {
    const apps = noneAdded.results;
    expect(authOptions(apps).map((o) => o.id)).toEqual([
      "api_key",
      "bearer",
      "none",
    ]);
    expect(ids(filterApps(apps, { ...ALL, auth: "none" }))).toEqual([
      "cobalt-search",
    ]);
    expect(ids(filterApps(apps, { ...ALL, auth: "bearer" }))).toEqual([
      "bolt-crm",
      "iris-code",
    ]);
  });

  it("says where each key goes", () => {
    const fern = app(noneAdded, "fern-files").providers;
    expect(placementText(fern[0].credential)).toBe(
      "Sent in the api_key query parameter",
    );
    expect(placementText(fern[1].credential)).toBe("Sent in the server URL");
    expect(
      placementText(currentProvider(app(noneAdded, "acme-docs")).credential),
    ).toBe("Sent in the X-Acme-Key header");
    expect(
      placementText(currentProvider(app(noneAdded, "iris-code")).credential),
    ).toBe("Sent as a bearer token");
    expect(
      placementText(
        currentProvider(app(noneAdded, "cobalt-search")).credential,
      ),
    ).toBe("No key needed");
  });
});

describe("badges come only from quality", () => {
  it("label the provider's quality tier", () => {
    expect(
      tierBadge(currentProvider(app(noneAdded, "cobalt-search"))),
    ).toMatchObject({ label: "Verified" });
    expect(
      tierBadge(currentProvider(app(noneAdded, "acme-docs"))),
    ).toMatchObject({ label: "Listed" });
    expect(tierBadge({ tier: "optimized" })).toMatchObject({
      label: "Optimized",
    });
  });

  it("show nothing for a tier the quality enum does not have", () => {
    expect(tierBadge({ tier: "gold" })).toBeNull();
    expect(tierBadge({ tier: "" })).toBeNull();
    expect(tierBadge(null)).toBeNull();
  });

  it("ignore every other field, such as the vendor's own status", () => {
    const provider = currentProvider(app(noneAdded, "acme-docs"));
    expect(
      tierBadge({
        ...provider,
        vendor_status: "verified",
        publisher: "verified",
      }),
    ).toMatchObject({ label: "Listed" });
  });
});

describe("vendor status", () => {
  it("is a plain fact for every label but GA", () => {
    expect(vendorStatusText({ vendor_status: "ga" })).toBeNull();
    expect(vendorStatusText({ vendor_status: "preview" })).toBe("Preview");
    expect(vendorStatusText({ vendor_status: "beta" })).toBe("Beta");
    expect(vendorStatusText({ vendor_status: "unknown" })).toBe(
      "Not stated by the vendor",
    );
    expect(vendorStatusText({})).toBeNull();
    expect(vendorStatusText(null)).toBeNull();
  });
});

describe("only apps an org can connect today", () => {
  const signIn = {
    ...app(noneAdded, "acme-docs").providers[0],
    id: "acme-oauth",
    credential: {
      ...app(noneAdded, "acme-docs").providers[0].credential,
      mode: "per_user",
      kind: "oauth2",
    },
  };

  it("keeps API key, token and no-key providers", () => {
    expect(ids(connectableApps(noneAdded.results))).toEqual(
      ids(noneAdded.results),
    );
  });

  it("drops a sign-in provider and an app that has nothing else", () => {
    const oauthOnly = {
      ...app(noneAdded, "bolt-crm"),
      id: "oauth-only",
      providers: [signIn],
      servers: [],
    };
    const both = {
      ...app(noneAdded, "acme-docs"),
      providers: [...app(noneAdded, "acme-docs").providers, signIn],
    };
    const listed = connectableApps([oauthOnly, both]);
    expect(ids(listed)).toEqual(["acme-docs"]);
    expect(listed[0].providers.map((p) => p.id)).toEqual(["acme-mcp"]);
  });

  it("keeps an app the org already added, so it can be disconnected", () => {
    const added = {
      ...app(mixed, "iris-code"),
      providers: [signIn],
    };
    expect(
      ids(connectableApps([added], { upstreamOAuth: true, k2Apps: true })),
    ).toEqual(["iris-code"]);
    expect(
      currentProvider(
        connectableApps([added], { upstreamOAuth: true, k2Apps: true })[0],
      ),
    ).toBeNull();
  });
});

describe("tools", () => {
  it("counts the tools the gateway lets through", () => {
    expect(toolSummary(currentProvider(app(noneAdded, "acme-docs")))).toEqual({
      allowed: 2,
      total: 7,
    });
  });

  it("explains each reason the gateway blocks a tool", () => {
    for (const reason of [
      "posture_deny",
      "destructive_tool",
      "write_tool",
      "generic_tool",
      "approval_not_enforced",
    ]) {
      expect(denialText(reason)).toMatch(/^Blocked/);
    }
    expect(denialText("something_new")).toBe("Blocked by the gateway.");
    expect(denialText(null)).toBeNull();
  });
});
