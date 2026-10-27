import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
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
const { default: VersionBar } = await import("../../VersionBar");

const ENV = {
  id: "env-1",
  testSubject: {
    name: "Refund agent",
    active_version: "v1",
    versions_count: 3,
  },
  counts: { scenarios: 12 },
};

let search = "";
function Probe() {
  search = useLocation().search;
  return null;
}
const renderBar = (url = "/x") =>
  render(
    <SnackbarProvider>
      <MemoryRouter initialEntries={[url]}>
        <VersionBar env={ENV} envState={{}} />
        <Probe />
      </MemoryRouter>
    </SnackbarProvider>,
  );

beforeEach(() => axios.mockClear());

describe("VersionBar on a real environment", () => {
  it("reads Agent v1 × environment, the name on hover, the environment version still to come", async () => {
    const user = userEvent.setup();
    renderBar();
    const line = screen.getByText(
      (_, el) =>
        el?.tagName === "P" && el.textContent === "Agent v1×environment",
    );
    expect(line).toBeInTheDocument();
    await user.hover(line);
    expect(
      await screen.findByText("Refund agent v1, the test subject"),
    ).toBeInTheDocument();
    expect(screen.getByText("Version soon")).toBeInTheDocument();
    expect(screen.getByText(/· 12 scenarios/)).toBeInTheDocument();
    expect(screen.queryByText(/edits? since environment/)).toBeNull();
  });

  it("opens History with the real agent version and no sample environment versions", async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(screen.getByRole("button", { name: "History" }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("Version history")).toBeInTheDocument();
    expect(
      within(menu).getByText(/Environment versions aren't in the API yet/),
    ).toBeInTheDocument();
    expect(within(menu).getByText("Agent v1")).toBeInTheDocument();
    expect(within(menu).getByText("active")).toBeInTheDocument();
    expect(within(menu).queryByText("Roll back")).toBeNull();
  });
});

describe("VersionBar with ?uiSample=versions", () => {
  it("names environment v3 and the unsaved edits, flagged UI only", () => {
    renderBar("/x?uiSample=versions");
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(
      screen.getByText(/2 edits since environment v3/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save as environment v4" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("UI only").length).toBeGreaterThan(0);
  });

  it("rolls back through a dialog that says what changes, and sends nothing", async () => {
    const user = userEvent.setup();
    renderBar("/x?uiSample=versions");
    await user.click(screen.getByRole("button", { name: "History" }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("latest")).toBeInTheDocument();
    await user.click(
      within(menu).getAllByRole("button", { name: "Roll back" })[0],
    );
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Roll back to environment v2?"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/makes environment v4 as it was in v2/),
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Roll back to v2" }),
    );
    expect(
      await screen.findByText("UI only. Nothing was saved."),
    ).toBeInTheDocument();
    expect(axios).not.toHaveBeenCalled();
  });

  it("views an older version read-only, with one way back", async () => {
    const user = userEvent.setup();
    renderBar("/x?uiSample=versions");
    await user.click(screen.getByRole("button", { name: "History" }));
    await user.click(
      within(screen.getByRole("menu")).getAllByRole("button", {
        name: "View",
      })[0],
    );
    expect(search).toContain("viewAs=v2");
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === "P" &&
          el.textContent === "Viewing as environment v2. Read-only.",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back to current" }));
    expect(search).not.toContain("viewAs");
  });

  it("uses no em dashes", async () => {
    const user = userEvent.setup();
    renderBar("/x?uiSample=versions");
    await user.click(screen.getByRole("button", { name: "History" }));
    expect(document.body.textContent).not.toMatch(/—/);
  });
});
