import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import ScenarioTable from "../ScenarioTable";

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (orig) => ({
  ...(await orig()),
  enqueueSnackbar: (...a) => enqueueSnackbar(...a),
  useSnackbar: () => ({ enqueueSnackbar }),
}));

const ROWS = [
  {
    id: "s1",
    number: 1,
    name: "refund-over-limit-01",
    summary: "Caller wants a refund above the limit",
    persona: { name: "Rachel Greene", gender: "female", ageGroup: "30-40" },
    situation: "You bought a jacket that arrived torn.",
    outcome: "Agent asks for supervisor approval.",
    subTasks: ["Verify the order", "Escalate"],
  },
];

const onEdit = vi.fn();
const onRemove = vi.fn();
const renderTable = (props = {}) =>
  renderWithRouter(<ScenarioTable rows={ROWS} onEdit={onEdit} onRemove={onRemove} onOpen={vi.fn()} {...props} />);

beforeEach(() => {
  enqueueSnackbar.mockClear();
  onEdit.mockClear();
  onRemove.mockClear();
});

describe("Scenario table in Vel's columns", () => {
  it("heads the columns #, Scenario, Persona, Sub-goals, Must-pass, Last runs, Brief, Passes when", () => {
    renderTable();
    const head = screen.getAllByRole("columnheader").map((c) => c.textContent);
    ["#", "Scenario", "Persona", "Sub-goals", "Brief", "Passes when"].forEach((h) =>
      expect(head.some((t) => t.startsWith(h))).toBe(true),
    );
    expect(head.some((t) => t.includes("Must-pass soon"))).toBe(true);
    expect(head.some((t) => t.includes("Last runs soon"))).toBe(true);
  });

  it("puts State and Source soon chips in the Scenario header", () => {
    renderTable();
    const scenarioHead = screen.getAllByRole("columnheader").find((c) => c.textContent.startsWith("Scenario"));
    expect(within(scenarioHead).getByText("State soon")).toBeInTheDocument();
    expect(within(scenarioHead).getByText("Source soon")).toBeInTheDocument();
  });

  it("Scenario cell shows the summary with the key on hover", () => {
    renderTable();
    expect(screen.getByText("Caller wants a refund above the limit")).toBeInTheDocument();
    expect(screen.queryByText("refund-over-limit-01")).toBeNull();
  });

  it('Persona reads "name · age group"', () => {
    renderTable();
    expect(screen.getByText("Rachel Greene · 30-40")).toBeInTheDocument();
  });

  it("Sub-goals shows the real count", () => {
    renderTable();
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByText("2")).toBeInTheDocument();
  });

  it("brief and passes-when read on one line", () => {
    renderTable();
    expect(screen.getByText("You bought a jacket that arrived torn.")).toBeInTheDocument();
    expect(screen.getByText("Agent asks for supervisor approval.")).toBeInTheDocument();
  });

  it("must-pass toggles locally, flagged UI only, and sends nothing", () => {
    renderTable();
    const toggle = screen.getByRole("button", { name: "Must-pass for refund-over-limit-01" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(enqueueSnackbar).toHaveBeenCalledWith("UI only. Nothing was saved.", { variant: "info" });
  });

  it("row ⋯ menu has Edit, Archive (UI only) and Remove", () => {
    renderTable();
    fireEvent.click(screen.getByRole("button", { name: "More actions for refund-over-limit-01" }));
    const menu = screen.getByRole("menu");
    fireEvent.click(within(menu).getByText("Edit"));
    expect(onEdit).toHaveBeenCalledWith(ROWS[0]);
    fireEvent.click(screen.getByRole("button", { name: "More actions for refund-over-limit-01" }));
    expect(within(screen.getByRole("menu")).getByLabelText("UI only")).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("menu")).getByText("Archive"));
    expect(enqueueSnackbar).toHaveBeenCalledWith("UI only. Nothing was saved.", { variant: "info" });
    fireEvent.click(screen.getByRole("button", { name: "More actions for refund-over-limit-01" }));
    fireEvent.click(within(screen.getByRole("menu")).getByText("Remove"));
    expect(onRemove).toHaveBeenCalledWith("s1");
  });

  it("a locked template has no row menu", () => {
    renderTable({ locked: true });
    expect(screen.getByRole("button", { name: "More actions for refund-over-limit-01" })).toBeDisabled();
  });
});
