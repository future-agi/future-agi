import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, waitFor, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { boundObserveListRow } from "src/sections/projects/LLMTracing/observeListPayload";
import AddTagsPopover from "../AddTagsPopover";
import SpanDetailPane from "../SpanDetailPane";
import useSaveEntityTags, { useLatestEntityTags } from "../useSaveEntityTags";
import ChatDetailsBar from "../../ChatDetailDrawerV2/ChatDetailsBar";
import CallDetailsBar from "../../VoiceDetailDrawerV2/CallDetailsBar";
import type {
  EditableTagEntity,
  EntityTag,
  EntityTagInput,
  ManualTagState,
  TagTarget,
} from "../entityTagState";

interface TestRow extends EditableTagEntity {
  id: string;
  name: string;
  trace_id: string;
  tags: EntityTag[];
}

type SaveBody = TagTarget & {
  expected_tag_revision: number;
  tags: EntityTag[];
};
type SaveResponse = { data: { result: TestRow } };
type SaveRequest = (url: string, body: SaveBody) => Promise<SaveResponse>;
const patch = vi.mocked<SaveRequest>(axios.patch);
const post = vi.mocked<SaveRequest>(axios.post);

vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal<typeof import("src/utils/axios")>();
  return {
    ...original,
    default: { ...original.default, patch: vi.fn(), post: vi.fn() },
  };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null }),
}));
const nextTag = vi.hoisted(() => ({ name: "new" }));
vi.mock("../TagInput", () => ({
  default: ({
    onAdd,
    disabled,
  }: {
    onAdd: (tag: EntityTag) => void;
    disabled?: boolean;
  }) => (
    <button disabled={disabled} onClick={() => onAdd({ name: nextTag.name })}>
      Add test tag
    </button>
  ),
}));
vi.mock("../TagChip", () => ({
  default: ({
    name,
    onRemove,
    readOnly,
  }: {
    name: string;
    onRemove?: React.MouseEventHandler<HTMLButtonElement>;
    readOnly?: boolean;
  }) => (
    <button disabled={readOnly} onClick={onRemove}>
      {name}
    </button>
  ),
}));

const target = (span = "root-a", trace = "trace-1") => ({
  project_id: "00000000-0000-4000-8000-000000000001",
  trace_id: trace,
  span_id: span,
  start_hour: "2026-11-08T10:00:00+00:00",
  observation_type: "span",
  service_name: "svc",
  expected_start_time: "2026-11-08T10:15:20.123456+00:00",
  expected_version: "18446744073709551614",
});
const row = (
  span: string,
  sources: string[],
  manual: string[] = [],
  revision = 0,
  trace = "trace-1",
): TestRow => ({
  id: span,
  name: span,
  trace_id: trace,
  parent_span_id: "",
  tag_target: target(span, trace),
  tag_revision: revision,
  tag_assignment_exists: revision > 0,
  source_tag_names: sources,
  manual_tags: manual.map((name) => ({ name })),
  tags: [...sources, ...manual].map((name) => ({ name })),
});
const names = (tags: EntityTag[]) => tags.map((tag) => tag.name);

function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Expected test fixture value");
  return value;
}

// A replacement/CAS server, not an echo-only API mock: reads are reconstructed
// from the stored assignment plus the requested physical root's SDK tags.
function serverFor(rows: TestRow[]) {
  const stored = new Map<string, Omit<ManualTagState, "tag_assignment_exists"> & {
    tag_assignment_exists: boolean;
  }>();
  rows.forEach((entity) =>
    stored.set(entity.trace_id, {
      manual_tags: entity.manual_tags,
      tag_revision: entity.tag_revision,
      tag_assignment_exists: entity.tag_assignment_exists,
    }),
  );
  const read = (entity: TestRow): TestRow => {
    const assignment = required(stored.get(entity.trace_id));
    return {
      ...entity,
      ...assignment,
      tags: [
        ...entity.source_tag_names.map((name) => ({ name })),
        ...assignment.manual_tags,
      ],
    };
  };
  const save: SaveRequest = async (_url, body) => {
    const entity = required(
      rows.find((item) => item.tag_target.span_id === body.span_id),
    );
    const previous = required(stored.get(entity.trace_id));
    if (body.expected_tag_revision !== previous.tag_revision)
      throw { statusCode: 409 };
    stored.set(entity.trace_id, {
      manual_tags: body.tags.map((tag) => ({ ...tag })),
      tag_revision: previous.tag_revision + 1,
      tag_assignment_exists: true,
    });
    return { data: { result: read(entity) } };
  };
  patch.mockImplementation(save);
  post.mockImplementation(save);
  return { stored, read, save };
}

