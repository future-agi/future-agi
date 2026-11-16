import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const read = vi.fn();
vi.mock("src/api/simulate-environments/startingRecords", () => ({
  useStartingRecords: (...args) => read(...args),
}));

const { default: StartingRecords } = await import("../StartingRecords");

const RIDER = {
  path: ["identity", "rider_id"],
  label: "identity.rider_id",
  table: "users",
  key_field: "rider_id",
  key: "rdr_arjun",
  status: "seeded",
  summary: "Arjun's row, as the server words it",
  fields: { rider_id: "rdr_arjun", first_name: "Arjun", tags: ["vip"] },
  note: null,
};
const MISSING = {
  path: ["order_id"],
  label: "order_id",
  table: "orders",
  key_field: "order_id",
  key: "ord_9",
  status: "missing",
  summary: null,
  fields: null,
  note: "orders has no seeded row with order_id ord_9.",
};
const RECORDS = {
  scenario_key: "book-ride",
  origin: "seed",
  records: [RIDER, MISSING],
  setup_note: null,
  rows_unavailable: null,
};

const reads = (data) =>
  read.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });

const open = () =>
  render(<StartingRecords envId="env-1" scenarioKey="book-ride" />);

describe("StartingRecords", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows each record the scenario starts from in the server's words, with its fields", () => {
    reads(RECORDS);
    open();

    const rider = within(
      screen.getByTestId("starting-record-identity.rider_id"),
    );
    expect(rider.getByText("identity.rider_id · users")).toBeInTheDocument();
    expect(
      rider.getByText("Arjun's row, as the server words it"),
    ).toBeInTheDocument();
    expect(rider.getByText("first_name")).toBeInTheDocument();
    expect(rider.getByText("Arjun")).toBeInTheDocument();
    expect(rider.getByText('["vip"]')).toBeInTheDocument();
  });

  it("shows a record the seed doesn't hold as missing, with the server's note", () => {
    reads(RECORDS);
    open();

    const missing = within(screen.getByTestId("starting-record-order_id"));
    expect(
      missing.getByText("orders has no seeded row with order_id ord_9."),
    ).toBeInTheDocument();
  });

  it("says when the scenario's setup changes the world before each run", () => {
    reads({ ...RECORDS, setup_note: "The server's setup note." });
    open();

    expect(screen.getByText("The server's setup note.")).toBeInTheDocument();
  });

  it("says why the world's rows can't be read", () => {
    reads({
      ...RECORDS,
      records: [],
      rows_unavailable: {
        code: "no_seed_rows",
        reason: "The server's reason.",
      },
    });
    open();

    expect(screen.getByText("The server's reason.")).toBeInTheDocument();
  });
});
