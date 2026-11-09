import { expect, it, vi } from "vitest";
import { getDataSource } from "../DevelopDataV2";

it.each([400, 403, 404, 500])(
  "surfaces a table read refusal (%s) to page recovery rather than only params.fail",
  async (statusCode) => {
    const error = { statusCode, message: "unavailable fixture" };
    const client = {
      getQueryState: vi.fn(),
      fetchQuery: vi.fn().mockRejectedValue(error),
    };
    const report = vi.fn();
    const source = getDataSource(
      client,
      "dataset-a",
      vi.fn(),
      vi.fn(),
      vi.fn(),
      { current: null },
      false,
      report,
    );
    const params = { request: { startRow: 0 }, fail: vi.fn() };
    await source.getRows(params);
    expect(report).toHaveBeenCalledWith({ datasetId: "dataset-a", error });
    expect(params.fail).toHaveBeenCalledOnce();
  },
);

it("ignores late failures after a dataset datasource is destroyed", async () => {
  let fail;
  const client = {
    getQueryState: vi.fn(),
    fetchQuery: () =>
      new Promise((_, reject) => {
        fail = reject;
      }),
  };
  const report = vi.fn();
  const source = getDataSource(
    client,
    "previous-dataset",
    vi.fn(),
    vi.fn(),
    vi.fn(),
    { current: null },
    false,
    report,
  );
  const params = { request: { startRow: 0 }, fail: vi.fn() };
  const reading = source.getRows(params);
  source.destroy();
  fail(new Error("late fixture"));
  await reading;
  expect(report).not.toHaveBeenCalled();
  expect(params.fail).not.toHaveBeenCalled();
});