function editor(kind = "popover") {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const view = (
    entity: EntityTagInput,
    extra: Partial<React.ComponentProps<typeof AddTagsPopover>> = {},
  ) => (
    <QueryClientProvider client={client}>
      {kind === "pane" ? (
        <SpanDetailPane
          entry={{ observation_span: entity }}
          projectId={required(entity.tag_target).project_id as string}
          onClose={vi.fn()}
        />
      ) : (
        <AddTagsPopover
          open
          anchorEl={document.body}
          onClose={vi.fn()}
          entity={entity}
          currentTags={Array.isArray(entity.tags) ? entity.tags : []}
          {...extra}
        />
      )}
    </QueryClientProvider>
  );
  const add = (name: string) => {
    nextTag.name = name;
    if (kind === "pane")
      fireEvent.click(screen.getByRole("button", { name: "tag" }));
    fireEvent.click(screen.getByText("Add test tag"));
  };
  return { view, add, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  nextTag.name = "new";
});

describe("authoritative tag state independent of list previews", () => {
  const sdk = Array.from(
    { length: 80 },
    (_, i) => `${String(i).padStart(3, "0")}-${"s".repeat(196)}`,
  );

  it.each(["trace", "span"])(
    "preserves stored manual tags from an actually bounded %s row",
    async (type) => {
      const original = row("root-a", sdk, ["reviewed"]);
      const server = serverFor([original]);
      const bounded = boundObserveListRow(original);
      expect(typeof bounded.tags).toBe("string");
      expect(bounded.source_tag_names).toEqual(sdk);
      expect(bounded.manual_tags).toEqual([{ name: "reviewed" }]);
      const { view, add } = editor();
      render(view(bounded, type === "span" ? { spanId: original.id } : {}));
      add("new");
      const method = type === "span" ? post : patch;
      await waitFor(() => expect(method).toHaveBeenCalledTimes(1));
      expect(names(method.mock.calls[0][1].tags)).toEqual(["reviewed", "new"]);
      expect(names(required(server.stored.get("trace-1")).manual_tags)).toEqual(
        ["reviewed", "new"],
      );
      expect(names(server.read(original).manual_tags)).toEqual([
        "reviewed",
        "new",
      ]);
      expect(names(server.read(original).tags)).toEqual([
        ...sdk,
        "reviewed",
        "new",
      ]);
    },
  );

  it("preserves each bulk target's own manual tags across bounded previews", async () => {
    const a = row("root-a", sdk, ["reviewed-a"]);
    const b = row("root-b", sdk, ["reviewed-b"], 5, "trace-2");
    const server = serverFor([a, b]);
    const { view, add } = editor();
    render(
      view(a, {
        bulkItems: [a, b].map((entity) => ({
          id: entity.id,
          type: "span",
          entity: boundObserveListRow(entity),
        })),
      }),
    );
    add("new");
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(
      post.mock.calls.map(([, body]) => ({
        span: body.span_id,
        revision: body.expected_tag_revision,
        tags: names(body.tags),
      })),
    ).toEqual([
      { span: "root-a", revision: 0, tags: ["reviewed-a", "new"] },
      { span: "root-b", revision: 5, tags: ["reviewed-b", "new"] },
    ]);
    expect(names(server.read(a).manual_tags)).toEqual(["reviewed-a", "new"]);
    expect(names(server.read(b).manual_tags)).toEqual(["reviewed-b", "new"]);
  });
});

