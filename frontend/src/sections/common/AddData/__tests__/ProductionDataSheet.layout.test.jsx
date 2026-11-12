import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import ProductionDataSheet from "../ProductionDataSheet";

// C5 (screen pass w*-21-add-to-dataset): the Add to dataset sheet opened
// from a trace had no Close button at the top, only Cancel at the bottom; at
// 1024 px the drawer fills the window, so no backdrop is left to click. The
// "Remove column N" buttons were text buttons at the top of their row, level
// with the field labels rather than with the inputs, and read as plain text.

const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  empty: [],
  busy: false,
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: h.get, post: h.post },
}));
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDevelopDatasetList: () => ({ data: h.empty }),
  useGetDatasetColumns: () => ({ data: h.empty }),
}));
vi.mock("../useProductionImport", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => ({
    submit: vi.fn(),
    busy: h.busy,
    pending: null,
    error: null,
    setError: vi.fn(),
  }),
}));

// Register the sheet's glyphs, as their owners do before rendering; an
// unregistered glyph adds "Missing offline icon" to accessible names.
for (const name of ["mingcute:close-line", "solar:routing-2-linear"]) {
  registerOfflineIcon(name, bundled[name]);
}

// One span chosen in Observe, as when the sheet opens from a trace.
const FROM_TRACE = {
  kind: "traces",
  project_id: "project",
  span_ids: ["span-1"],
};
const SPAN = { span_id: "span-1", trace_id: "trace-1", name: "ChatOpenAI" };

let client;
beforeEach(() => {
  vi.clearAllMocks();
  h.busy = false;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  h.get.mockResolvedValue({
    data: { result: { projects: [{ id: "project", name: "Project" }] } },
  });
  h.post.mockResolvedValue({
    data: { result: { items: [SPAN], limited: false, limit: 200 } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

function mount(source = FROM_TRACE) {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ProductionDataSheet
        initial={{ source, target: { new: { name: "" } } }}
        onClose={onClose}
        setDirty={vi.fn()}
      />
    </QueryClientProvider>,
  );
  return onClose;
}

const loaded = () => screen.findByLabelText("ChatOpenAI · span-1");
const header = () => screen.getByTestId("add-data-sheet").firstElementChild;

describe("Add to dataset sheet header and column rows (C5)", () => {
  it("puts the title and a Close button in a header above the scrolling form", async () => {
    mount();
    await loaded();

    const top = header();
    expect(within(top).getByText("From production")).toBeInTheDocument();
    expect(within(top).getByRole("button", { name: "Close" })).toBeEnabled();
    // The form scrolls below the header, so Close stays in view.
    expect(top).not.toContainElement(
      screen.getByRole("combobox", { name: "Project" }),
    );
    expect(top).not.toContainElement(
      screen.getByRole("button", { name: "Cancel" }),
    );
  });

  it("closes the sheet the way Cancel does, and keeps Cancel", async () => {
    const user = userEvent.setup();
    const onClose = mount();
    await loaded();

    await user.click(within(header()).getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenNthCalledWith(1, true);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenNthCalledWith(2, true);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("disables Close while rows are being written, as it does Cancel", async () => {
    h.busy = true;
    mount();
    await loaded();

    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(
      within(header()).getByRole("button", { name: "Close" }),
    ).toBeDisabled();
  });

  it("offers the same Close when the workspace has no Observe project", async () => {
    const user = userEvent.setup();
    h.get.mockResolvedValue({ data: { result: { projects: [] } } });
    const onClose = mount({ kind: "sessions" });
    expect(await screen.findByText(/no observe projects/i)).toBeVisible();

    const top = header();
    expect(within(top).getByText("From production")).toBeInTheDocument();
    await user.click(within(top).getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledWith(true);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("draws each Remove column button as an outlined button on its input row", async () => {
    mount();
    await loaded();

    for (const row of [1, 2]) {
      const remove = screen.getByRole("button", {
        name: `Remove column ${row}`,
      });
      expect(remove).toHaveClass("MuiButton-outlined");
      expect(remove).not.toHaveClass("MuiButton-text");
      // Bottom-aligned in its row, level with the inputs, not the labels.
      expect(remove).toHaveStyle({ alignSelf: "flex-end" });
      expect(remove.parentElement).toContainElement(
        screen.getByLabelText(`Column ${row}`),
      );
    }
  });
});
