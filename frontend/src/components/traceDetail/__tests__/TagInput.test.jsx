import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import TagInput from "../TagInput";
import AddTagsPopover from "../AddTagsPopover";
import { hashColor } from "../tagUtils";

// Existing tag names come from /tracer/dashboard/filter_values/ through
// useDashboardFilterValues. Mock that read with a controllable vocabulary and
// record every call, so the tests can check the exact request it describes.
const { vocabulary, filterValuesSpy } = vi.hoisted(() => ({
  vocabulary: { current: [] },
  filterValuesSpy: vi.fn(),
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: (args) => {
    filterValuesSpy(args);
    return {
      data: args.enabled ? vocabulary.current : [],
      isLoading: false,
      isError: false,
    };
  },
}));

// The same vocabulary the trace filter's Tag field reads.
const TAG_VOCABULARY_REQUEST = {
  metricName: "tag",
  metricType: "system_metric",
  source: "traces",
};

const NO_TAGS = [];

const renderTagInput = (props = {}) => {
  const onAdd = vi.fn();
  const user = userEvent.setup();
  render(<TagInput onAdd={onAdd} projectId="project-1" {...props} />);
  const input = screen.getByRole("combobox", { name: "Tag name" });
  return { onAdd, user, input };
};

describe("TagInput suggestions", () => {
  beforeEach(() => {
    vocabulary.current = ["production", "prod-eu", "staging"];
    filterValuesSpy.mockClear();
  });

  it("suggests the project's existing tags that match what is typed", async () => {
    const { user, input } = renderTagInput({ existingNames: ["prod-eu"] });

    await user.type(input, "PROD");

    expect(
      await screen.findByRole("option", { name: "production" }),
    ).toBeInTheDocument();
    // A tag already on this trace or span is not suggested again, and names
    // that don't match the typed text are left out.
    expect(
      screen.queryByRole("option", { name: "prod-eu" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "staging" }),
    ).not.toBeInTheDocument();
    expect(filterValuesSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        ...TAG_VOCABULARY_REQUEST,
        projectIds: ["project-1"],
        search: "PROD",
        enabled: true,
      }),
    );
  });

  it("adds the exact existing name when a suggestion is clicked", async () => {
    const { user, input, onAdd } = renderTagInput();

    await user.type(input, "Prod");
    await user.click(await screen.findByRole("option", { name: "production" }));

    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd).toHaveBeenCalledWith({
      name: "production",
      color: hashColor("production"),
    });
    expect(input).toHaveValue("");
  });

  it("picks a suggestion with the keyboard", async () => {
    vocabulary.current = ["production", "staging"];
    const { user, input, onAdd } = renderTagInput();

    await user.type(input, "prod");
    await screen.findByRole("option", { name: "production" });
    await user.keyboard("{ArrowDown}{Enter}");

    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd).toHaveBeenCalledWith({
      name: "production",
      color: hashColor("production"),
    });
  });

  it("still adds the typed text as a new tag while suggestions are showing", async () => {
    const { user, input, onAdd } = renderTagInput();

    await user.type(input, "prod");
    await screen.findByRole("option", { name: "production" });
    await user.keyboard("{Enter}");

    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd).toHaveBeenCalledWith({
      name: "prod",
      color: hashColor("prod"),
    });
  });

  it("adds a brand-new tag when nothing matches", async () => {
    const { user, input, onAdd } = renderTagInput();

    await user.type(input, "brand-new{Enter}");

    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(onAdd).toHaveBeenCalledWith({
      name: "brand-new",
      color: hashColor("brand-new"),
    });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("keeps working without suggestions when there is no project", async () => {
    const { user, input, onAdd } = renderTagInput({ projectId: undefined });

    await user.type(input, "prod");
    // Wait past the search debounce, when a read would otherwise start.
    await waitFor(() =>
      expect(filterValuesSpy).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "prod" }),
      ),
    );

    expect(
      filterValuesSpy.mock.calls.every(([args]) => args.enabled === false),
    ).toBe(true);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await user.keyboard("{Enter}");
    expect(onAdd).toHaveBeenCalledWith({
      name: "prod",
      color: hashColor("prod"),
    });
  });
});

describe("AddTagsPopover tag suggestions", () => {
  beforeEach(() => {
    vocabulary.current = ["production"];
    filterValuesSpy.mockClear();
  });

  it("reads suggestions for the project it is given", async () => {
    const user = userEvent.setup();
    const anchor = document.createElement("div");
    document.body.appendChild(anchor);

    render(
      <QueryClientProvider client={new QueryClient()}>
        <AddTagsPopover
          open
          anchorEl={anchor}
          onClose={() => {}}
          traceId="trace-1"
          projectId="project-1"
          currentTags={NO_TAGS}
        />
      </QueryClientProvider>,
    );

    await user.type(
      screen.getByRole("combobox", { name: "Tag name" }),
      "prod",
    );

    expect(
      await screen.findByRole("option", { name: "production" }),
    ).toBeInTheDocument();
    expect(filterValuesSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        ...TAG_VOCABULARY_REQUEST,
        projectIds: ["project-1"],
        enabled: true,
      }),
    );
  });
});
