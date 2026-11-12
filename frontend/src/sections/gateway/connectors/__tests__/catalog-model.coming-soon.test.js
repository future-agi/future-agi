import { describe, expect, it } from "vitest";

import noneAdded from "../__fixtures__/catalog-none-added.json";
import {
  APP_STATUS,
  appStatus,
  authOptions,
  connectableApps,
  currentProvider,
  filterApps,
} from "../catalog-model";

const held = {
  ...noneAdded.results[0],
  id: "microsoft-excel",
  name: "Microsoft Excel",
  group: "microsoft",
  status: "coming_soon",
  default_provider: null,
  providers: [],
  servers: [],
};
const capabilities = { ownAppStorable: true, upstreamOAuth: true, k2Apps: true };

describe("coming-soon catalog cards", () => {
  it("retains a held empty app but still omits ordinary unavailable apps", () => {
    const unavailable = { ...held, id: "other-app", status: undefined };
    expect(connectableApps([held, unavailable], capabilities)).toEqual([held]);
    expect(APP_STATUS.COMING_SOON).toBe("coming_soon");
    expect(appStatus(held)).toBe("coming_soon");
  });

  it("drops stale addable providers without mutating the API payload", () => {
    const stale = { ...held, providers: noneAdded.results[0].providers };
    const [app] = connectableApps([stale], capabilities);
    expect(app.providers).toEqual([]);
    expect(stale.providers).toEqual(noneAdded.results[0].providers);
    expect(currentProvider(stale)).toBeNull();
  });

  it.each([false, true])("keeps existing servers with connected=%s for cleanup", (connected) => {
    const server = {
      server_id: "existing-excel",
      catalog_ref: "microsoft-excel/futureagi-rest@1.0.0",
      provider: "futureagi-rest",
      credential_mode: "per_user",
      connected,
      enabled: true,
    };
    const [app] = connectableApps([{ ...held, servers: [server] }], capabilities);
    expect(app.servers).toEqual([server]);
    expect(appStatus(app)).toBe("coming_soon");
  });

  it.each([false, true])("retains gate-refused provider/reason with already-added=%s", (added) => {
    const provider = {
      ...noneAdded.results[0].providers[0],
      id: "futureagi-rest",
      credential: { mode: "per_user", kind: "oauth" },
      connect: { served: false, state: "unavailable", reason: "microsoft_coming_soon" },
    };
    const payload = {
      ...held,
      status: undefined,
      default_provider: provider.id,
      providers: [provider],
      servers: added ? [{
        server_id: "existing-excel", provider: provider.id,
        credential_mode: "shared", connected: false, enabled: true,
        catalog_ref: "microsoft-excel/futureagi-rest@1.0.0",
      }] : [],
    };
    const [app] = connectableApps([payload], capabilities);
    expect(app).toBeDefined();
    expect(app.status).toBe("coming_soon");
    expect(app.default_provider).toBeNull();
    expect(app.providers).toEqual([provider]);
    expect(app.servers).toEqual(payload.servers);
    expect(currentProvider(app)).toBeNull();
    expect(appStatus(app)).toBe("coming_soon");
    expect(payload.status).toBeUndefined();
    expect(authOptions([app])).toEqual([]);
    expect(connectableApps([payload], { ...capabilities, upstreamOAuth: false })).toEqual([]);
    expect(connectableApps([payload], { ...capabilities, k2Apps: false })).toEqual([]);
  });

  it("does not assign No key auth to an app that cannot be connected", () => {
    expect(authOptions([held])).toEqual([]);
    expect(filterApps([held], { view: "all", auth: "none" })).toEqual([]);
    expect(filterApps([held], { view: "all", search: "Excel" })).toEqual([held]);
  });
});
