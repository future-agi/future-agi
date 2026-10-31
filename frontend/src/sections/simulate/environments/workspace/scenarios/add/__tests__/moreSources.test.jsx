import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import ProductionRoute from "../ProductionRoute";
import DatasetRoute from "../DatasetRoute";
import ScriptRoute from "../ScriptRoute";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (orig) => ({
  ...(await orig()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
  useSnackbar: () => ({ enqueueSnackbar }),
}));

const ENV = { ...MOCK_WORLD, id: "job-more" };
const onClose = vi.fn();
const render = (Route) =>
  renderWithRouter(
    <Route env={ENV} envState={{ scenarios: [] }} onClose={onClose} />,
  );

const expectFlaggedHeading = (text) => {
  const heading = screen.getByText(text);
  expect(
    within(heading.parentElement).getByLabelText("UI only"),
  ).toBeInTheDocument();
};

const expectSavedNothing = () => {
  expect(enqueueSnackbar).toHaveBeenCalledWith("UI only. Nothing was saved.", {
    variant: "info",
  });
  expect(onClose).toHaveBeenCalledTimes(1);
};

const expectNoEmDash = () =>
  expect(document.body.textContent).not.toContain("—");

beforeEach(() => {
  enqueueSnackbar.mockClear();
  onClose.mockClear();
});

describe("ProductionRoute", () => {
  it("shows Vel's heading with a UI only flag", () => {
    render(ProductionRoute);
    expectFlaggedHeading("Production conversations, replayed word for word");
    expectNoEmDash();
  });

  it("picks replays and adds them without saving anything", () => {
    render(ProductionRoute);
    const add = screen.getByRole("button", { name: "Add replays" });
    expect(add).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Select Caller chasing their order",
      }),
    );
    fireEvent.click(screen.getByText("Caller chasing their refund"));
    expect(
      screen.getByText(/Persona inferred: Marcus Hale/),
    ).toBeInTheDocument();
    expectNoEmDash();
    fireEvent.click(screen.getByRole("button", { name: "Add 1 replay" }));
    expectSavedNothing();
  });

  it("selects every open conversation at once", () => {
    render(ProductionRoute);
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));
    expect(screen.getByRole("button", { name: "Add 5 replays" })).toBeEnabled();
  });
});

describe("DatasetRoute", () => {
  it("shows Vel's heading with a UI only flag", () => {
    render(DatasetRoute);
    expectFlaggedHeading("Import from a dataset");
    expectNoEmDash();
  });

  it("picks rows from the sample dataset and imports nothing", () => {
    render(DatasetRoute);
    expect(screen.getAllByText("Support transcripts").length).toBeGreaterThan(
      0,
    );
    const add = screen.getByRole("button", { name: "Add scenarios" });
    expect(add).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select row 1" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select row 2" }));
    expectNoEmDash();
    fireEvent.click(screen.getByRole("button", { name: "Add 2 scenarios" }));
    expectSavedNothing();
  });

  it("switches datasets and filters rows by search", () => {
    render(DatasetRoute);
    fireEvent.click(screen.getByRole("radio", { name: /Pilot sample/ }));
    expect(screen.getByText("Where's my refund?")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Search rows"), {
      target: { value: "invoice" },
    });
    expect(screen.getByText("1 of 6 rows match")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select 1 matching" }));
    expect(
      screen.getByRole("button", { name: "Add 1 scenario" }),
    ).toBeEnabled();
  });
});

describe("ScriptRoute", () => {
  it("shows Vel's heading with a UI only flag", () => {
    render(ScriptRoute);
    expectFlaggedHeading("Upload a script");
    expectNoEmDash();
  });

  it("reads a dropped script into sample scenarios and extracts nothing", async () => {
    const { container } = render(ScriptRoute);
    const input = container.querySelector('input[type="file"]');
    const file = new File(
      ["Opening. Objection. Close."],
      "returns-script.txt",
      { type: "text/plain" },
    );
    fireEvent.change(input, { target: { files: [file] } });
    expect(await screen.findByText("returns-script.txt")).toBeInTheDocument();
    expect(
      await screen.findByText(
        /scenarios found in returns-script\.txt/,
        {},
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));
    expectNoEmDash();
    const add = screen.getByRole("button", { name: /^Add \d+ scenarios$/ });
    expect(add).toBeEnabled();
    fireEvent.click(add);
    expectSavedNothing();
  });
});
