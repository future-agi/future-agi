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

// The guard reads the exported show-unreleased switch, which the override
// cannot reach. It starts as shipped (on: the guard is not installed); the
// cases that pin the launch 404 turn it off first, and the as-shipped case
// below keeps it on.
const unreleased = vi.hoisted(() => ({ shown: true }));
vi.mock("../launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

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
  unreleased.shown = true;
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

describe("accepted Results routes", () => {
  it("releases exactly the accepted Results GET holds and freezes the list", () => {
    expect(LAUNCH_HOLD_ROUTES).toEqual([]);
    expect(Object.isFrozen(LAUNCH_HOLD_ROUTES)).toBe(true);
  });

  it.each(RESULTS_GETS)(
    "keeps GET $template contracted and released, not an exception",
    ({ template, url }) => {
      expect(isContractedApiPath(template)).toBe(true);
      expect(getContractedApiMethods(template)).toEqual(["get"]);
      expect(isApiContractExceptionPath(template)).toBe(false);
      expect(LAUNCH_HOLD_ROUTES).not.toContain(template);
      expect(apiPath(template, { dataset_id: "d1" })).toBe(url);
      expect(() => uncontractedApiPath(template, { dataset_id: "d1" })).toThrow(
        "API contract exception path is not registered",
      );
      expect(guard.absentTemplates()).not.toContain(template);
    },
  );

  it("retains an explicit hold despite backing claims", async () => {
    const template = RESULTS_GETS[0].template;
    vi.doMock("../launchHoldRoutes", () => ({
      LAUNCH_HOLD_ROUTES: Object.freeze([template]),
    }));
    try {
      vi.resetModules();
      const isolated = await import("../absentPathGuard");
      expect(isolated.absentTemplates({}, new Set([template]))).toContain(
        template,
      );
      const absent = isolated.absentTemplates({}, new Set([template]));
      expect(new Set(absent).size).toBe(absent.length);
    } finally {
      vi.doUnmock("../launchHoldRoutes");
    }
  });

  it.each(RESULTS_GETS)(
    "passes GET $template in launch with no refusal log",
    async ({ url }) => {
      unreleased.shown = false;
      const { client, adapter } = makeClient();
      expect(guard.installAbsentPathGuard(client)).toBe(true);
      expect(guard.installAbsentPathGuard(client)).toBe(false);
      const urls = [
        url,
        `${url.slice(0, -1)}?page=1`,
        `https://api.example.test${url}?page=1`,
      ];
      for (const requestUrl of urls) {
        expect(
          guard.absentTemplateOf(requestUrl, client.defaults.baseURL),
        ).toBeNull();
        await expect(client.get(requestUrl)).resolves.toMatchObject({
          status: 200,
          config: { url: requestUrl, method: "get" },
        });
      }
      expect(adapter.mock.calls.map(([config]) => config.url)).toEqual(urls);
      expect(window.__dsxLaunchAbsent).toEqual([]);
    },
  );

  it.each(RESULTS_GETS)(
    "passes the real datasetRuns.$builder GET in launch",
    async ({ builder, url }) => {
      unreleased.shown = false;
      const { client, adapter } = makeClient();
      expect(guard.installAbsentPathGuard(client)).toBe(true);
      const requestUrl = endpoints.develop.datasetRuns[builder]("ds /1");
      expect(requestUrl).toBe(url.replace("/d1/", "/ds%20%2F1/"));
      expect(
        guard.absentTemplateOf(requestUrl, client.defaults.baseURL),
      ).toBeNull();
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

  it.each(["d1", "new"])(
    "admits the accepted copy POST for %s",
    async (target) => {
      unreleased.shown = false;
      const { client, adapter } = makeClient();
      expect(guard.installAbsentPathGuard(client)).toBe(true);
      const url = `/model-hub/datasets/${target}/rows/from-results/`;
      expect(guard.absentTemplateOf(url)).toBeNull();
      await expect(client.post(url, {})).resolves.toMatchObject({
        status: 200,
        config: { url, method: "post" },
      });
      expect(adapter).toHaveBeenCalledTimes(1);
      expect(window.__dsxLaunchAbsent).toEqual([]);
    },
  );

  it.each([
    ["get", "/model-hub/datasets/{dataset_id}/derived-metrics/"],
    ["get", "/model-hub/datasets/{dataset_id}/classification/"],
    ["put", "/model-hub/datasets/{dataset_id}/baseline/"],
    ["delete", "/model-hub/datasets/{dataset_id}/baseline/"],
    ["post", "/model-hub/experiments/v2/{experiment_id}/runs/"],
  ])(
    "refuses unsupported %s %s before the adapter",
    async (method, template) => {
      unreleased.shown = false;
      const { client, adapter } = makeClient();
      expect(guard.installAbsentPathGuard(client)).toBe(true);
      const url = template.replace(/\{[^}]+\}/g, "fixture-id");
      expect(guard.absentTemplateOf(url)).toBe(template);
      const error = await client
        .request({ method, url })
        .catch((value) => value);
      expect(error).toBeInstanceOf(Error);
      expect(error.name).toBe("LaunchAbsentPath");
      expect(error.message).toBe(`Not available yet: ${template}`);
      expect(error.code).toBe("ERR_LAUNCH_ABSENT");
      expect(error.launchAbsent).toBe(template);
      expect(error.config).toMatchObject({ method, url });
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
        { template, method: method.toUpperCase(), at: expect.any(Number) },
      ]);
    },
  );

  it.each(RESULTS_GETS)(
    "passes the real datasetRuns.$builder GET to the adapter in prototype",
    async ({ builder, url }) => {
      setLaunchOverrideForTest({ launch: false });
      const { client, adapter } = makeClient();
      expect(guard.installAbsentPathGuard(client)).toBe(false);
      const requestUrl = endpoints.develop.datasetRuns[builder]("ds /1");
      expect(requestUrl).toBe(url.replace("/d1/", "/ds%20%2F1/"));
      expect(
        guard.absentTemplateOf(requestUrl, client.defaults.baseURL),
      ).toBeNull();
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
    unreleased.shown = false;
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

// The release as shipped: the switch stays on, so a launch build installs no
// guard and the three Results GETs reach the adapter. These read the guard
// and the adapter only; they check no words.
describe("Results launch holds as shipped (unreleased parts shown)", () => {
  it("installs no guard in launch and passes the three GETs to the adapter", async () => {
    // The mocked flag stands for the shipped switch, so pin that switch here.
    const actual = await vi.importActual("../launchSwitch");
    expect(actual.SHOW_UNRELEASED_PARTS).toBe(true);
    unreleased.shown = true;
    const { client, adapter } = makeClient();
    expect(guard.installAbsentPathGuard(client)).toBe(false);
    for (const { url } of RESULTS_GETS) {
      await expect(client.get(url)).resolves.toMatchObject({ status: 200 });
    }
    expect(adapter).toHaveBeenCalledTimes(RESULTS_GETS.length);
    expect(window.__dsxLaunchAbsent).toEqual([]);
  });
});
