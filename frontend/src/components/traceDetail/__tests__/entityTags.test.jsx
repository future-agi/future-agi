import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import AddTagsPopover from "../AddTagsPopover";
import useSaveEntityTags, { tagUpdateErrorMessage } from "../useSaveEntityTags";
import ChatDetailsBar from "../../ChatDetailDrawerV2/ChatDetailsBar";
import CallDetailsBar from "../../VoiceDetailDrawerV2/CallDetailsBar";
import SpanDetailPane from "../SpanDetailPane";

vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    default: { ...original.default, patch: vi.fn(), post: vi.fn() },
  };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null }),
}));
// The name the mocked tag input adds next.
const nextTag = vi.hoisted(() => ({ name: "reviewed" }));
vi.mock("../TagInput", () => ({
  default: ({ onAdd, disabled }) => (
    <button
      disabled={disabled}
      onClick={() => onAdd({ name: nextTag.name, color: "#123456" })}
    >
      Add test tag
    </button>
  ),
}));
vi.mock("../TagChip", () => ({
  default: ({ name, onRemove, readOnly }) => (
    <button disabled={readOnly} onClick={onRemove}>
      {name}
    </button>
  ),
}));

const target = {
  project_id: "00000000-0000-4000-8000-000000000001",
  trace_id: "trace-1",
  span_id: "root-1",
  start_hour: "2026-11-08T10:00:00+00:00",
  observation_type: "",
  service_name: "",
  expected_start_time: "2026-11-08T10:15:20.123456+00:00",
  expected_version: "18446744073709551614",
};
const entity = {
  tag_target: target,
  tag_revision: 0,
  manual_tags: [],
  source_tag_names: ["sdk"],
  tags: [{ name: "sdk" }],
};
const tags = [{ name: "reviewed", color: "#123456" }];
const success = (revision = 1) => ({
  data: {
    result: {
      tags,
      manual_tags: tags,
      source_tag_names: ["sdk"],
      tag_revision: revision,
    },
  },
});

function renderEditor(props = {}) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AddTagsPopover
        open
        anchorEl={document.body}
        onClose={vi.fn()}
        entity={entity}
        currentTags={entity.tags}
        {...props}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  nextTag.name = "reviewed";
  axios.patch.mockResolvedValue(success());
  axios.post.mockResolvedValue(success());
});

