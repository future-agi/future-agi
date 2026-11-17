import { afterEach, describe, expect, it, vi } from "vitest";
import { publicApiJourneys } from "../../../../scripts/api-journeys/journeys/public-api.mjs";
import {
  historicalBoundaryPath,
  HISTORICAL_BOUNDARY_PATHS,
} from "../../../../scripts/api-journeys/lib/historical-boundary-paths.mjs";
import { API_SURFACE_PATHS } from "../api-surface.generated.js";
import { API_CONTRACT_EXCEPTIONS } from "../api-contract-exceptions.js";
import * as apiSurface from "../api-surface.js";

const journey = publicApiJourneys.find(({ id }) => id === "PUBLIC-AUTH-004");
const ee = () =>
  publicApiJourneys.find(({ id }) => id === "PUBLIC-AUTH-004-EE");
const apiBase = "http://localhost:8999";
const refused = () =>
  new Response(
    JSON.stringify({ detail: "Authentication credentials were not provided." }),
    {
      status: 401,
      headers: { "Content-Type": "application/json" },
    },
  );
const absent = () => new Response("Not Found", { status: 404 });
const responseFor = (url) =>
  url.includes("/usage/ee/licenses/") ? absent() : refused();
afterEach(() => vi.unstubAllGlobals());

describe("personal key anonymous boundaries", () => {
  it("bounds absence history to the removed licensing routes without advertising them", () => {
    expect(HISTORICAL_BOUNDARY_PATHS).toEqual({
      "/usage/ee/licenses/": ["GET", "POST"],
      "/usage/ee/licenses/{grant_id}/revoke/": ["POST"],
    });
    for (const template of Object.keys(HISTORICAL_BOUNDARY_PATHS)) {
      expect(API_SURFACE_PATHS).not.toHaveProperty(template);
      expect(API_CONTRACT_EXCEPTIONS).not.toHaveProperty(template);
    }
    expect(() => historicalBoundaryPath("/accounts/keys/")).toThrow(
      "Not a retained historical boundary",
    );
    expect(() =>
      historicalBoundaryPath("/usage/ee/licenses/{grant_id}/revoke/"),
    ).toThrow("Missing historical boundary parameter");
    expect(
      historicalBoundaryPath("/usage/ee/licenses/{grant_id}/revoke/", {
        grant_id: "a/b",
      }),
    ).toBe("/usage/ee/licenses/a%2Fb/revoke/");
  });

  it("executes exactly 32 auth refusals and three explicit absence checks, including the valid issuer body", async () => {
    const fetch = vi.fn(async (url) => responseFor(url));
    vi.stubGlobal("fetch", fetch);
    const evidence = [];
    await journey.run({ apiBase, evidence });
    expect(fetch).toHaveBeenCalledTimes(35);
    expect(
      evidence.filter(({ kind }) => kind === "authentication"),
    ).toHaveLength(32);
    expect(
      evidence
        .filter(({ kind }) => kind === "absence")
        .map(({ method, status }) => [method, status]),
    ).toEqual([
      ["GET", 404],
      ["POST", 404],
      ["POST", 404],
    ]);
    const create = fetch.mock.calls.find(([, options]) =>
      options.body?.includes("anonymous-personal-key-must-not-exist"),
    );
    expect(create?.[0]).toBe(`${apiBase}/accounts/key/generate_secret_key/`);
    expect(JSON.parse(create[1].body)).toEqual({
      key_name: "anonymous-personal-key-must-not-exist",
      workspace_id: null,
    });
    expect(evidence.at(-1).label).toBe("gateway API key sync");
    expect(
      fetch.mock.calls.every(([, { headers }]) => !headers.Authorization),
    ).toBe(true);
  });

  it.each([200, 401, 403, 500])(
    "rejects a removed route returning %s instead of treating it as auth",
    async (status) => {
      const fetch = vi.fn(async (url) =>
        url.includes("/usage/ee/licenses/")
          ? new Response(
              JSON.stringify({
                detail: "Authentication credentials were not provided.",
              }),
              { status, headers: { "Content-Type": "application/json" } },
            )
          : refused(),
      );
      vi.stubGlobal("fetch", fetch);
      const evidence = [];
      await expect(journey.run({ apiBase, evidence })).rejects.toThrow(
        "expected absent route 404",
      );
      expect(fetch).toHaveBeenCalledTimes(35);
      expect(evidence.at(-1).label).toBe("gateway API key sync");
    },
  );

  it("rejects a missing active route and still evaluates subsequent guards", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) =>
        url.endsWith("/accounts/key/get_secret_keys/")
          ? absent()
          : responseFor(url),
      ),
    );
    const evidence = [];
    await expect(journey.run({ apiBase, evidence })).rejects.toThrow(
      "developer secret key list expected anonymous auth guard 401/403, saw 404",
    );
    expect(evidence).toHaveLength(35);
    expect(evidence.at(-1).label).toBe("gateway API key sync");
  });

  it("rejects successful valid-body issuance and still evaluates all historical absences", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, options) =>
        options.body?.includes("anonymous-personal-key-must-not-exist")
          ? new Response(JSON.stringify({ status: true, result: {} }), {
              status: 200,
            })
          : responseFor(url),
      ),
    );
    const evidence = [];
    await expect(journey.run({ apiBase, evidence })).rejects.toThrow(
      "personal key issuance with a valid body expected anonymous auth guard 401/403, saw 200",
    );
    expect(evidence.filter(({ kind }) => kind === "absence")).toHaveLength(3);
    expect(evidence.at(-1).label).toBe("gateway API key sync");
  });

  it("fails closed when a removed route is advertised in either product registry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => responseFor(url)),
    );
    const template = "/usage/ee/licenses/";
    for (const name of ["isContractedApiPath", "isApiContractExceptionPath"]) {
      const original = apiSurface[name];
      const advertised = vi
        .spyOn(apiSurface, name)
        .mockImplementation((path) => path === template || original(path));
      try {
        await expect(journey.run({ apiBase, evidence: [] })).rejects.toThrow(
          "removed route is advertised",
        );
        expect(advertised).toHaveBeenCalledWith(template);
      } finally {
        advertised.mockRestore();
      }
    }
  });

  it("registers all eight unchanged billing boundaries for integrated staging", async () => {
    expect(ee()).toBeDefined();
    const fetch = vi.fn(async () => refused());
    vi.stubGlobal("fetch", fetch);
    const evidence = [];
    await ee().run({ apiBase, evidence });
    expect(fetch).toHaveBeenCalledTimes(8);
    expect(
      evidence.map(({ label, method, path }) => [label, method, path]),
    ).toEqual([
      ["payment methods", "GET", "/usage/v2/payment-methods/"],
      [
        "payment setup intent",
        "POST",
        "/usage/v2/payment-methods/setup-intent/",
      ],
      [
        "payment method delete",
        "DELETE",
        "/usage/v2/payment-methods/00000000-0000-0000-0000-000000000000/",
      ],
      [
        "payment method default",
        "POST",
        "/usage/v2/payment-methods/00000000-0000-0000-0000-000000000000/default/",
      ],
      [
        "billing portal session",
        "POST",
        "/usage/create-billing-portal-session/",
      ],
      ["checkout session", "POST", "/usage/create-checkout-session/"],
      ["legacy billing details", "GET", "/usage/get-billing-details/"],
      ["plans and add-ons", "GET", "/usage/v2/plans-and-addons/"],
    ]);
    expect(
      fetch.mock.calls.every(([, { headers }]) => !headers.Authorization),
    ).toBe(true);
  });

  it.each([404, 200])(
    "does not accept %s at any EE boundary",
    async (status) => {
      expect(ee()).toBeDefined();
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response("{}", { status })),
      );
      const evidence = [];
      await expect(ee().run({ apiBase, evidence })).rejects.toThrow(
        "expected anonymous auth guard 401/403",
      );
      expect(evidence).toHaveLength(8);
    },
  );
});
