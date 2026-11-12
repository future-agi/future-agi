import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import AddTagsPopover from "../AddTagsPopover";

// OBS-SW-133: the Observe grids hand the popover their selection as
// bulkItems and never pass a traceId or spanId. A one-row selection must be
// edited as that row (start from its tags, save to its id), not as a trace
// with no id.

const snackbar = vi.hoisted(() => ({ calls: [] }));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => snackbar.calls.push(args),
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: {
    patch: vi.fn(() => Promise.resolve({ data: {} })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
  },
}));

const traceTagsUrl = (id) => apiPath("/tracer/trace/{id}/tags/", { id });

const renderPopover = (props) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AddTagsPopover
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
};

const addTag = async (name) => {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText("Add tag..."), `${name}{Enter}`);
};

const tagNames = (body) => body.tags.map((tag) => tag.name);

describe("AddTagsPopover with a one-row selection", () => {
  beforeEach(() => {
    snackbar.calls = [];
    axios.patch.mockClear();
    axios.post.mockClear();
  });

  it("shows the selected trace's tags and saves them with the new one", async () => {
    renderPopover({
      bulkItems: [
        { id: "trace-1", type: "trace", currentTags: ["vip", "billing"] },
      ],
    });

    expect(screen.getByText("vip")).toBeInTheDocument();
    expect(screen.getByText("billing")).toBeInTheDocument();
    await addTag("escalate");

    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(1));
    const [url, body] = axios.patch.mock.calls[0];
    expect(url).toBe(traceTagsUrl("trace-1"));
    expect(tagNames(body)).toEqual(["vip", "billing", "escalate"]);
    expect(snackbar.calls).not.toContainEqual([
      "Failed to update tags",
      { variant: "error" },
    ]);
  });

  it("saves a selected span's tags to that span", async () => {
    renderPopover({
      bulkItems: [
        {
          id: "span-1",
          type: "span",
          currentTags: [{ name: "slow", color: "#EF4444" }],
        },
      ],
    });

    await addTag("retry");

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    const [url, body] = axios.post.mock.calls[0];
    expect(url).toBe(apiPath("/tracer/observation-span/update-tags/"));
    expect(body.span_id).toBe("span-1");
    expect(tagNames(body)).toEqual(["slow", "retry"]);
    expect(axios.patch).not.toHaveBeenCalled();
  });

  it("still adds to each row's own tags when several rows are selected", async () => {
    renderPopover({
      bulkItems: [
        { id: "trace-1", type: "trace", currentTags: ["vip"] },
        { id: "trace-2", type: "trace", currentTags: [] },
      ],
    });

    await addTag("reviewed");

    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(2));
    const sent = Object.fromEntries(
      axios.patch.mock.calls.map(([url, body]) => [url, tagNames(body)]),
    );
    expect(sent).toEqual({
      [traceTagsUrl("trace-1")]: ["vip", "reviewed"],
      [traceTagsUrl("trace-2")]: ["reviewed"],
    });
  });
});