describe("shared manual assignment with physical-root source tags", () => {
  it.each(["pane", "popover"])(
    "keeps root B's SDK tags after saving A in the %s",
    async (kind) => {
      const a = row("root-a", ["sdk-a"]);
      const b = row("root-b", ["sdk-b"]);
      const server = serverFor([a, b]);
      const { view, add } = editor(kind);
      const { rerender } = render(view(a));
      add("reviewed");
      await waitFor(() =>
        expect(screen.getByText("reviewed")).not.toBeDisabled(),
      );
      rerender(view(b));
      expect(screen.queryByText("sdk-a")).not.toBeInTheDocument();
      expect(screen.getByText("sdk-b")).toBeDisabled();
      expect(screen.getByText("reviewed")).toBeInTheDocument();
      add("new");
      const method = kind === "pane" ? post : patch;
      await waitFor(() =>
        expect(names(server.read(b).manual_tags)).toEqual(["reviewed", "new"]),
      );
      expect(required(method.mock.calls.at(-1))[1]).toMatchObject({
        span_id: "root-b",
        expected_tag_revision: 1,
      });
      expect(names(required(method.mock.calls.at(-1))[1].tags)).toEqual([
        "reviewed",
        "new",
      ]);
      rerender(view(a));
      expect(screen.getByText("sdk-a")).toBeDisabled();
      expect(screen.queryByText("sdk-b")).not.toBeInTheDocument();
      expect(screen.getByText("new")).toBeInTheDocument();
    },
  );

  it("never uses a cached sibling's source names to filter a save", async () => {
    const a = row("root-a", ["sdk-a"]);
    const b = row("root-b", ["sdk-b"]);
    const server = serverFor([a, b]);
    const { result, rerender } = renderHook(
      ({ entity }) => ({
        latest: useLatestEntityTags(entity, entity.tags),
        save: useSaveEntityTags(),
      }),
      { initialProps: { entity: a } },
    );
    await act(async () => {
      const saved = await result.current.save({
        entity: a,
        base: undefined,
        tags: [{ name: "reviewed" }],
        type: "span",
      });
      result.current.latest.confirm(saved, a);
    });
    rerender({ entity: b });
    await act(() =>
      result.current.save({
        entity: b,
        type: "span",
        base: result.current.latest.base,
        tags: [...result.current.latest.tags, { name: "sdk-b" }],
      }),
    );
    expect(names(post.mock.calls[1][1].tags)).toEqual(["reviewed"]);
    expect(names(server.read(b).manual_tags)).toEqual(["reviewed"]);
  });
});

describe("response/refetch ordering and deletions", () => {
  it.each(["pane", "popover"])(
    "recombines a late A response with the selected B in the %s",
    async (kind) => {
      const a = row("root-a", ["sdk-a"]);
      const b = row("root-b", ["sdk-b"]);
      const server = serverFor([a, b]);
      const method = kind === "pane" ? post : patch;
      let acknowledge: (() => void) | undefined;
      method.mockImplementationOnce(async (...args) => {
        const response = await server.save(...args);
        return new Promise<SaveResponse>((resolve) => {
          acknowledge = () => resolve(response);
        });
      });
      const { view, add } = editor(kind);
      const { rerender } = render(view(a));
      add("reviewed");
      await waitFor(() => expect(acknowledge).toBeTypeOf("function"));
      rerender(view(b));
      await act(async () => required(acknowledge)());
      await waitFor(() =>
        expect(screen.getByText("reviewed")).not.toBeDisabled(),
      );
      expect(screen.getByText("sdk-b")).toBeDisabled();
      expect(screen.queryByText("sdk-a")).not.toBeInTheDocument();
      add("new");
      await waitFor(() =>
        expect(names(server.read(b).manual_tags)).toEqual(["reviewed", "new"]),
      );
      expect(method.mock.calls[1][1]).toMatchObject({
        span_id: "root-b",
        expected_tag_revision: 1,
      });
    },
  );

  it.each(["pane", "popover"])(
    "does not let a late response undo a newer refetch in the %s",
    async (kind) => {
      const a = row("root-a", ["sdk-a"]);
      const b = row("root-b", ["sdk-b"]);
      const server = serverFor([a, b]);
      const method = kind === "pane" ? post : patch;
      let acknowledge: (() => void) | undefined;
      method.mockImplementationOnce(async (...args) => {
        const response = await server.save(...args);
        return new Promise<SaveResponse>((resolve) => {
          acknowledge = () => resolve(response);
        });
      });
      const { view, add } = editor(kind);
      const { rerender } = render(view(a));
      add("reviewed");
      await waitFor(() => expect(acknowledge).toBeTypeOf("function"));
      // Another writer replaces reviewed while A's acknowledged response is delayed.
      server.stored.set("trace-1", {
        manual_tags: [{ name: "remote" }],
        tag_assignment_exists: true,
        tag_revision: 2,
      });
      rerender(view(server.read(b)));
      expect(screen.getByText("remote")).toBeInTheDocument();
      await act(async () => required(acknowledge)());
      expect(screen.queryByText("reviewed")).not.toBeInTheDocument();
      expect(screen.queryByText("sdk-a")).not.toBeInTheDocument();
      expect(screen.getByText("sdk-b")).toBeDisabled();
      rerender(view(a)); // An older physical row must retain the shared watermark.
      expect(screen.getByText("remote")).toBeInTheDocument();
      expect(screen.queryByText("sdk-b")).not.toBeInTheDocument();
      add("new");
      await waitFor(() =>
        expect(names(server.read(a).manual_tags)).toEqual(["remote", "new"]),
      );
      expect(method.mock.calls[1][1].expected_tag_revision).toBe(2);
    },
  );

  it.each(["pane", "popover"])(
    "preserves an acknowledged deletion and handles 409 without resurrection in the %s",
    async (kind) => {
      const a = row("root-a", ["sdk-a"], ["reviewed"], 1);
      const b = row("root-b", ["sdk-b"], ["reviewed"], 1);
      const server = serverFor([a, b]);
      const method = kind === "pane" ? post : patch;
      const { view, add, client } = editor(kind);
      const invalidate = vi.spyOn(client, "invalidateQueries");
      const { rerender } = render(view(a));
      fireEvent.click(screen.getByText("reviewed"));
      await waitFor(() => expect(method).toHaveBeenCalledTimes(1));
      await waitFor(() =>
        expect(screen.queryByText("reviewed")).not.toBeInTheDocument(),
      );
      expect(method.mock.calls[0][1].tags).toEqual([]);
      expect(server.read(a).manual_tags).toEqual([]);
      rerender(view(b));
      expect(screen.queryByText("reviewed")).not.toBeInTheDocument();
      expect(screen.getByText("sdk-b")).toBeDisabled();
      server.stored.set("trace-1", {
        manual_tags: [{ name: "remote" }],
        tag_assignment_exists: true,
        tag_revision: 3,
      });
      add("conflicting");
      await waitFor(() =>
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          expect.stringContaining("Refresh and review the latest tags"),
          { variant: "error" },
        ),
      );
      expect(method).toHaveBeenCalledTimes(2);
      expect(method.mock.calls[1][1].expected_tag_revision).toBe(2);
      expect(names(server.read(b).manual_tags)).toEqual(["remote"]);
      expect(screen.queryByText("conflicting")).not.toBeInTheDocument();
      expect(screen.queryByText("reviewed")).not.toBeInTheDocument();
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["trace-detail"] });
      rerender(view(server.read(b)));
      add("new");
      await waitFor(() =>
        expect(names(server.read(b).manual_tags)).toEqual(["remote", "new"]),
      );
      expect(method.mock.calls[2][1].expected_tag_revision).toBe(3);
    },
  );
});

