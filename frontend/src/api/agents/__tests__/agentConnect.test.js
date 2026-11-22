import { describe, expect, it } from "vitest";
import { SCAN_POLL_MS, scanOpen, scanPollInterval } from "../agentConnect";

const query = (status, data) => ({ state: { status, data } });

describe("scanPollInterval", () => {
  it.each(["pending", "running"])("keeps polling a %s scan", (status) => {
    expect(scanPollInterval(query("success", { status }))).toBe(SCAN_POLL_MS);
  });

  it("polls until the first answer arrives", () => {
    expect(scanOpen(undefined)).toBe(true);
    expect(scanPollInterval(query("pending", undefined))).toBe(SCAN_POLL_MS);
  });

  it.each(["completed", "failed", "error"])("stops on a %s scan", (status) => {
    expect(scanPollInterval(query("success", { status }))).toBe(false);
  });

  it("stops when the request itself errored", () => {
    expect(scanPollInterval(query("error", undefined))).toBe(false);
    expect(scanPollInterval(query("error", { status: "running" }))).toBe(false);
  });
});
