import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios, { endpoints } from "src/utils/axios";

vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));

const metricId = "44444444-4444-4444-8444-444444444444";
const metric = { project_id: "33333333-3333-4333-8333-333333333333" };
let adapter;
let oldAdapter;
beforeEach(() => {
  oldAdapter = axios.defaults.adapter;
  // Keep the real request contract interceptor: rejection must precede transport.
  adapter = vi.fn(() => {
    throw new Error("Unexpected transport request");
  });
  axios.defaults.adapter = adapter;
});
afterEach(() => {
  axios.defaults.adapter = oldAdapter;
  vi.restoreAllMocks();
});

describe("metrics repair through the real Axios contract interceptor", () => {
  it("the same interceptor rejects a monitor without organization before the adapter", async () => {
    await expect(
      axios.post(endpoints.project.createMonitor, {
        name: "Invalid",
        project: metric.project_id,
        metric_type: "derived_metric",
        metric: metricId,
        threshold_operator: "less_than",
      }),
    ).rejects.toMatchObject({
      message: expect.stringContaining("organization"),
    });
    expect(adapter).not.toHaveBeenCalled();
  });
});
