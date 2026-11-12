import axios from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiPath,
  getContractedApiMethods,
  isApiContractExceptionPath,
  isContractedApiPath,
  uncontractedApiPath,
} from "src/api/contracts/api-surface";
import { endpoints } from "src/utils/axios";
import { LAUNCH_HOLD_ROUTES } from "../launchHoldRoutes";

// Independent inventory: deriving this from the live list would miss additions.
const RESULTS_GETS = [
  {
    builder: "list",
    template: "/model-hub/datasets/{dataset_id}/runs/",
    url: "/model-hub/datasets/d1/runs/",
  },
  {
    builder: "results",
    template: "/model-hub/datasets/{dataset_id}/results/",
    url: "/model-hub/datasets/d1/results/",
  },
  {
    builder: "analysis",
    template: "/model-hub/datasets/{dataset_id}/results/analysis/",
    url: "/model-hub/datasets/d1/results/analysis/",
  },
];

let guard;
let setLaunchOverrideForTest;
let previousLog;

beforeEach(async () => {
  previousLog = window.__dsxLaunchAbsent;
  window.__dsxLaunchAbsent = [];
  // Each test exercises a real installation, not the module's previous one.
  vi.resetModules();
  ({ setLaunchOverrideForTest } = await import("../launchSwitch"));
  setLaunchOverrideForTest({ launch: true });
  guard = await import("../absentPathGuard");
});

afterEach(() => {
  setLaunchOverrideForTest?.(null);
  if (previousLog === undefined) delete window.__dsxLaunchAbsent;
  else window.__dsxLaunchAbsent = previousLog;
  vi.resetModules();
});

const makeClient = () => {
  const adapter = vi.fn(async (config) => ({
    data: {},
    status: 200,
    statusText: "OK",
    headers: {},
    config,
  }));
  return {
    adapter,
    client: axios.create({ baseURL: "https://api.example.test", adapter }),
  };
};

