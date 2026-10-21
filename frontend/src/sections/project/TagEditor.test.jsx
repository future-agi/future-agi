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
const enqueueSnackbarMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock, patch: axiosPatchMock },
  endpoints: {},
}));

vi.mock("src/api/project/observe-project-list", () => ({
  fetchAllObserveProjects: fetchAllObserveProjectsMock,
}));

vi.mock("notistack", () => ({ enqueueSnackbar: enqueueSnackbarMock }));

import TagEditor from "./TagEditor";

const tagsResponse = (tags) => ({
  data: { status: true, result: { id: "p1", tags } },
});

const respondWithTags = (tags) =>
  axiosGetMock.mockResolvedValue(tagsResponse(tags));

const renderEditor = (props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      {/* Mirrors the 150px Tags column in ObserveListView. */}
      <div style={{ width: 150 }}>
        <TagEditor projectId="p1" projectName="Checkout Service" {...props} />
      </div>
    </QueryClientProvider>,
  );
  return { ...utils, queryClient };
};

// MUI's modal marks the rest of the document aria-hidden while the popover
// is open, so the trigger must be queried with `hidden` to assert on it then.
// The list trigger is named "View tags for …", the header trigger "Edit
// tags, N tags"; the in-popover "Edit tags" button has no trailing comma.
const trigger = () =>
  screen.getByRole("button", {
    name: /^(view tags for|tags for|edit tags,)/i,
    hidden: true,
  });

const openEditor = async () => {
  await waitFor(() => expect(trigger()).toBeInTheDocument());
  fireEvent.click(trigger());
  return screen.findByRole("presentation");
};

const enterEditMode = async (popover) => {
  fireEvent.click(
    within(popover).getByRole("button", { name: /^(edit|add) tags$/i }),
  );
  return within(popover).findByPlaceholderText("Type new tag and press Enter");
};

