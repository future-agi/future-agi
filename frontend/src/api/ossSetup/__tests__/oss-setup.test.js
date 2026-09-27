import { describe, it, expect, vi, beforeEach } from "vitest";

const h = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("axios", () => ({
  default: { create: () => ({ get: h.get }) },
}));

vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

vi.mock("src/utils/axios", () => ({
  endpoints: { ossSetup: { setupChecks: "/api/setup-checks/" } },
}));

import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";

import { fetchSetupChecks } from "../oss-setup";

const CONTRACT_FIELDS = Object.keys(
  OPENAPI_CONTRACT.definitions.SetupCheck.properties,
);

const SERVED = {
  id: "storage",
  label: "Object storage service",
  status: "failed",
  required: true,
  detail: "Dataset uploads, exports and media will fail",
  fix: "Start it: `docker compose up -d minio`.",
  docs_url:
    "https://github.com/future-agi/future-agi/blob/dev/INSTALLATION.md#pre-flight-says-object-storage-service-failed",
};

const respond = (checks) =>
  h.get.mockResolvedValue({
    data: { result: { status: "issues", mode: "live", checks } },
  });

describe("fetchSetupChecks", () => {
  beforeEach(() => h.get.mockReset());

  it("keeps every field the SetupCheck contract declares", async () => {
    respond([SERVED]);

    const { checks } = await fetchSetupChecks("live");

    expect(Object.keys(checks[0]).sort()).toEqual([...CONTRACT_FIELDS].sort());
  });

  it("passes the remedy and the docs link through untouched", async () => {
    respond([SERVED]);

    const { checks } = await fetchSetupChecks("live");

    expect(checks[0].fix).toBe(SERVED.fix);
    expect(checks[0].docs_url).toBe(SERVED.docs_url);
  });

  it("returns a blank remedy rather than undefined when the check passed", async () => {
    respond([{ ...SERVED, status: "passed", fix: "", docs_url: "" }]);

    const { checks } = await fetchSetupChecks("live");

    expect(checks[0].fix).toBe("");
    expect(checks[0].docs_url).toBe("");
  });
});
