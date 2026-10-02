import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPatchMock = vi.hoisted(() => vi.fn());
const fetchAllObserveProjectsMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock, patch: axiosPatchMock },
  endpoints: {},
}));

vi.mock("src/api/project/observe-project-list", () => ({
  fetchAllObserveProjects: fetchAllObserveProjectsMock,
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

import TagEditor from "./TagEditor";

const respondWithTags = (tags) =>
  axiosGetMock.mockResolvedValue({
    data: { status: true, result: { id: "p1", tags } },
  });

const renderEditor = (props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      {/* Mirrors the 150px Tags column in ObserveListView. */}
      <div style={{ width: 150 }}>
        <TagEditor projectId="p1" projectName="Checkout Service" {...props} />
      </div>
    </QueryClientProvider>,
  );
};

// MUI's modal marks the rest of the document aria-hidden while the popover
// is open, so the trigger must be queried with `hidden` to assert on it then.
const trigger = () =>
  screen.getByRole("button", { name: /tags for/i, hidden: true });

const openEditor = async () => {
  await waitFor(() => expect(trigger()).toBeInTheDocument());
  fireEvent.click(trigger());
  return screen.findByRole("presentation");
};

describe("TagEditor", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosPatchMock.mockReset();
    fetchAllObserveProjectsMock.mockReset();
    fetchAllObserveProjectsMock.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("project-list summary (TH-4058 regression)", () => {
    it("shows one bounded preview chip plus +1 for two tags instead of two clipped chips", async () => {
      respondWithTags(["billing-service-production", "customer-support-chatbot"]);
      renderEditor();

      await waitFor(() => expect(screen.getByText("+1")).toBeInTheDocument());

      const chips = trigger().querySelectorAll(".MuiChip-root");
      expect(chips).toHaveLength(1);
      expect(chips[0]).toHaveTextContent("billing-service-production");
      expect(trigger()).toHaveAttribute(
        "aria-label",
        "View tags for project Checkout Service, 2 tags",
      );
    });

    it("counts every tag beyond the preview and keeps response order", async () => {
      respondWithTags(["alpha", "beta", "gamma"]);
      renderEditor();

      await waitFor(() => expect(screen.getByText("+2")).toBeInTheDocument());
      expect(trigger().querySelectorAll(".MuiChip-root")).toHaveLength(1);
      expect(trigger()).toHaveTextContent("alpha");
      expect(trigger()).not.toHaveTextContent("beta");
    });

    it("shows a single chip with no remainder for one tag and an add affordance for none", async () => {
      respondWithTags(["solo"]);
      const { unmount } = renderEditor();
      await waitFor(() =>
        expect(trigger()).toHaveTextContent("solo"),
      );
      expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
      unmount();

      respondWithTags([]);
      renderEditor();
      await waitFor(() =>
        expect(trigger()).toHaveAttribute(
          "aria-label",
          "View tags for project Checkout Service, 0 tags",
        ),
      );
      expect(trigger().querySelectorAll(".MuiChip-root")).toHaveLength(0);
    });

    it("falls back to a total count when the trigger is too narrow for a readable preview", async () => {
      let resizeCallback;
      vi.stubGlobal(
        "ResizeObserver",
        class {
          constructor(cb) {
            resizeCallback = cb;
          }

          observe() {}

          unobserve() {}

          disconnect() {}
        },
      );
      respondWithTags(["alpha", "beta"]);
      renderEditor();
      await waitFor(() => expect(screen.getByText("+1")).toBeInTheDocument());

      await waitFor(() => expect(resizeCallback).toBeTypeOf("function"));
      resizeCallback([{ contentRect: { width: 60 } }]);

      await waitFor(() => expect(screen.getByText("2 tags")).toBeInTheDocument());
      expect(trigger().querySelectorAll(".MuiChip-root")).toHaveLength(0);

      resizeCallback([{ contentRect: { width: 140 } }]);
      await waitFor(() => expect(screen.getByText("+1")).toBeInTheDocument());
      vi.unstubAllGlobals();
    });

    it("never masquerades loading or a failed read as zero tags", async () => {
      let resolveRead;
      axiosGetMock.mockReturnValue(
        new Promise((resolve) => {
          resolveRead = resolve;
        }),
      );
      renderEditor();

      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: /are loading/i }),
        ).toBeInTheDocument(),
      );
      expect(screen.queryByText(/0 tags/)).not.toBeInTheDocument();

      resolveRead({ data: { status: true, result: { tags: ["alpha"] } } });
      await waitFor(() => expect(trigger()).toHaveTextContent("alpha"));
    });

    it("surfaces a project-tag read failure with a retry instead of an empty summary", async () => {
      axiosGetMock
        .mockRejectedValueOnce(new Error("boom"))
        .mockResolvedValue({ data: { status: true, result: { tags: ["alpha"] } } });
      renderEditor();

      await waitFor(() =>
        expect(screen.getByText("Tags unavailable")).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole("button", { name: /are unavailable/i }));
      const popover = await screen.findByRole("presentation");
      expect(within(popover).getByRole("alert")).toHaveTextContent(
        "Tags unavailable.",
      );
      expect(
        within(popover).queryByRole("button", { name: /edit tags|add tags/i }),
      ).not.toBeInTheDocument();

      fireEvent.click(within(popover).getByRole("button", { name: "Retry" }));
      await waitFor(() => expect(trigger()).toHaveTextContent("alpha"));
    });
  });

  describe("inspection before editing", () => {
    it("opens read-only full names without loading suggestions or writing", async () => {
      respondWithTags([
        "a-very-long-tag-name-that-does-not-fit-the-column",
        "beta",
      ]);
      renderEditor();
      const popover = await openEditor();

      expect(within(popover).getByText("Tags (2)")).toBeInTheDocument();
      const list = within(popover).getByTestId("tag-inspect-list");
      expect(list).toHaveTextContent(
        "a-very-long-tag-name-that-does-not-fit-the-column",
      );
      expect(list).toHaveTextContent("beta");
      expect(within(popover).queryByRole("checkbox")).not.toBeInTheDocument();
      expect(
        within(popover).queryByPlaceholderText("Search tags"),
      ).not.toBeInTheDocument();
      expect(fetchAllObserveProjectsMock).not.toHaveBeenCalled();

      fireEvent.click(within(popover).getByRole("button", { name: "Close" }));
      await waitFor(() =>
        expect(screen.queryByTestId("tag-inspect-list")).not.toBeInTheDocument(),
      );
      expect(axiosPatchMock).not.toHaveBeenCalled();
      expect(trigger()).toHaveAttribute("aria-expanded", "false");
    });

    it("opens from the keyboard and closes on Escape", async () => {
      respondWithTags(["alpha"]);
      renderEditor();
      await waitFor(() => expect(trigger()).toHaveTextContent("alpha"));

      fireEvent.keyDown(trigger(), { key: "Enter" });
      const popover = await screen.findByRole("presentation");
      expect(trigger()).toHaveAttribute("aria-expanded", "true");

      fireEvent.keyDown(popover, { key: "Escape" });
      await waitFor(() =>
        expect(screen.queryByTestId("tag-inspect-list")).not.toBeInTheDocument(),
      );
      expect(axiosPatchMock).not.toHaveBeenCalled();
    });

    it("offers Add tags when the project has none", async () => {
      respondWithTags([]);
      renderEditor();
      const popover = await openEditor();

      expect(within(popover).getByText("No tags on this project")).toBeInTheDocument();
      fireEvent.click(within(popover).getByRole("button", { name: "Add tags" }));
      expect(
        await within(popover).findByPlaceholderText("Type new tag and press Enter"),
      ).toBeInTheDocument();
    });
  });

  describe("deliberate editing", () => {
    it("switches to the existing editor, loads suggestions, toggles with a named checkbox and returns", async () => {
      respondWithTags(["alpha"]);
      fetchAllObserveProjectsMock.mockResolvedValue([
        { id: "p1", tags: ["alpha"] },
        { id: "p2", tags: ["beta"] },
      ]);
      axiosPatchMock.mockImplementation((_url, body) => {
        // The server now owns the new array; the settled refetch must see it.
        respondWithTags(body.tags);
        return Promise.resolve({ data: { result: { tags: body.tags } } });
      });
      renderEditor();
      const popover = await openEditor();

      fireEvent.click(within(popover).getByRole("button", { name: "Edit tags" }));
      expect(
        await within(popover).findByPlaceholderText("Search tags"),
      ).toBeInTheDocument();
      await waitFor(() => expect(fetchAllObserveProjectsMock).toHaveBeenCalled());

      const beta = await within(popover).findByRole("checkbox", { name: "beta" });
      expect(beta).not.toBeChecked();
      fireEvent.click(beta);

      await waitFor(() =>
        expect(axiosPatchMock).toHaveBeenCalledWith("/tracer/project/p1/tags/", {
          tags: ["alpha", "beta"],
        }),
      );

      fireEvent.click(within(popover).getByRole("button", { name: "Back to tags" }));
      const list = await within(popover).findByTestId("tag-inspect-list");
      expect(list).toHaveTextContent("beta");
    });

    it("keeps reading the selected tags when suggestions fail", async () => {
      respondWithTags(["alpha"]);
      fetchAllObserveProjectsMock.mockRejectedValue(new Error("nope"));
      renderEditor();
      const popover = await openEditor();
      fireEvent.click(within(popover).getByRole("button", { name: "Edit tags" }));

      expect(
        await within(popover).findByText("Tag suggestions unavailable."),
      ).toBeInTheDocument();
      expect(within(popover).getByRole("checkbox", { name: "alpha" })).toBeChecked();
    });

    it("creates a trimmed new tag on Enter and ignores blanks and duplicates", async () => {
      respondWithTags(["alpha"]);
      axiosPatchMock.mockImplementation((_url, body) =>
        Promise.resolve({ data: { result: { tags: body.tags } } }),
      );
      renderEditor();
      const popover = await openEditor();
      fireEvent.click(within(popover).getByRole("button", { name: "Edit tags" }));
      const input = await within(popover).findByPlaceholderText(
        "Type new tag and press Enter",
      );

      fireEvent.change(input, { target: { value: "   " } });
      fireEvent.keyDown(input, { key: "Enter" });
      fireEvent.change(input, { target: { value: "alpha" } });
      fireEvent.keyDown(input, { key: "Enter" });
      expect(axiosPatchMock).not.toHaveBeenCalled();

      fireEvent.change(input, { target: { value: "  gamma  " } });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() =>
        expect(axiosPatchMock).toHaveBeenCalledWith("/tracer/project/p1/tags/", {
          tags: ["alpha", "gamma"],
        }),
      );
      expect(axiosPatchMock).toHaveBeenCalledTimes(1);
    });
  });

  describe("header variant", () => {
    it("keeps the existing two-chip presentation and editor-first entry", async () => {
      respondWithTags(["alpha", "beta", "gamma"]);
      renderEditor({ variant: "header" });

      await waitFor(() =>
        expect(trigger().querySelectorAll(".MuiChip-root")).toHaveLength(2),
      );
      expect(screen.getByText("+1")).toBeInTheDocument();

      const popover = await openEditor();
      expect(
        await within(popover).findByPlaceholderText("Search tags"),
      ).toBeInTheDocument();
      expect(
        within(popover).queryByRole("button", { name: "Back to tags" }),
      ).not.toBeInTheDocument();
    });
  });
});
