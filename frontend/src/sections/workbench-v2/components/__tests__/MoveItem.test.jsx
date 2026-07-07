import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

let MoveItem;

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockTrackEvent = vi.fn();
const mockEnqueueSnackbar = vi.fn();
const mockUseParams = vi.fn(() => ({ folder: "source-folder" }));

vi.mock("react-router", () => ({
  useParams: () => mockUseParams(),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
  },
  endpoints: {
    develop: {
      runPrompt: {
        promptFolder: "/prompt-folders/",
        movePrompt: (id) => `/prompts/${id}/move/`,
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: { promptMoveConfirmed: "prompt_move_confirmed" },
  PropertyName: {
    type: "type",
    currentLocation: "currentLocation",
    newLocation: "newLocation",
  },
  trackEvent: (...args) => mockTrackEvent(...args),
}));

vi.mock("../../../../components/ModalWrapper/ModalWrapper", () => ({
  default: ({ title, children, onSubmit, isValid }) => (
    <div>
      <h1>{title}</h1>
      {children}
      <button onClick={onSubmit} disabled={!isValid}>
        Move
      </button>
    </div>
  ),
}));

vi.mock(
  "../../../../components/FromSearchSelectField/FormSearchSelectFieldState",
  () => ({
    default: ({ value, onChange, options = [], label }) => (
      <label>
        {label}
        <select
          aria-label={label}
          value={value}
          onChange={(event) => onChange(event)}
        >
          <option value="">Select folder</option>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
    ),
  }),
);

function renderMoveItem(queryClient, props = {}) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MoveItem
        id="prompt-1"
        open
        onClose={vi.fn()}
        name="Draft prompt"
        folderId="source-folder"
        {...props}
      />
    </QueryClientProvider>,
  );
}

describe("MoveItem", () => {
  beforeAll(async () => {
    MoveItem = (await import("../MoveItem")).default;
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue({
      data: {
        result: [
          { id: "source-folder", name: "Source" },
          { id: "target-folder", name: "Target" },
        ],
      },
    });
    mockPost.mockResolvedValue({ data: { ok: true } });
  });

  it("invalidates source, destination, and all folders after a successful move", async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    queryClient.setQueryData(["prompt-folders"], {
      data: {
        result: [
          { id: "source-folder", name: "Source" },
          { id: "target-folder", name: "Target" },
        ],
      },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const onClose = vi.fn();

    renderMoveItem(queryClient, { onClose });

    fireEvent.change(screen.getByLabelText("Select folder"), {
      target: { value: "target-folder" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Move" }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/prompts/prompt-1/move/", {
        prompt_folder_id: "target-folder",
      }),
    );

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["folder-items", "source-folder"],
      }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["folder-items", "target-folder"],
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["folder-items", "all"],
    });
    expect(onClose).toHaveBeenCalled();
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Prompt moved successfully",
      { variant: "success" },
    );
    expect(mockTrackEvent).toHaveBeenCalledWith("prompt_move_confirmed", {
      type: "PROMPT",
      currentLocation: "source-folder",
      newLocation: "target-folder",
    });
  });
});
