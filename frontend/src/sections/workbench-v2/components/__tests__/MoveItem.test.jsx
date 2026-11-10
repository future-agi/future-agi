import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  within,
  fireEvent,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import MoveItem from "../MoveItem";

// Issue #1068: after "Move", the destination folder (and, from "All prompts",
// the prompt's real source folder) kept serving its pre-move cached list.
const FOLDER_A = "a0000000-0000-4000-8000-00000000000a";
const FOLDER_B = "b0000000-0000-4000-8000-00000000000b";
const PROMPT_ID = "c0000000-0000-4000-8000-00000000000c";

// Raw GET /model-hub/prompt-folders/ response; MoveItem's `select` maps it.
const FOLDERS_RESPONSE = {
  data: {
    result: [
      { id: FOLDER_A, name: "Folder A" },
      { id: FOLDER_B, name: "Folder B" },
    ],
  },
};

const SORT_CONFIG = { field: "updated_at", direction: "desc" };
// Same shape FolderListView uses for its list query.
const folderItemsKey = (folder) => [
  "folder-items",
  folder,
  1,
  10,
  "",
  SORT_CONFIG,
];

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  enqueueSnackbar: vi.fn(),
  params: { folder: "all" },
}));

vi.mock("react-router", async (importActual) => {
  const actual = await importActual();
  return {
    ...actual,
    useParams: () => mocks.params,
  };
});

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post, get: mocks.get },
  endpoints: {
    develop: {
      runPrompt: {
        promptFolder: "/model-hub/prompt-folders/",
        movePrompt: (id) =>
          `/model-hub/prompt-templates/${id}/save-prompt-folder/`,
      },
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: mocks.enqueueSnackbar }));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));

vi.mock("src/components/ModalWrapper/ModalWrapper", async () => {
  const { default: PropTypes } = await import("prop-types");
  const MockModalWrapper = ({
    open,
    title,
    actionBtnTitle,
    isValid,
    onSubmit,
    children,
  }) =>
    open ? (
      <div role="dialog" aria-label={title}>
        {children}
        <button type="button" disabled={!isValid} onClick={onSubmit}>
          {actionBtnTitle}
        </button>
      </div>
    ) : null;
  MockModalWrapper.propTypes = {
    open: PropTypes.bool,
    title: PropTypes.string,
    actionBtnTitle: PropTypes.string,
    isValid: PropTypes.bool,
    onSubmit: PropTypes.func,
    children: PropTypes.node,
  };
  return { default: MockModalWrapper };
});

vi.mock(
  "src/components/FromSearchSelectField/FormSearchSelectFieldState",
  async () => {
    const { default: PropTypes } = await import("prop-types");
    const MockSelect = ({ label, value, options, onChange }) => (
      <select aria-label={label} value={value} onChange={onChange}>
        <option value="">Select Option</option>
        {(options ?? []).map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
    MockSelect.propTypes = {
      label: PropTypes.string,
      value: PropTypes.string,
      options: PropTypes.array,
      onChange: PropTypes.func,
    };
    return { default: MockSelect };
  },
);

function renderMove({ folder, folderId }) {
  mocks.params = { folder };
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  queryClient.setQueryData(["prompt-folders"], FOLDERS_RESPONSE);
  [FOLDER_A, FOLDER_B, "all"].forEach((f) =>
    queryClient.setQueryData(folderItemsKey(f), {
      data: { results: [], count: 0, total_pages: 1 },
    }),
  );
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <MoveItem
        id={PROMPT_ID}
        open
        onClose={onClose}
        name="Support triage"
        folderId={folderId}
      />
    </QueryClientProvider>,
  );
  return { queryClient, onClose };
}

const isInvalidated = (queryClient, folder) =>
  queryClient.getQueryState(folderItemsKey(folder))?.isInvalidated === true;

function moveTo(folderId) {
  fireEvent.change(screen.getByLabelText("Select folder"), {
    target: { value: folderId },
  });
  fireEvent.click(screen.getByRole("button", { name: "Move" }));
}

describe("MoveItem — refresh folder lists after a move (#1068)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.post.mockResolvedValue({ data: { status: true } });
    mocks.get.mockResolvedValue(FOLDERS_RESPONSE);
  });

  it("refreshes the destination folder when moving from inside a folder", async () => {
    const { queryClient } = renderMove({
      folder: FOLDER_A,
      folderId: FOLDER_A,
    });

    moveTo(FOLDER_B);

    await waitFor(() =>
      expect(isInvalidated(queryClient, FOLDER_B)).toBe(true),
    );
    expect(isInvalidated(queryClient, FOLDER_A)).toBe(true);
    expect(isInvalidated(queryClient, "all")).toBe(true);
  });

  it("refreshes the prompt's real source folder when moving from All prompts", async () => {
    const { queryClient } = renderMove({ folder: "all", folderId: FOLDER_A });

    moveTo(FOLDER_B);

    await waitFor(() =>
      expect(isInvalidated(queryClient, FOLDER_A)).toBe(true),
    );
    expect(isInvalidated(queryClient, FOLDER_B)).toBe(true);
    expect(isInvalidated(queryClient, "all")).toBe(true);
  });

  it("does not offer the prompt's current folder as a destination from All prompts", () => {
    renderMove({ folder: "all", folderId: FOLDER_A });

    const select = screen.getByLabelText("Select folder");
    expect(
      within(select).queryByRole("option", { name: "Folder A" }),
    ).not.toBeInTheDocument();
    expect(
      within(select).getByRole("option", { name: "Folder B" }),
    ).toBeInTheDocument();
  });

  it("still sends the move, shows the success toast and closes the modal", async () => {
    const { onClose } = renderMove({
      folder: FOLDER_A,
      folderId: FOLDER_A,
    });

    moveTo(FOLDER_B);

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mocks.post).toHaveBeenCalledWith(
      `/model-hub/prompt-templates/${PROMPT_ID}/save-prompt-folder/`,
      { prompt_folder_id: FOLDER_B },
    );
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
      "Prompt moved successfully",
      { variant: "success" },
    );
  });
});