describe("scoped entity tag writes", () => {
  it.each(["trace", "span"])(
    "sends the complete %s target verbatim and only manual tags",
    async (type) => {
      const { result } = renderHook(() => useSaveEntityTags());
      await act(() =>
        result.current({ entity, tags: [...entity.tags, ...tags], type }),
      );
      const call = (type === "span" ? axios.post : axios.patch).mock.calls[0];
      expect(call[0]).toBe(
        type === "span"
          ? "/tracer/observation-span/update-tags/"
          : "/tracer/trace/trace-1/tags/",
      );
      expect(call[1]).toEqual({ ...target, expected_tag_revision: 0, tags });
    },
  );

  it("uses the acknowledged revision when the next list is built on that save", async () => {
    const { result } = renderHook(() => useSaveEntityTags());
    let saved;
    await act(async () => {
      saved = await result.current({ entity, tags });
    });
    await act(() => result.current({ entity, tags: [], base: saved }));
    expect(axios.patch.mock.calls[1][1]).toEqual({
      ...target,
      expected_tag_revision: 1,
      tags: [],
    });
  });

  it("never pairs a list built from stale tags with a newer acknowledged revision", async () => {
    // Saved x at revision 1, but the caller's list still comes from the
    // entity as loaded (revision 0): expecting revision 1 would silently drop
    // x. Expect the entity's revision, so the server refuses it (409).
    const { result } = renderHook(() => useSaveEntityTags());
    await act(() => result.current({ entity, tags }));
    await act(() =>
      result.current({
        entity,
        tags: [...entity.tags, { name: "y" }],
      }),
    );
    expect(axios.patch.mock.calls[1][1]).toEqual({
      ...target,
      expected_tag_revision: 0,
      tags: [{ name: "y" }],
    });
  });

  it("uses a newer refetched revision and keeps projects and physical targets separate", async () => {
    const { result } = renderHook(() => useSaveEntityTags());
    await act(() => result.current({ entity, tags }));
    await act(() =>
      result.current({ entity: { ...entity, tag_revision: 7 }, tags }),
    );
    expect(axios.patch.mock.calls[1][1].expected_tag_revision).toBe(7);
    await act(() =>
      result.current({
        entity: {
          ...entity,
          tag_target: {
            ...target,
            project_id: "00000000-0000-4000-8000-000000000002",
          },
        },
        tags,
      }),
    );
    expect(axios.patch.mock.calls[2][1].expected_tag_revision).toBe(0);
  });

  it("does not guess target identity or a missing revision", async () => {
    const { result } = renderHook(() => useSaveEntityTags());
    await expect(
      result.current({ entity: { trace_id: "trace-1" }, tags }),
    ).rejects.toMatchObject({ code: "tag_scope_required" });
    await expect(
      result.current({ entity: { tag_target: target }, tags }),
    ).rejects.toMatchObject({ code: "tag_scope_required" });
    expect(axios.patch).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it.each([404, 409, 503])(
    "shows an actionable %s error without retrying or claiming success",
    async (statusCode) => {
      axios.patch.mockRejectedValue({ statusCode });
      renderEditor();
      fireEvent.click(screen.getByText("Add test tag"));
      await waitFor(() =>
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          tagUpdateErrorMessage({ statusCode }),
          { variant: "error" },
        ),
      );
      expect(axios.patch).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar).not.toHaveBeenCalledWith(
        "Tags updated",
        expect.anything(),
      );
      expect(screen.getByText("sdk")).toBeDisabled();
    },
  );

  it("tells a workspace viewer they need write access, not to retry", () => {
    expect(
      tagUpdateErrorMessage({ statusCode: 403, code: "tag_write_forbidden" }),
    ).toBe(
      "You need write access to this workspace to edit tags. Ask a workspace admin for access.",
    );
  });

  it("restores the last confirmed tags when a later edit fails before grid refresh", async () => {
    renderEditor();
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(screen.getByText("reviewed")).not.toBeDisabled(),
    );
    axios.patch.mockRejectedValueOnce({ statusCode: 503 });
    fireEvent.click(screen.getByText("reviewed"));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        tagUpdateErrorMessage({ statusCode: 503 }),
        { variant: "error" },
      ),
    );
    expect(screen.getByText("reviewed")).toBeInTheDocument();
    expect(axios.patch.mock.calls[1][1].expected_tag_revision).toBe(1);
  });

  it("edits the sole selected bulk item rather than an undefined trace", async () => {
    const onTagsSaved = vi.fn();
    renderEditor({
      onTagsSaved,
      entity: undefined,
      bulkItems: [
        { id: "span-1", type: "span", entity, currentTags: entity.tags },
      ],
    });
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        "/tracer/observation-span/update-tags/",
        { ...target, tags, expected_tag_revision: 0 },
      ),
    );
    expect(axios.patch).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(onTagsSaved).toHaveBeenCalledWith([
        { result: success().data.result, entity, type: "span" },
      ]),
    );
  });

  it("preserves each bulk item's target/revision and reports partial failure honestly", async () => {
    axios.post.mockRejectedValue({ statusCode: 409 });
    const onTagsSaved = vi.fn();
    renderEditor({
      onTagsSaved,
      bulkItems: [
        { id: "trace-1", type: "trace", entity, currentTags: entity.tags },
        {
          id: "child",
          type: "span",
          entity: {
            ...entity,
            tag_revision: 3,
            tag_target: { ...target, span_id: "child" },
          },
          currentTags: [],
        },
      ],
    });
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringContaining("Some items may have been updated."),
        { variant: "error" },
      ),
    );
    expect(axios.patch.mock.calls[0][1].span_id).toBe("root-1");
    expect(axios.post.mock.calls[0][1]).toMatchObject({
      span_id: "child",
      expected_tag_revision: 3,
    });
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      "Tags applied to 2 items",
      expect.anything(),
    );
    expect(onTagsSaved).toHaveBeenCalledWith([
      { result: success().data.result, entity, type: "trace" },
    ]);
  });

  it("builds each successive bulk add on each item's acknowledged result", async () => {
    const child = {
      ...entity,
      tag_target: { ...target, span_id: "child" },
    };
    const revisions = new Map([
      ["root-1", 0],
      ["child", 0],
    ]);
    const save = async (_url, body) => {
      const revision = revisions.get(body.span_id);
      if (body.expected_tag_revision !== revision) throw { statusCode: 409 };
      revisions.set(body.span_id, revision + 1);
      return {
        data: {
          result: {
            tags: [{ name: "sdk" }, ...body.tags],
            manual_tags: body.tags,
            source_tag_names: ["sdk"],
            tag_revision: revision + 1,
          },
        },
      };
    };
    axios.patch.mockImplementation(save);
    axios.post.mockImplementation(save);
    renderEditor({
      bulkItems: [
        { id: "root-1", type: "trace", entity, currentTags: entity.tags },
        { id: "child", type: "span", entity: child, currentTags: child.tags },
      ],
    });
    nextTag.name = "a";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Tags applied to 2 items", {
        variant: "success",
      }),
    );
    nextTag.name = "b";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(revisions.get("child")).toBe(2));
    for (const method of [axios.patch, axios.post]) {
      expect(method.mock.calls[1][1]).toMatchObject({
        expected_tag_revision: 1,
        tags: [
          { name: "a", color: "#123456" },
          { name: "b", color: "#123456" },
        ],
      });
    }
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      expect.stringContaining("changed. Refresh"),
      expect.anything(),
    );
  });

  it("retries a partially failed bulk add without losing successful revisions", async () => {
    const child = {
      ...entity,
      tag_target: { ...target, span_id: "child" },
    };
    axios.patch
      .mockResolvedValueOnce(success())
      .mockResolvedValueOnce(success(2));
    axios.post
      .mockRejectedValueOnce({ statusCode: 503 })
      .mockResolvedValueOnce(success());
    renderEditor({
      bulkItems: [
        { id: "root-1", type: "trace", entity, currentTags: entity.tags },
        { id: "child", type: "span", entity: child, currentTags: child.tags },
      ],
    });
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        expect.stringContaining("Some items may have been updated."),
        { variant: "error" },
      ),
    );
    await waitFor(() =>
      expect(screen.getByText("Add test tag")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.patch.mock.calls[1][1]).toMatchObject({
      expected_tag_revision: 1,
      tags: [{ name: "reviewed", color: "#123456" }],
    });
    expect(axios.post.mock.calls[1][1]).toMatchObject({
      expected_tag_revision: 0,
      tags: [{ name: "reviewed", color: "#123456" }],
    });
  });

  it("does not pair a stale bulk tag list with a row mutated by an inline acknowledgement", async () => {
    const liveRow = { ...entity, tags: [...entity.tags] };
    const staleTags = liveRow.tags;
    const child = {
      ...entity,
      tag_target: { ...target, span_id: "child" },
    };
    renderEditor({
      bulkItems: [
        {
          id: "root-1",
          type: "trace",
          entity: liveRow,
          currentTags: staleTags,
        },
        { id: "child", type: "span", entity: child, currentTags: child.tags },
      ],
    });
    // An inline popover closed before its response; applySavedEntityTags later
    // mutates the selected grid row without replacing the bulk snapshot.
    liveRow.tags = [{ name: "sdk" }, { name: "c" }];
    liveRow.manual_tags = [{ name: "c" }];
    liveRow.tag_revision = 1;
    nextTag.name = "a";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(1));
    const body = axios.patch.mock.calls[0][1];
    expect(
      body.expected_tag_revision === 0 || body.tags.some((t) => t.name === "c"),
    ).toBe(true);
  });

  it("does not pair a stale single-item seed with a mutated row revision", async () => {
    const liveRow = { ...entity, tags: [...entity.tags] };
    renderEditor({
      entity: undefined,
      bulkItems: [
        {
          id: "root-1",
          type: "trace",
          entity: liveRow,
          currentTags: liveRow.tags,
        },
      ],
    });
    liveRow.tags = [{ name: "sdk" }, { name: "c" }];
    liveRow.manual_tags = [{ name: "c" }];
    liveRow.tag_revision = 1;
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(1));
    const body = axios.patch.mock.calls[0][1];
    expect(
      body.expected_tag_revision === 0 || body.tags.some((t) => t.name === "c"),
    ).toBe(true);
  });

  it("bases a new bulk tag on each item's own result after a partial failure", async () => {
    const child = { ...entity, tag_target: { ...target, span_id: "child" } };
    axios.patch.mockResolvedValueOnce({
      data: {
        result: {
          tags: [{ name: "sdk" }, { name: "a", color: "#123456" }],
          manual_tags: [{ name: "a", color: "#123456" }],
          source_tag_names: ["sdk"],
          tag_revision: 1,
        },
      },
    });
    axios.post.mockRejectedValueOnce({ statusCode: 503 });
    renderEditor({
      bulkItems: [
        { id: "root-1", type: "trace", entity, currentTags: entity.tags },
        { id: "child", type: "span", entity: child, currentTags: child.tags },
      ],
    });
    nextTag.name = "a";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(screen.getByText("Add test tag")).not.toBeDisabled(),
    );
    nextTag.name = "b";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.patch.mock.calls[1][1]).toMatchObject({
      expected_tag_revision: 1,
      tags: [
        { name: "a", color: "#123456" },
        { name: "b", color: "#123456" },
      ],
    });
    expect(axios.post.mock.calls[1][1]).toMatchObject({
      expected_tag_revision: 0,
      tags: [{ name: "b", color: "#123456" }],
    });
  });

  it("reuses acknowledged per-item revisions when bulk tags close and reopen", async () => {
    const child = { ...entity, tag_target: { ...target, span_id: "child" } };
    const items = [
      { id: "root-1", type: "trace", entity, currentTags: entity.tags },
      { id: "child", type: "span", entity: child, currentTags: child.tags },
    ];
    const client = new QueryClient();
    const popover = (open) => (
      <QueryClientProvider client={client}>
        <AddTagsPopover
          open={open}
          anchorEl={document.body}
          onClose={vi.fn()}
          bulkItems={items}
        />
      </QueryClientProvider>
    );
    const response = {
      data: {
        result: {
          tags: [{ name: "sdk" }, { name: "a", color: "#123456" }],
          manual_tags: [{ name: "a", color: "#123456" }],
          source_tag_names: ["sdk"],
          tag_revision: 1,
        },
      },
    };
    axios.patch.mockResolvedValueOnce(response);
    axios.post.mockResolvedValueOnce(response);
    const { rerender } = render(popover(true));
    nextTag.name = "a";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(screen.getByText("Add test tag")).not.toBeDisabled(),
    );
    rerender(popover(false));
    rerender(popover(true));
    nextTag.name = "b";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    for (const method of [axios.patch, axios.post]) {
      expect(method.mock.calls[1][1]).toMatchObject({
        expected_tag_revision: 1,
        tags: [
          { name: "a", color: "#123456" },
          { name: "b", color: "#123456" },
        ],
      });
    }
  });

  it("keeps an earlier bulk session's late success out of the reopened selection", async () => {
    let resolveOld;
    axios.patch.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
    );
    const child = { ...entity, tag_target: { ...target, span_id: "child" } };
    const other = { ...entity, tag_target: { ...target, span_id: "other" } };
    const first = [
      { id: "root-1", type: "trace", entity, currentTags: entity.tags },
      { id: "child", type: "span", entity: child, currentTags: child.tags },
    ];
    const second = [
      { id: "other", type: "span", entity: other, currentTags: other.tags },
    ];
    const onSuccess = vi.fn();
    const onTagsSaved = vi.fn();
    const latestOnTagsSaved = vi.fn();
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const popover = (open, bulkItems, callback = onTagsSaved) => (
      <QueryClientProvider client={client}>
        <AddTagsPopover
          open={open}
          anchorEl={document.body}
          onClose={vi.fn()}
          bulkItems={bulkItems}
          onSuccess={onSuccess}
          onTagsSaved={callback}
        />
      </QueryClientProvider>
    );
    const { rerender } = render(popover(true, first));
    nextTag.name = "a";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(1));
    rerender(popover(false, first, latestOnTagsSaved));
    await act(async () => resolveOld(success()));
    await waitFor(() => expect(latestOnTagsSaved).toHaveBeenCalledOnce());
    rerender(popover(true, second, latestOnTagsSaved));
    await waitFor(() =>
      expect(screen.getByText("Add test tag")).not.toBeDisabled(),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith("Tags applied to 2 items", {
      variant: "success",
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["trace-detail"] });
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onTagsSaved).not.toHaveBeenCalled();
    expect(latestOnTagsSaved).toHaveBeenCalledWith([
      { result: success().data.result, entity, type: "trace" },
      { result: success().data.result, entity: child, type: "span" },
    ]);
    expect(screen.getByText("sdk")).toBeInTheDocument();
    expect(screen.queryByText("a")).not.toBeInTheDocument();

    // A new session's failed save must roll back to its own confirmed tags,
    // not to the old bulk session's acknowledged tags.
    axios.post.mockRejectedValueOnce({ statusCode: 503 });
    nextTag.name = "new";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(screen.getByText("Add test tag")).not.toBeDisabled(),
    );
    expect(screen.getByText("sdk")).toBeInTheDocument();
    expect(screen.queryByText("a")).not.toBeInTheDocument();
  });

  it("reports an old bulk error with bulk wording after switching to a single item", async () => {
    let rejectOld;
    axios.patch.mockReturnValueOnce(
      new Promise((_, reject) => {
        rejectOld = reject;
      }),
    );
    const child = { ...entity, tag_target: { ...target, span_id: "child" } };
    const bulk = [
      { id: "root-1", type: "trace", entity, currentTags: entity.tags },
      { id: "child", type: "span", entity: child, currentTags: child.tags },
    ];
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const onTagsSaved = vi.fn();
    const popover = (items) => (
      <QueryClientProvider client={client}>
        <AddTagsPopover
          open
          anchorEl={document.body}
          onClose={vi.fn()}
          bulkItems={items}
          onTagsSaved={onTagsSaved}
        />
      </QueryClientProvider>
    );
    const { rerender } = render(popover(bulk));
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(1));
    rerender(popover([bulk[1]]));
    await act(async () => rejectOld({ statusCode: 409 }));
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      `Some items may have been updated. ${tagUpdateErrorMessage({ statusCode: 409 })}`,
      { variant: "error" },
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["trace-detail"] });
    // The voice drawer edits tags on its own payload.
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["voiceCallDetail"] });
    expect(onTagsSaved).toHaveBeenCalledWith([
      { result: success().data.result, entity: child, type: "span" },
    ]);
    expect(screen.getByText("sdk")).toBeInTheDocument();
  });

  it("saves one shared assignment for two root spans and can add again", async () => {
    const rootA = { ...entity, parent_span_id: "", tag_target: target };
    const rootB = {
      ...entity,
      parent_span_id: null,
      tag_target: { ...target, span_id: "root-2" },
    };
    let revision = 0;
    axios.post.mockImplementation(async (_url, body) => {
      if (body.expected_tag_revision !== revision) throw { statusCode: 409 };
      revision += 1;
      return {
        data: {
          result: {
            tags: [{ name: "sdk" }, ...body.tags],
            manual_tags: body.tags,
            source_tag_names: ["sdk"],
            tag_revision: revision,
          },
        },
      };
    });
    renderEditor({
      bulkItems: [
        { id: "root-1", type: "span", entity: rootA, currentTags: rootA.tags },
        { id: "root-2", type: "span", entity: rootB, currentTags: rootB.tags },
      ],
    });
    nextTag.name = "a";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(screen.getByText("Add test tag")).not.toBeDisabled(),
    );
    nextTag.name = "b";
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(axios.post.mock.calls[1][1]).toMatchObject({
      expected_tag_revision: 1,
      tags: [
        { name: "a", color: "#123456" },
        { name: "b", color: "#123456" },
      ],
    });
    expect(revision).toBe(2);
  });

  it("uses a newer entity revision even when its tags are deep-equal", async () => {
    const client = new QueryClient();
    const popover = (selected) => (
      <QueryClientProvider client={client}>
        <AddTagsPopover
          open
          anchorEl={document.body}
          onClose={vi.fn()}
          entity={selected}
          currentTags={entity.tags}
        />
      </QueryClientProvider>
    );
    const { rerender } = render(popover(entity));
    rerender(popover({ ...entity, tag_revision: 1 }));
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(1));
    expect(axios.patch.mock.calls[0][1].expected_tag_revision).toBe(1);
  });

  it("wires the inline span editor to its physical span target", async () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <SpanDetailPane
          entry={{
            observation_span: {
              ...entity,
              id: "root-1",
              trace: "trace-1",
              name: "root",
            },
          }}
          projectId={target.project_id}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "tag" }));
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        "/tracer/observation-span/update-tags/",
        { ...target, tags, expected_tag_revision: 0 },
      ),
    );
  });

  it("hands the popover's saved result and entity to its owner", async () => {
    // The trace grid updates that row from these, instead of a list refresh
    // that would send the user back to page 1.
    const onSuccess = vi.fn();
    renderEditor({ onSuccess });
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(onSuccess).toHaveBeenCalledWith(success().data.result, {
      entity,
      type: "trace",
    });
  });

  it("reports the entity a popover save was sent for, not a later selection", async () => {
    let acknowledge;
    axios.patch.mockReturnValueOnce(
      new Promise((resolve) => {
        acknowledge = resolve;
      }),
    );
    const onSuccess = vi.fn();
    const other = {
      ...entity,
      tag_target: { ...target, trace_id: "trace-2", span_id: "root-2" },
    };
    const client = new QueryClient();
    const popover = (selected) => (
      <QueryClientProvider client={client}>
        <AddTagsPopover
          open
          anchorEl={document.body}
          onClose={vi.fn()}
          entity={selected}
          currentTags={selected.tags}
          onSuccess={onSuccess}
        />
      </QueryClientProvider>
    );
    const { rerender } = render(popover(entity));
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(1));
    rerender(popover(other));
    await act(async () => acknowledge(success()));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(onSuccess.mock.calls[0][1]).toEqual({ entity, type: "trace" });
  });

  it("tells the drawer after an inline span tag save so the grid updates the row", async () => {
    const onTagsSaved = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SpanDetailPane
          entry={{
            observation_span: {
              ...entity,
              id: "root-1",
              trace: "trace-1",
              name: "root",
            },
          }}
          projectId={target.project_id}
          onClose={vi.fn()}
          onTagsSaved={onTagsSaved}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "tag" }));
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(onTagsSaved).toHaveBeenCalledTimes(1));
    expect(onTagsSaved).toHaveBeenCalledWith(success().data.result, {
      entity: expect.objectContaining({ id: "root-1", tag_target: target }),
      type: "span",
    });
  });

  it("reports the span an inline save was sent for after the pane switches span", async () => {
    let acknowledge;
    axios.post.mockReturnValueOnce(
      new Promise((resolve) => {
        acknowledge = resolve;
      }),
    );
    const onTagsSaved = vi.fn();
    const client = new QueryClient();
    const pane = (id, spanTarget) => (
      <QueryClientProvider client={client}>
        <SpanDetailPane
          entry={{
            observation_span: {
              ...entity,
              tag_target: spanTarget,
              id,
              trace: "trace-1",
              name: id,
            },
          }}
          projectId={target.project_id}
          onClose={vi.fn()}
          onTagsSaved={onTagsSaved}
        />
      </QueryClientProvider>
    );
    const { rerender } = render(
      pane("child-a", { ...target, span_id: "child-a" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "tag" }));
    fireEvent.click(screen.getByText("Add test tag"));
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    rerender(pane("child-b", { ...target, span_id: "child-b" }));
    await act(async () => acknowledge(success()));
    await waitFor(() => expect(onTagsSaved).toHaveBeenCalledTimes(1));
    expect(onTagsSaved.mock.calls[0][1].entity.tag_target.span_id).toBe(
      "child-a",
    );
  });

  it.each([ChatDetailsBar, CallDetailsBar])(
    "wires inline call/chat tag editors to the scoped API",
    async (Component) => {
      const client = new QueryClient();
      render(
        <QueryClientProvider client={client}>
          <Component
            data={{
              ...entity,
              trace_id: "trace-1",
              project_id: target.project_id,
            }}
          />
        </QueryClientProvider>,
      );
      fireEvent.click(screen.getByRole("button", { name: "tag" }));
      fireEvent.click(screen.getByText("Add test tag"));
      await waitFor(() =>
        expect(axios.patch).toHaveBeenCalledWith(
          "/tracer/trace/trace-1/tags/",
          { ...target, tags, expected_tag_revision: 0 },
        ),
      );
    },
  );

  describe("a quick second edit before the refetch lands keeps both tags", () => {
    // The first save is acknowledged at revision 1; the owner's entity and
    // tags stay at revision 0 because the trace-detail refetch has not landed.
    const acknowledgeX = (method) =>
      method.mockResolvedValueOnce({
        data: {
          result: {
            tags: [{ name: "sdk" }, { name: "x", color: "#123456" }],
            manual_tags: [{ name: "x", color: "#123456" }],
            source_tag_names: ["sdk"],
            tag_revision: 1,
          },
        },
      });
    const both = [
      { name: "x", color: "#123456" },
      { name: "y", color: "#123456" },
    ];

    it("in the span pane's inline tag row", async () => {
      acknowledgeX(axios.post);
      render(
        <QueryClientProvider client={new QueryClient()}>
          <SpanDetailPane
            entry={{
              observation_span: {
                ...entity,
                id: "root-1",
                trace: "trace-1",
                name: "root",
              },
            }}
            projectId={target.project_id}
            onClose={vi.fn()}
          />
        </QueryClientProvider>,
      );
      nextTag.name = "x";
      fireEvent.click(screen.getByRole("button", { name: "tag" }));
      fireEvent.click(screen.getByText("Add test tag"));
      await waitFor(() => expect(screen.getByText("x")).not.toBeDisabled());
      nextTag.name = "y";
      fireEvent.click(screen.getByRole("button", { name: "tag" }));
      fireEvent.click(screen.getByText("Add test tag"));
      await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
      expect(axios.post.mock.calls[1][1]).toEqual({
        ...target,
        expected_tag_revision: 1,
        tags: both,
      });
    });

    it("in the drawer header popover reopened before the refetch", async () => {
      acknowledgeX(axios.patch);
      const client = new QueryClient();
      const popover = (open) => (
        <QueryClientProvider client={client}>
          <AddTagsPopover
            open={open}
            anchorEl={document.body}
            onClose={vi.fn()}
            entity={entity}
            currentTags={entity.tags}
          />
        </QueryClientProvider>
      );
      const { rerender } = render(popover(true));
      nextTag.name = "x";
      fireEvent.click(screen.getByText("Add test tag"));
      await waitFor(() => expect(screen.getByText("x")).not.toBeDisabled());
      rerender(popover(false));
      rerender(popover(true));
      expect(screen.getByText("x")).toBeInTheDocument();
      nextTag.name = "y";
      fireEvent.click(screen.getByText("Add test tag"));
      await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(2));
      expect(axios.patch.mock.calls[1][1]).toEqual({
        ...target,
        expected_tag_revision: 1,
        tags: both,
      });
    });

    it.each([
      ["chat", ChatDetailsBar],
      ["voice", CallDetailsBar],
    ])("in the %s drawer's inline tag row", async (_kind, Bar) => {
      acknowledgeX(axios.patch);
      // The refetch never lands: the drawer keeps the row it was opened with.
      const data = {
        ...entity,
        trace_id: "trace-1",
        id: "trace-1",
        tags: entity.tags,
      };
      render(
        <QueryClientProvider client={new QueryClient()}>
          <Bar data={data} onAction={vi.fn()} />
        </QueryClientProvider>,
      );
      nextTag.name = "x";
      fireEvent.click(screen.getByRole("button", { name: "tag" }));
      fireEvent.click(screen.getByText("Add test tag"));
      await waitFor(() => expect(screen.getByText("x")).not.toBeDisabled());
      nextTag.name = "y";
      fireEvent.click(screen.getByRole("button", { name: "tag" }));
      fireEvent.click(screen.getByText("Add test tag"));
      await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(2));
      expect(axios.patch.mock.calls[1][1]).toEqual({
        ...target,
        expected_tag_revision: 1,
        tags: both,
      });
    });
  });

  describe("an editor moved to another entity and back keeps each one's save", () => {
    // A and B are saved in turn (each acknowledged at revision 1) while the
    // owner's data stays at revision 0; back on A, its edit builds on A's save.
    const spanA = { ...entity, tag_target: { ...target, span_id: "span-a" } };
    const spanB = {
      ...entity,
      tag_target: { ...target, trace_id: "trace-2", span_id: "span-b" },
    };
    const acknowledge = (method, name) =>
      method.mockResolvedValueOnce({
        data: {
          result: {
            tags: [{ name: "sdk" }, { name, color: "#123456" }],
            manual_tags: [{ name, color: "#123456" }],
            source_tag_names: ["sdk"],
            tag_revision: 1,
          },
        },
      });

    it("in the span pane's inline tag row", async () => {
      acknowledge(axios.post, "a1");
      acknowledge(axios.post, "b1");
      const client = new QueryClient();
      const pane = (span) => (
        <QueryClientProvider client={client}>
          <SpanDetailPane
            entry={{
              observation_span: {
                ...span,
                id: span.tag_target.span_id,
                trace: "trace-1",
                name: span.tag_target.span_id,
              },
            }}
            projectId={target.project_id}
            onClose={vi.fn()}
          />
        </QueryClientProvider>
      );
      const add = async (name, calls) => {
        nextTag.name = name;
        fireEvent.click(screen.getByRole("button", { name: "tag" }));
        fireEvent.click(screen.getByText("Add test tag"));
        await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(calls));
      };
      const { rerender } = render(pane(spanA));
      await add("a1", 1);
      await waitFor(() => expect(screen.getByText("a1")).not.toBeDisabled());
      rerender(pane(spanB));
      await add("b1", 2);
      await waitFor(() => expect(screen.getByText("b1")).not.toBeDisabled());
      rerender(pane(spanA));
      expect(screen.getByText("a1")).toBeInTheDocument();
      await add("a2", 3);
      expect(axios.post.mock.calls[2][1]).toEqual({
        ...target,
        span_id: "span-a",
        expected_tag_revision: 1,
        tags: [
          { name: "a1", color: "#123456" },
          { name: "a2", color: "#123456" },
        ],
      });
    });

    it("in the drawer header popover", async () => {
      acknowledge(axios.patch, "a1");
      acknowledge(axios.patch, "b1");
      const client = new QueryClient();
      const popover = (selected) => (
        <QueryClientProvider client={client}>
          <AddTagsPopover
            open
            anchorEl={document.body}
            onClose={vi.fn()}
            entity={selected}
            currentTags={selected.tags}
          />
        </QueryClientProvider>
      );
      const add = async (name, calls) => {
        nextTag.name = name;
        fireEvent.click(screen.getByText("Add test tag"));
        await waitFor(() => expect(axios.patch).toHaveBeenCalledTimes(calls));
        await waitFor(() =>
          expect(screen.getByText("Add test tag")).not.toBeDisabled(),
        );
      };
      const { rerender } = render(popover(spanA));
      await add("a1", 1);
      rerender(popover(spanB));
      await add("b1", 2);
      rerender(popover(spanA));
      expect(screen.getByText("a1")).toBeInTheDocument();
      await add("a2", 3);
      expect(axios.patch.mock.calls[2][1]).toEqual({
        ...target,
        span_id: "span-a",
        expected_tag_revision: 1,
        tags: [
          { name: "a1", color: "#123456" },
          { name: "a2", color: "#123456" },
        ],
      });
    });
  });
});