describe("TagEditor", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosPatchMock.mockReset();
    fetchAllObserveProjectsMock.mockReset();
    enqueueSnackbarMock.mockReset();
    fetchAllObserveProjectsMock.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  describe("project-list summary (TH-4058 regression)", () => {
    it("shows one bounded preview chip plus +1 for two tags instead of two clipped chips", async () => {
      respondWithTags([
        "billing-service-production",
        "customer-support-chatbot",
      ]);
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

    it("shows a single chip with no remainder for one tag", async () => {
      respondWithTags(["solo"]);
      renderEditor();
      await waitFor(() => expect(trigger()).toHaveTextContent("solo"));
      expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
    });

    it("shows the text 'No tags' with a disclosure for a project without tags (D01)", async () => {
      respondWithTags([]);
      renderEditor();
      await waitFor(() =>
        expect(trigger()).toHaveAttribute(
          "aria-label",
          "View tags for project Checkout Service, 0 tags",
        ),
      );
      expect(within(trigger()).getByText("No tags")).toBeInTheDocument();
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

      await waitFor(() =>
        expect(screen.getByText("2 tags")).toBeInTheDocument(),
      );
      expect(trigger().querySelectorAll(".MuiChip-root")).toHaveLength(0);

      resizeCallback([{ contentRect: { width: 140 } }]);
      await waitFor(() => expect(screen.getByText("+1")).toBeInTheDocument());
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
      expect(screen.queryByText("No tags")).not.toBeInTheDocument();
      expect(screen.queryByText(/^\d+ tags?$/)).not.toBeInTheDocument();

      resolveRead(tagsResponse(["alpha"]));
      await waitFor(() => expect(trigger()).toHaveTextContent("alpha"));
    });

    it("surfaces a first-read failure with a retry instead of an empty summary", async () => {
      axiosGetMock
        .mockRejectedValueOnce(new Error("boom"))
        .mockResolvedValue(tagsResponse(["alpha"]));
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

    it("keeps the last-known tags readable and pauses editing when a refresh fails (AC08)", async () => {
      respondWithTags(["alpha", "beta"]);
      const { queryClient } = renderEditor();
      await waitFor(() => expect(screen.getByText("+1")).toBeInTheDocument());

      axiosGetMock.mockRejectedValue(new Error("refresh failed"));
      await queryClient.invalidateQueries({ queryKey: ["project-tags", "p1"] });

      // Summary: still the chip + remainder, with a stale indication.
      await waitFor(() =>
        expect(trigger()).toHaveAttribute(
          "aria-label",
          expect.stringMatching(/2 tags.*refresh/i),
        ),
      );
      expect(trigger()).toHaveTextContent("alpha");
      expect(screen.getByText("+1")).toBeInTheDocument();
      expect(screen.queryByText("Tags unavailable")).not.toBeInTheDocument();

      // Inspection: names remain, stale alert with Retry, no editing.
      fireEvent.click(trigger());
      const popover = await screen.findByRole("presentation");
      const list = within(popover).getByTestId("tag-inspect-list");
      expect(list).toHaveTextContent("alpha");
      expect(list).toHaveTextContent("beta");
      expect(within(popover).getByRole("alert")).toHaveTextContent(
        /couldn.t refresh/i,
      );
      expect(
        within(popover).getByRole("button", { name: "Edit tags" }),
      ).toBeDisabled();

      // Recovery re-enables editing.
      respondWithTags(["alpha", "beta"]);
      fireEvent.click(within(popover).getByRole("button", { name: "Retry" }));
      await waitFor(() =>
        expect(
          within(popover).getByRole("button", { name: "Edit tags" }),
        ).toBeEnabled(),
      );
      expect(within(popover).queryByRole("alert")).not.toBeInTheDocument();
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
        expect(
          screen.queryByTestId("tag-inspect-list"),
        ).not.toBeInTheDocument(),
      );
      expect(axiosPatchMock).not.toHaveBeenCalled();
      expect(trigger()).toHaveAttribute("aria-expanded", "false");
    });

    it("exposes the popover as a named dialog", async () => {
      respondWithTags(["alpha", "beta"]);
      renderEditor();
      const popover = await openEditor();
      expect(
        within(popover).getByRole("dialog", { name: "Tags (2)" }),
      ).toBeInTheDocument();
    });

    it("opens with Enter or Space, closes on Escape and returns focus to the trigger", async () => {
      respondWithTags(["alpha"]);
      renderEditor();
      await waitFor(() => expect(trigger()).toHaveTextContent("alpha"));

      trigger().focus();
      fireEvent.keyDown(trigger(), { key: "Enter" });
      let popover = await screen.findByRole("presentation");
      expect(trigger()).toHaveAttribute("aria-expanded", "true");

      fireEvent.keyDown(popover, { key: "Escape" });
      await waitFor(() =>
        expect(
          screen.queryByTestId("tag-inspect-list"),
        ).not.toBeInTheDocument(),
      );
      await waitFor(() => expect(document.activeElement).toBe(trigger()));

      fireEvent.keyDown(trigger(), { key: " " });
      popover = await screen.findByRole("presentation");
      expect(within(popover).getByTestId("tag-inspect-list")).toBeVisible();
      expect(axiosPatchMock).not.toHaveBeenCalled();
    });

    it("offers Add tags when the project has none", async () => {
      respondWithTags([]);
      renderEditor();
      const popover = await openEditor();

      expect(
        within(popover).getByText("No tags on this project"),
      ).toBeInTheDocument();
      fireEvent.click(
        within(popover).getByRole("button", { name: "Add tags" }),
      );
      expect(
        await within(popover).findByPlaceholderText(
          "Type new tag and press Enter",
        ),
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

      fireEvent.click(
        within(popover).getByRole("button", { name: "Edit tags" }),
      );
      expect(
        await within(popover).findByPlaceholderText("Search tags"),
      ).toBeInTheDocument();
      await waitFor(() =>
        expect(fetchAllObserveProjectsMock).toHaveBeenCalled(),
      );

      const beta = await within(popover).findByRole("checkbox", {
        name: "beta",
      });
      expect(beta).not.toBeChecked();
      fireEvent.click(beta);

      await waitFor(() =>
        expect(axiosPatchMock).toHaveBeenCalledWith(
          "/tracer/project/p1/tags/",
          {
            tags: ["alpha", "beta"],
          },
        ),
      );

      fireEvent.click(
        within(popover).getByRole("button", { name: "Back to tags" }),
      );
      const list = await within(popover).findByTestId("tag-inspect-list");
      expect(list).toHaveTextContent("beta");
    });

    it("moves focus to Edit tags when returning from the editor", async () => {
      respondWithTags(["alpha"]);
      renderEditor();
      const popover = await openEditor();
      await enterEditMode(popover);

      fireEvent.click(
        within(popover).getByRole("button", { name: "Back to tags" }),
      );
      const edit = await within(popover).findByRole("button", {
        name: "Edit tags",
      });
      await waitFor(() => expect(document.activeElement).toBe(edit));
    });

    it("keeps reading the selected tags when suggestions fail", async () => {
      respondWithTags(["alpha"]);
      fetchAllObserveProjectsMock.mockRejectedValue(new Error("nope"));
      renderEditor();
      const popover = await openEditor();
      fireEvent.click(
        within(popover).getByRole("button", { name: "Edit tags" }),
      );

      expect(
        await within(popover).findByText("Tag suggestions unavailable."),
      ).toBeInTheDocument();
      expect(
        within(popover).getByRole("checkbox", { name: "alpha" }),
      ).toBeChecked();
    });

    it("creates a trimmed new tag on Enter and ignores blanks and duplicates", async () => {
      respondWithTags(["alpha"]);
      axiosPatchMock.mockImplementation((_url, body) =>
        Promise.resolve({ data: { result: { tags: body.tags } } }),
      );
      renderEditor();
      const popover = await openEditor();
      const input = await enterEditMode(popover);

      fireEvent.change(input, { target: { value: "   " } });
      fireEvent.keyDown(input, { key: "Enter" });
      fireEvent.change(input, { target: { value: "alpha" } });
      fireEvent.keyDown(input, { key: "Enter" });
      expect(axiosPatchMock).not.toHaveBeenCalled();

      fireEvent.change(input, { target: { value: "  gamma  " } });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() =>
        expect(axiosPatchMock).toHaveBeenCalledWith(
          "/tracer/project/p1/tags/",
          {
            tags: ["alpha", "gamma"],
          },
        ),
      );
      expect(axiosPatchMock).toHaveBeenCalledTimes(1);
    });

    it("blocks writes while a save is pending without disabling the focused control", async () => {
      respondWithTags(["alpha"]);
      let resolvePatch;
      axiosPatchMock.mockReturnValue(
        new Promise((resolve) => {
          resolvePatch = resolve;
        }),
      );
      renderEditor();
      const popover = await openEditor();
      const input = await enterEditMode(popover);
      const alpha = within(popover).getByRole("checkbox", { name: "alpha" });

      input.focus();
      fireEvent.change(input, { target: { value: "gamma" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() => expect(axiosPatchMock).toHaveBeenCalledTimes(1));
      expect(within(popover).getByText("Saving…")).toBeInTheDocument();

      // Pending: controls stay focusable (no `disabled`), but are inert.
      expect(input).not.toBeDisabled();
      expect(input).toHaveAttribute("aria-disabled", "true");
      expect(alpha).not.toBeDisabled();
      expect(alpha).toHaveAttribute("aria-disabled", "true");
      expect(document.activeElement).toBe(input);

      fireEvent.change(input, { target: { value: "delta" } });
      fireEvent.keyDown(input, { key: "Enter" });
      fireEvent.click(alpha);
      expect(axiosPatchMock).toHaveBeenCalledTimes(1);

      respondWithTags(["alpha", "gamma"]);
      resolvePatch({ data: { result: { tags: ["alpha", "gamma"] } } });
      await waitFor(() =>
        expect(input).not.toHaveAttribute("aria-disabled", "true"),
      );
      expect(document.activeElement).toBe(input);
    });

    it("rolls the summary back and reports a rejected write", async () => {
      respondWithTags(["alpha"]);
      axiosPatchMock.mockRejectedValue(new Error("denied"));
      renderEditor();
      const popover = await openEditor();
      const input = await enterEditMode(popover);

      fireEvent.change(input, { target: { value: "gamma" } });
      fireEvent.keyDown(input, { key: "Enter" });

      await waitFor(() =>
        expect(enqueueSnackbarMock).toHaveBeenCalledWith(
          "Failed to update tags",
          { variant: "error" },
        ),
      );
      await waitFor(() =>
        expect(
          within(popover).queryByRole("checkbox", { name: "gamma" }),
        ).not.toBeInTheDocument(),
      );
      expect(trigger()).toHaveTextContent("alpha");
      expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
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
      expect(trigger()).toHaveAttribute("aria-label", "Edit tags, 3 tags");

      const popover = await openEditor();
      expect(
        await within(popover).findByPlaceholderText("Search tags"),
      ).toBeInTheDocument();
      expect(
        within(popover).queryByRole("button", { name: "Back to tags" }),
      ).not.toBeInTheDocument();
    });

    it("does not write while the project's tags are still loading", async () => {
      axiosGetMock.mockReturnValue(new Promise(() => {}));
      fetchAllObserveProjectsMock.mockResolvedValue([
        { id: "p2", tags: ["beta"] },
      ]);
      renderEditor({ variant: "header" });

      fireEvent.click(
        await screen.findByRole("button", { name: /are loading/i }),
      );
      const popover = await screen.findByRole("presentation");
      const beta = await within(popover).findByRole("checkbox", {
        name: "beta",
      });
      expect(beta).toHaveAttribute("aria-disabled", "true");
      fireEvent.click(beta);
      expect(axiosPatchMock).not.toHaveBeenCalled();
    });
  });
});