describe("Results launch holds", () => {
  it("closes and freezes the list to exactly the three Results routes", () => {
    expect(LAUNCH_HOLD_ROUTES).toEqual(
      RESULTS_GETS.map(({ template }) => template),
    );
    expect(Object.isFrozen(LAUNCH_HOLD_ROUTES)).toBe(true);
  });

  it.each(RESULTS_GETS)(
    "keeps GET $template contracted and explicitly held, not an exception",
    ({ template, url }) => {
      expect(isContractedApiPath(template)).toBe(true);
      expect(getContractedApiMethods(template)).toEqual(["get"]);
      expect(isApiContractExceptionPath(template)).toBe(false);
      expect(LAUNCH_HOLD_ROUTES).toContain(template);
      expect(apiPath(template, { dataset_id: "d1" })).toBe(url);
      expect(() => uncontractedApiPath(template, { dataset_id: "d1" })).toThrow(
        "API contract exception path is not registered",
      );
      expect(guard.absentTemplates()).toContain(template);
    },
  );

  it("retains explicit holds without exceptions and despite backing claims", () => {
    const templates = RESULTS_GETS.map(({ template }) => template);
    const absent = guard.absentTemplates({}, new Set(templates));
    for (const template of templates) expect(absent).toContain(template);
    expect(new Set(absent).size).toBe(absent.length);
  });

  it.each(RESULTS_GETS)(
    "blocks GET $template before the adapter with the existing 404 and log",
    async ({ template, url }) => {
      const { client, adapter } = makeClient();
      expect(guard.installAbsentPathGuard(client)).toBe(true);
      expect(guard.installAbsentPathGuard(client)).toBe(false);
      const urls = [
        url,
        `${url.slice(0, -1)}?page=1`,
        `https://api.example.test${url}?page=1`,
      ];
      for (const requestUrl of urls) {
        expect(guard.absentTemplateOf(requestUrl, client.defaults.baseURL)).toBe(
          template,
        );
        const error = await client.get(requestUrl).catch((rejection) => rejection);
        expect(error).toBeInstanceOf(Error);
        expect(error.message).toBe(`Not available yet: ${template}`);
        expect(error.name).toBe("LaunchAbsentPath");
        expect(error.code).toBe("ERR_LAUNCH_ABSENT");
        expect(error.launchAbsent).toBe(template);
        expect(error.config).toMatchObject({ url: requestUrl, method: "get" });
        expect(error.response).toEqual({
          status: 404,
          statusText: "Not Found",
          data: { detail: "Not available yet", launch_absent: template },
          headers: {},
          config: error.config,
        });
        expect(error.response.config).toBe(error.config);
      }
      expect(adapter).not.toHaveBeenCalled();
      expect(window.__dsxLaunchAbsent).toEqual(
        urls.map(() => ({ template, method: "GET", at: expect.any(Number) })),
      );
    },
  );

  // Use the application's exports, not apiPath or hand-written URLs: removing
  // an exception must not make a builder throw before the guard can refuse it.
  // The isolated real Axios client exposes the guard's error before the app's
  // response interceptor normalizes it; neither the builders nor guard are mocked.
  it.each(RESULTS_GETS)(
    "routes the real datasetRuns.$builder GET through the launch 404 and log",
    async ({ builder, template, url }) => {
      const { client, adapter } = makeClient();
      expect(guard.installAbsentPathGuard(client)).toBe(true);
      const requestUrl = endpoints.develop.datasetRuns[builder]("ds /1");
      expect(requestUrl).toBe(url.replace("/d1/", "/ds%20%2F1/"));
      expect(guard.absentTemplateOf(requestUrl, client.defaults.baseURL)).toBe(
        template,
      );
      const error = await client.get(requestUrl).catch((rejection) => rejection);
      expect(error).toBeInstanceOf(Error);
      expect(error.message).toBe(`Not available yet: ${template}`);
      expect(error.name).toBe("LaunchAbsentPath");
      expect(error.code).toBe("ERR_LAUNCH_ABSENT");
      expect(error.launchAbsent).toBe(template);
      expect(error.config).toMatchObject({ url: requestUrl, method: "get" });
      expect(error.response).toEqual({
        status: 404,
        statusText: "Not Found",
        data: { detail: "Not available yet", launch_absent: template },
        headers: {},
        config: error.config,
      });
      expect(error.response.config).toBe(error.config);
      expect(adapter).not.toHaveBeenCalled();
      expect(window.__dsxLaunchAbsent).toEqual([
        { template, method: "GET", at: expect.any(Number) },
      ]);
    },
  );

  it.each(RESULTS_GETS)(
    "passes the real datasetRuns.$builder GET to the adapter in prototype",
    async ({ builder, template, url }) => {
      setLaunchOverrideForTest({ launch: false });
      const { client, adapter } = makeClient();
      expect(guard.installAbsentPathGuard(client)).toBe(false);
      const requestUrl = endpoints.develop.datasetRuns[builder]("ds /1");
      expect(requestUrl).toBe(url.replace("/d1/", "/ds%20%2F1/"));
      expect(guard.absentTemplateOf(requestUrl, client.defaults.baseURL)).toBe(
        template,
      );
      await expect(client.get(requestUrl)).resolves.toMatchObject({
        status: 200,
        config: { url: requestUrl, method: "get" },
      });
      expect(adapter).toHaveBeenCalledTimes(1);
      expect(adapter).toHaveBeenCalledWith(
        expect.objectContaining({ url: requestUrl, method: "get" }),
      );
      expect(window.__dsxLaunchAbsent).toEqual([]);
    },
  );

  it("still passes unrelated contracted routes to the adapter in launch", async () => {
    const { client, adapter } = makeClient();
    expect(guard.installAbsentPathGuard(client)).toBe(true);
    await expect(client.get("/model-hub/develops/get-datasets/")).resolves.toMatchObject({
      status: 200,
    });
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(window.__dsxLaunchAbsent).toEqual([]);
  });

  it("does not install the guard or refuse the three GETs outside launch", async () => {
    setLaunchOverrideForTest({ launch: false });
    const { client, adapter } = makeClient();
    expect(guard.installAbsentPathGuard(client)).toBe(false);
    for (const { url } of RESULTS_GETS) {
      await expect(client.get(url)).resolves.toMatchObject({ status: 200 });
    }
    expect(adapter).toHaveBeenCalledTimes(RESULTS_GETS.length);
    expect(window.__dsxLaunchAbsent).toEqual([]);
  });
});
