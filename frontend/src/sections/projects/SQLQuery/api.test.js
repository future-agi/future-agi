import { afterEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { errorMessage, sqlApi } from "./api";
import { describeEstimate } from "./estimateText";

vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
afterEach(() => vi.restoreAllMocks());

describe("SQL shared HTTP boundary", () => {
  it.each([
    [
      400,
      "Unknown dataset: x.",
      "This query will be rejected: Unknown dataset: x.",
    ],
    [
      429,
      "Too many requests.",
      "Estimate paused: too many estimates. Run is not affected.",
    ],
    [503, "Service unavailable.", "Estimate unavailable. Run is not affected."],
  ])(
    "describes a normalized %s estimate error",
    async (status, detail, text) => {
      const rejected = axios.interceptors.response.handlers.find(
        (handler) => handler.rejected,
      ).rejected;
      const config = { url: "/tracer/sql-query/estimate/", method: "post" };
      const error = await rejected({
        config,
        response: { status, config, data: { detail } },
      }).catch((value) => value);
      expect(error).toMatchObject({ statusCode: status, detail });
      expect(error.response).toBeUndefined();
      expect(describeEstimate({ status: "error", error }).text).toBe(text);
      expect(errorMessage(error)).toBe(detail);
    },
  );

  it("keeps transport failures actionable without a response", async () => {
    const rejected = axios.interceptors.response.handlers.find(
      (handler) => handler.rejected,
    ).rejected;
    const error = await rejected(new Error("Network Error")).catch(
      (value) => value,
    );
    expect(errorMessage(error)).toBe("Network Error");
    expect(describeEstimate({ status: "error", error }).text).toBe(
      "Estimate unavailable. Run is not affected.",
    );
  });

  it("downloads the server CSV unchanged, including numeric precision and formula escaping", async () => {
    const csv =
      "amount,label\r\n-9007199254740993,'-1+cmd\r\n-1.25e-8,'=SUM(1)\r\n";
    const blob = new Blob([csv], { type: "text/csv" });
    vi.spyOn(axios, "get").mockResolvedValue({ data: blob });
    expect(await sqlApi.download("project", "execution", "csv")).toBe(blob);
    expect(axios.get).toHaveBeenCalledWith(
      "/tracer/sql-query/execution/download/",
      {
        params: { project_id: "project", file_format: "csv" },
        responseType: "blob",
      },
    );
  });
});
