import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const read = vi.fn();
const rework = vi.fn();
vi.mock("src/api/simulate-environments/startingRecords", () => ({
  useStartingRecords: (...args) => read(...args),
  useReworkStartingRecord: (...args) => rework(...args),
}));

const { default: StartingRecords } = await import("../StartingRecords");
const { subscribeBuilderChatFocus } = await import(
  "../../../buildEnvironment/console/builderChatFocusBus"
);

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
  choices: [
    { key: "rdr_priya", summary: "Priya's row, as the server words it" },
  ],
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
  choices: [],
};
const RECORDS = {
  scenario_key: "book-ride",
  origin: "seed",
  records: [RIDER, MISSING],
  setup_note: null,
  rows_unavailable: null,
  rework: null,
};

const reads = (data) =>
  read.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });

const sends = (state = {}) => {
  const mutate = vi.fn();
  rework.mockReturnValue({
    mutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
    ...state,
  });
  return mutate;
};

const open = () =>
  render(<StartingRecords envId="env-1" scenarioKey="book-ride" />);

describe("StartingRecords", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sends();
  });

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

  it("asks the builder to start the scenario from the row picked instead", () => {
    reads(RECORDS);
    const mutate = sends();
    open();

    const rider = within(
      screen.getByTestId("starting-record-identity.rider_id"),
    );
    fireEvent.click(
      rider.getByRole("button", { name: "Start from another record" }),
    );
    fireEvent.mouseDown(rider.getByLabelText("Record to start from"));
    fireEvent.click(
      within(screen.getByRole("listbox")).getByText(
        "Priya's row, as the server words it",
      ),
    );
    fireEvent.click(rider.getByRole("button", { name: "Ask the builder" }));

    expect(mutate.mock.calls[0][0]).toEqual({
      path: ["identity", "rider_id"],
      table: "users",
      key: "rdr_priya",
    });
  });

  it("shows the builder's request as the server reads it, and asks no other while it runs", () => {
    reads({
      ...RECORDS,
      rework: {
        id: "m1",
        state: "pending",
        table: "users",
        key_field: "rider_id",
        key: "rdr_priya",
        reason: null,
        requested_at: "2026-11-16T10:00:00Z",
      },
    });
    open();

    expect(screen.getByTestId("starting-records-rework")).toHaveTextContent(
      "users rider_id rdr_priya",
    );
    expect(
      screen.getByRole("button", { name: "Start from another record" }),
    ).toBeDisabled();
  });

  it("says the builder is waiting on the person, and opens its chat at the message that asks", () => {
    const focused = [];
    const stop = subscribeBuilderChatFocus((id) => focused.push(id));
    reads({
      ...RECORDS,
      rework: {
        id: "m1",
        state: "waiting",
        table: "users",
        key_field: "rider_id",
        key: "rdr_priya",
        reason: null,
        requested_at: "2026-11-16T10:00:00Z",
        confirm_message_id: "ask-1",
      },
    });
    open();

    expect(screen.getByTestId("starting-records-rework")).toHaveTextContent(
      "needs you to confirm",
    );
    expect(
      screen.getByRole("button", { name: "Start from another record" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Open the builder chat" }),
    );
    stop();
    expect(focused).toEqual(["ask-1"]);
  });

  it("shows a refused request's reason in the server's words", () => {
    reads({
      ...RECORDS,
      rework: {
        id: "m1",
        state: "refused",
        table: "users",
        key_field: "rider_id",
        key: "rdr_priya",
        reason: "The server's reason.",
        requested_at: "2026-11-16T10:00:00Z",
      },
    });
    open();

    expect(screen.getByTestId("starting-records-rework")).toHaveTextContent(
      "The server's reason.",
    );
  });

  it("keeps a builder's refusal to the server's plain line, its own reply rendered on asking, with a way to it", () => {
    const focused = [];
    const stop = subscribeBuilderChatFocus((id) => focused.push(id));
    reads({
      ...RECORDS,
      rework: {
        id: "m1",
        state: "refused",
        table: "orders",
        key_field: "order_id",
        key: "1044",
        reason:
          "The builder says this scenario can't start from orders 1044 as seeded.",
        requested_at: "2026-11-16T10:00:00Z",
        confirm_message_id: null,
        builder_reply: "### Ruling value\n- **`days_since_delivery: 41`**",
        reply_message_id: "reply-1",
      },
    });
    open();

    const line = screen.getByTestId("starting-records-rework");
    expect(line).toHaveTextContent(
      "The builder says this scenario can't start from orders 1044 as seeded.",
    );
    expect(line).not.toHaveTextContent("**");
    fireEvent.click(
      screen.getByRole("button", { name: "The builder's reply" }),
    );
    expect(screen.getByRole("heading", { name: "Ruling value" })).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Open in the builder chat" }),
    );
    stop();
    expect(focused).toEqual(["reply-1"]);
  });

  it("shows the server's problem with a choice under the picker", () => {
    reads(RECORDS);
    sends({
      isError: true,
      error: {
        problems: [
          {
            field: "key",
            message: "users has no seeded row with rider_id rdr_x.",
          },
        ],
      },
    });
    open();

    fireEvent.click(
      screen.getByRole("button", { name: "Start from another record" }),
    );

    expect(
      screen.getByText("users has no seeded row with rider_id rdr_x."),
    ).toBeInTheDocument();
  });
});