describe("incomplete editing metadata fails closed", () => {
  const incomplete: [string, EntityTagInput][] = [
    ["absent target", { tag_target: undefined }],
    ["null target", { tag_target: null }],
    ["absent revision", { tag_revision: undefined }],
    ["absent source names", { source_tag_names: undefined }],
    ["truncated source names", { source_tag_names: '["sdk"…' }],
    ["absent manual tags", { manual_tags: undefined }],
    ["truncated manual tags", { manual_tags: '[{"name":"reviewed"}…' }],
  ];

  it.each(incomplete)(
    "disables a popover with %s and refuses direct saves",
    async (_label, overrides) => {
      const original = row("root-a", ["sdk"], ["reviewed"]);
      const entity = { ...original, ...overrides };
      const server = serverFor([original]);
      const { view } = editor();
      render(view(entity));
      expect(screen.getByText("Add test tag")).toBeDisabled();
      expect(screen.getByText("reviewed")).toBeDisabled();
      expect(
        screen.getByText(/load complete tags before editing/),
      ).toBeInTheDocument();
      const { result } = renderHook(() => useSaveEntityTags());
      await expect(
        result.current({ entity, base: undefined, tags: [{ name: "new" }] }),
      ).rejects.toMatchObject({ code: "tag_scope_required" });
      expect(patch).not.toHaveBeenCalled();
      expect(names(server.read(original).manual_tags)).toEqual(["reviewed"]);
    },
  );

  it("fails closed when the actual row bound truncates source or manual metadata", () => {
    const hugeNames = Array.from(
      { length: 100 },
      (_, i) => `${i}-${"s".repeat(200)}`,
    );
    const sourceBounded = boundObserveListRow(row("root-a", hugeNames));
    const manualBounded = boundObserveListRow(row("root-b", [], hugeNames));
    expect(typeof sourceBounded.source_tag_names).toBe("string");
    expect(typeof manualBounded.manual_tags).toBe("string");
    const { view } = editor();
    const { rerender } = render(view(sourceBounded));
    expect(screen.getByText("Add test tag")).toBeDisabled();
    rerender(view(manualBounded));
    expect(screen.getByText("Add test tag")).toBeDisabled();
    expect(patch).not.toHaveBeenCalled();
  });

  it("reseeds when complete metadata arrives without a display-tag change", async () => {
    const original = row("root-a", ["sdk"], ["reviewed"]);
    const server = serverFor([original]);
    const displayTags = original.tags;
    const { view, add } = editor();
    const { rerender } = render(
      view(
        { ...original, manual_tags: undefined },
        { currentTags: displayTags },
      ),
    );
    expect(screen.getByText("Add test tag")).toBeDisabled();
    rerender(view(original, { currentTags: displayTags }));
    expect(screen.getByText("Add test tag")).not.toBeDisabled();
    add("new");
    await waitFor(() =>
      expect(names(server.read(original).manual_tags)).toEqual([
        "reviewed",
        "new",
      ]),
    );
    expect(patch.mock.calls[0][1].expected_tag_revision).toBe(0);
  });

  it("does not partially write a bulk selection with incomplete metadata", () => {
    const a = row("root-a", ["sdk-a"], ["reviewed"]);
    const b = { ...row("root-b", ["sdk-b"]), manual_tags: undefined };
    const { view, add } = editor();
    render(
      view(a, {
        bulkItems: [a, b].map((entity) => ({
          id: entity.id,
          entity,
          type: "span",
        })),
      }),
    );
    expect(screen.getByText("Add test tag")).toBeDisabled();
    add("new");
    expect(post).not.toHaveBeenCalled();
  });
});

