import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { SnackbarProvider } from "notistack";

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const spy = vi.fn(() => Promise.resolve({ data: {} }));
  return {
    ...actual,
    default: Object.assign(spy, {
      get: spy,
      post: spy,
      put: spy,
      patch: spy,
      delete: spy,
    }),
  };
});
const { default: axios } = await import("src/utils/axios");
const { default: NeedsInputButton } = await import("../NeedsInputButton");

const ENV = { id: "env-1", name: "Refund Copilot", surface: "voice" };
const renderButton = (url = "/x") =>
  render(
    <SnackbarProvider>
      <MemoryRouter initialEntries={[url]}>
        <NeedsInputButton env={ENV} />
      </MemoryRouter>
    </SnackbarProvider>,
  );
const drawer = () => screen.getByRole("presentation");

beforeEach(() => axios.mockClear());

describe("NeedsInputButton on a real environment", () => {
  it("shows the inbox with no count", () => {
    renderButton();
    const button = screen.getByRole("button", { name: "Needs input" });
    expect(button.textContent).toBe("");
  });

  it("opens a drawer that says setup questions aren't in the API yet, flagged UI only", async () => {
    const user = userEvent.setup();
    renderButton();
    await user.click(screen.getByRole("button", { name: "Needs input" }));
    expect(
      within(drawer()).getByText("Nothing to resolve"),
    ).toBeInTheDocument();
    expect(
      within(drawer()).getByText(/Setup questions aren't in the API yet/),
    ).toBeInTheDocument();
    expect(within(drawer()).getByText("UI only")).toBeInTheDocument();
    expect(within(drawer()).queryByText("Waiting on you")).toBeNull();
  });
});

describe("NeedsInputButton with ?uiSample=gaps", () => {
  it("counts Vel's two open questions, not red since nothing blocks", () => {
    renderButton("/x?uiSample=gaps");
    expect(
      screen.getByRole("button", { name: "Needs input" }),
    ).toHaveTextContent("2");
  });

  it("shows Vel's meter, groups and credentials", async () => {
    const user = userEvent.setup();
    renderButton("/x?uiSample=gaps");
    await user.click(screen.getByRole("button", { name: "Needs input" }));
    const d = within(drawer());
    expect(d.getByText("Run can start")).toBeInTheDocument();
    expect(d.getByText("of 68 scenarios")).toBeInTheDocument();
    expect(d.getByText("Waiting on you")).toBeInTheDocument();
    expect(
      d.getByText("Do issue_refund and lookup_order share a store?"),
    ).toBeInTheDocument();
    expect(d.getByText("Credentials")).toBeInTheDocument();
    expect(d.getByText("· 3 of 4 set")).toBeInTheDocument();
    // The Resolved cell's note and the Settled group both read "Settled".
    expect(d.getAllByText("Settled")).toHaveLength(2);
    expect(d.getByText("UI only")).toBeInTheDocument();
  });

  it("accepting a suggestion settles the gap, says UI only, and sends nothing", async () => {
    const user = userEvent.setup();
    renderButton("/x?uiSample=gaps");
    await user.click(screen.getByRole("button", { name: "Needs input" }));
    await user.click(
      within(drawer()).getByRole("button", { name: "Accept suggestion" }),
    );
    expect(
      await screen.findByText("UI only. Nothing was saved."),
    ).toBeInTheDocument();
    // The open drawer hides the header from the accessibility tree.
    expect(
      screen.getByRole("button", { name: "Needs input", hidden: true }),
    ).toHaveTextContent("1");
    expect(axios).not.toHaveBeenCalled();
  });

  it("uses no em dashes in the sample copy", async () => {
    const user = userEvent.setup();
    renderButton("/x?uiSample=gaps");
    await user.click(screen.getByRole("button", { name: "Needs input" }));
    expect(drawer().textContent).not.toMatch(/—/);
  });
});
