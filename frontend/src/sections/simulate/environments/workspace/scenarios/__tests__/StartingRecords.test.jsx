import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const read = vi.fn();
const choose = vi.fn();
vi.mock("src/api/simulate-environments/startingRecords", () => ({
  useStartingRecords: (...args) => read(...args),
  useChooseStartingRecord: (...args) => choose(...args),
}));

const { default: StartingRecords } = await import("../StartingRecords");

const ARJUN = {
  rider_id: "rdr_arjun",
  first_name: "Arjun",
  phone: "+14155550101",
};
const PRIYA = {
  rider_id: "rdr_priya",
  first_name: "Priya",
  phone: "+14155550102",
};
const RIDER = {
  path: ["identity", "rider_id"],
  label: "identity.rider_id",
  table: "users",
  key_field: "rider_id",
  key: "rdr_arjun",
  fields: ARJUN,
  alternatives: [{ key: "rdr_priya", fields: PRIYA }],
  alternatives_total: 1,
};
const RECORDS = {
  scenario_key: "book-ride",
  origin: "seed",
  editable: true,
  rows_unavailable: null,
  records: [RIDER],
};

const reads = (data) =>
  read.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
const chooses = (state = {}) => {
  const mutate = vi.fn();
  choose.mockReturnValue({
    mutate,
    isPending: false,
    error: null,
    variables: undefined,
    ...state,
  });
  return mutate;
};

const open = () =>
  render(<StartingRecords envId="env-1" scenarioKey="book-ride" />);

describe("StartingRecords", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the row the scenario starts from, read for this environment and scenario", () => {
    reads(RECORDS);
    chooses();
    open();

    expect(read).toHaveBeenCalledWith("env-1", "book-ride");
    expect(
      screen.getByLabelText("identity.rider_id · users"),
    ).toHaveTextContent("rdr_arjun · Arjun · +14155550101");
    expect(screen.getByText("first_name")).toBeInTheDocument();
    expect(screen.getByText("Arjun")).toBeInTheDocument();
  });

  it("sends the row chosen instead, with the fact's path and table", () => {
    reads(RECORDS);
    const mutate = chooses();
    open();

    fireEvent.mouseDown(screen.getByLabelText("identity.rider_id · users"));
    fireEvent.click(
      within(screen.getByRole("listbox")).getByText(
        "rdr_priya · Priya · +14155550102",
      ),
    );

    expect(mutate).toHaveBeenCalledWith({
      label: "identity.rider_id",
      path: ["identity", "rider_id"],
      table: "users",
      key: "rdr_priya",
    });
  });

  it("shows the server's problem with the choice on the record it is about", () => {
    reads(RECORDS);
    chooses({
      error: {
        error: "starting_record_invalid",
        message: "users has no seeded row with rider_id rdr_nobody.",
        problems: [
          {
            field: "key",
            message: "users has no seeded row with rider_id rdr_nobody.",
          },
        ],
      },
      variables: { label: "identity.rider_id" },
    });
    open();

    expect(
      screen.getByText("users has no seeded row with rider_id rdr_nobody."),
    ).toBeInTheDocument();
  });

  it("says why an older environment's rows can't be read, and offers no choice", () => {
    reads({
      ...RECORDS,
      editable: false,
      records: [],
      rows_unavailable: {
        code: "no_seed_rows",
        reason: "This environment's world keeps no seeded rows.",
      },
    });
    chooses();
    open();

    expect(
      screen.getByText("This environment's world keeps no seeded rows."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("keeps the choice closed where the environment can't take one", () => {
    reads({ ...RECORDS, editable: false });
    chooses();
    open();

    expect(screen.getByLabelText("identity.rider_id · users")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });
});
