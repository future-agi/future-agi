import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), put: vi.fn() },
}));
// Built as apiPath builds it; the route is not in the generated contract yet.
vi.mock("src/api/contracts/api-surface", () => ({
  apiPath: (template, params) =>
    template.replace(/\{([^}]+)\}/g, (_, key) =>
      encodeURIComponent(String(params[key])),
    ),
}));

const { default: axios } = await import("src/utils/axios");
const {
  chooseStartingRecord,
  getStartingRecords,
  startingRecordsKey,
  useChooseStartingRecord,
} = await import("../startingRecords");

const PATH =
  "/simulate/api/harness-environments/env-1/scenarios/book-ride/starting-records/";
const RECORDS = {
  scenario_key: "book-ride",
  origin: "seed",
  editable: true,
  rows_unavailable: null,
  records: [],
};

const wrapperFor = (client) => {
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

describe("starting records api", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads one scenario's starting records", async () => {
    axios.get.mockResolvedValue({ data: RECORDS });

    expect(await getStartingRecords("env-1", "book-ride")).toEqual(RECORDS);
    expect(axios.get).toHaveBeenCalledWith(PATH);
  });

  it("sends the chosen row's key as text, with the fact's path and table", async () => {
    axios.put.mockResolvedValue({ data: RECORDS });

    await chooseStartingRecord("env-1", "book-ride", {
      label: "identity.rider_id",
      path: ["identity", "rider_id"],
      table: "users",
      key: 42,
    });

    expect(axios.put).toHaveBeenCalledWith(PATH, {
      path: ["identity", "rider_id"],
      table: "users",
      key: "42",
    });
  });

  it("seeds the read with the server's answer after a choice", async () => {
    const answer = { ...RECORDS, records: [{ label: "identity.rider_id" }] };
    axios.put.mockResolvedValue({ data: answer });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    const { result } = renderHook(
      () => useChooseStartingRecord("env-1", "book-ride"),
      { wrapper: wrapperFor(client) },
    );

    await act(() =>
      result.current.mutateAsync({
        path: ["identity", "rider_id"],
        table: "users",
        key: "rdr_priya",
      }),
    );

    await waitFor(() =>
      expect(
        client.getQueryData(startingRecordsKey("env-1", "book-ride")),
      ).toEqual(answer),
    );
  });
});
