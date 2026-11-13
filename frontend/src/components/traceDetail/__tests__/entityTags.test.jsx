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
vi.mock("../TagInput", () => ({
  default: ({ onAdd, disabled }) => (
    <button
      disabled={disabled}
      onClick={() => onAdd({ name: "reviewed", color: "#123456" })}
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

  it("uses the acknowledged revision for consecutive saves before grid refresh", async () => {
    const { result } = renderHook(() => useSaveEntityTags());
    await act(() => result.current({ entity, tags }));
    await act(() => result.current({ entity, tags: [] }));
    expect(axios.patch.mock.calls[1][1]).toEqual({
      ...target,
      expected_tag_revision: 1,
      tags: [],
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
    renderEditor({
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
  });

  it("preserves each bulk item's target/revision and reports partial failure honestly", async () => {
    axios.post.mockRejectedValue({ statusCode: 409 });
    renderEditor({
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
    expect(onSuccess).toHaveBeenCalledWith(success().data.result, entity);
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
    expect(onTagsSaved).toHaveBeenCalledWith(
      success().data.result,
      expect.objectContaining({ id: "root-1", tag_target: target }),
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
});