it.each([
  ["chat", ChatDetailsBar],
  ["voice", CallDetailsBar],
])(
  "fails closed for a linked simulation %s call without authoritative tag metadata",
  (_kind, Bar) => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Bar
          data={{
            module: "simulate",
            trace_id: "trace-1",
            tags: [{ name: "reviewed" }],
          }}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByText("reviewed")).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "tag" }),
    ).not.toBeInTheDocument();
    expect(patch).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  },
);

// `manual_tags` is the backend's complete replacement-editable set: legacy
// labels before the first assignment, and the assignment thereafter. It is
// never recovered by subtracting SDK names from the bounded display preview.
describe("explicit legacy editing contract", () => {
  it.each(["popover", "pane"])(
    "preserves rich legacy labels and SDK overlap through the real %s editor",
    async (kind) => {
      const legacy = [
        { name: "A", color: "#123456" },
        { name: "B", color: "#abcdef" },
        { name: "sdk-overlap", color: "#112233" },
      ];
      const original = {
        ...row("root-a", ["sdk-only", "sdk-overlap"]),
        tag_assignment_exists: false,
        manual_tags: legacy,
        tags: [{ name: "sdk-only" }, ...legacy],
      };
      const server = serverFor([original]);
      const { view, add } = editor(kind);
      render(view(original));
      add("C");
      const method = kind === "pane" ? post : patch;
      await waitFor(() => expect(method).toHaveBeenCalledTimes(1));
      expect(method.mock.calls[0][1].tags).toEqual([
        legacy[2], legacy[0], legacy[1], { name: "C" },
      ]);
      expect(method.mock.calls[0][1].expected_tag_revision).toBe(0);
      expect(server.read(original).manual_tags).toEqual([
        legacy[2], legacy[0], legacy[1], { name: "C" },
      ]);
    },
  );

  it("requires an explicit assignment-presence discriminator even for revision zero", async () => {
    const original = row("root-a", ["sdk"], ["A"]);
    const entity = { ...original, tag_assignment_exists: undefined };
    const { view } = editor();
    render(view(entity));
    expect(screen.getByText("Add test tag")).toBeDisabled();
    const { result } = renderHook(() => useSaveEntityTags());
    await expect(result.current({ entity, tags: [{ name: "C" }], base: undefined }))
      .rejects.toMatchObject({ code: "tag_scope_required" });
    expect(patch).not.toHaveBeenCalled();
  });

  it("does not resurrect legacy labels after an explicitly empty assignment", async () => {
    const original = {
      ...row("root-a", ["sdk"], [], 2),
      tag_assignment_exists: true,
      // A stale preview is never an editing authority.
      tags: [{ name: "sdk" }, { name: "A" }, { name: "B" }],
    };
    const server = serverFor([original]);
    const { view, add } = editor();
    render(view(original));
    expect(screen.queryByText("A")).not.toBeInTheDocument();
    expect(screen.queryByText("B")).not.toBeInTheDocument();
    add("C");
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][1].tags).toEqual([{ name: "C" }]);
    expect(server.read(original).manual_tags).toEqual([{ name: "C" }]);
  });
});
