import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import DuplicateEvals from "./DuplicateEvals";

const mockPost = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    post: (...args) => mockPost(...args),
  },
  endpoints: {
    develop: {
      eval: {
        duplicateEvalsTemplate: "/model-hub/duplicate-eval-template/",
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Mirror app.jsx's global mutation error handler so the test exercises the
// same double-snackbar surface that production users hit.
const handleError = (error, _variable, _context, mutation) => {
  if (mutation?.options?.meta?.errorHandled) return;
  if (error?.result) {
    enqueueSnackbar(String(error.result), { variant: "error" });
  }
};

const DEFAULT_PROPS = {
  open: true,
  onClose: vi.fn(),
  evalId: "11111111-1111-1111-1111-111111111111",
  onSubmit: vi.fn(),
};

function renderComponent(props = {}) {
  const queryClient = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DuplicateEvals {...DEFAULT_PROPS} {...props} />
    </QueryClientProvider>,
  );
}

describe("DuplicateEvals", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the dialog when open", () => {
    renderComponent();
    expect(screen.getByText("Duplicate evaluation")).toBeInTheDocument();
  });

  it("does not render the dialog when closed", () => {
    renderComponent({ open: false });
    expect(screen.queryByText("Duplicate evaluation")).not.toBeInTheDocument();
  });

  it("sends eval_template_id (snake_case) in the POST payload", async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        status: true,
        result: {
          message: "Evaluation template duplicated successfully",
          eval_template_id: "22222222-2222-2222-2222-222222222222",
        },
      },
    });

    const user = userEvent.setup();
    renderComponent();

    const input = screen.getByPlaceholderText("Enter evaluation name");
    await user.type(input, "my_copy");

    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    expect(mockPost).toHaveBeenCalledTimes(1);
    expect(mockPost).toHaveBeenCalledWith(
      "/model-hub/duplicate-eval-template/",
      {
        name: "my_copy",
        eval_template_id: DEFAULT_PROPS.evalId,
      },
    );
  });

  it("calls onSubmit with the result on success", async () => {
    const resultData = {
      message: "Evaluation template duplicated successfully",
      eval_template_id: "33333333-3333-3333-3333-333333333333",
    };
    mockPost.mockResolvedValueOnce({
      data: { status: true, result: resultData },
    });

    const onSubmit = vi.fn();
    const user = userEvent.setup();
    renderComponent({ onSubmit });

    const input = screen.getByPlaceholderText("Enter evaluation name");
    await user.type(input, "my-copy");
    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    await vi.waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(resultData);
    });
  });

  it("lowercases the name and replaces invalid characters", async () => {
    mockPost.mockResolvedValueOnce({
      data: { status: true, result: { eval_template_id: "some-id" } },
    });

    const user = userEvent.setup();
    renderComponent();

    const input = screen.getByPlaceholderText("Enter evaluation name");
    await user.type(input, "My Special Eval!");

    expect(input).toHaveValue("my_special_eval_");

    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    expect(mockPost).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ name: "my_special_eval_" }),
    );
  });

  it("shows exactly one error snackbar and keeps the dialog open when the mutation fails", async () => {
    const backendMessage =
      "error duplicating the eval template evaluation template with this name already exists";
    mockPost.mockRejectedValueOnce({
      result: backendMessage,
    });

    const user = userEvent.setup();
    renderComponent();

    await user.type(
      screen.getByPlaceholderText("Enter evaluation name"),
      "my-copy",
    );
    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    await vi.waitFor(() => {
      // meta: { errorHandled: true } suppresses the global handler, so the
      // snackbar fires exactly once from the local onError.
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar).toHaveBeenCalledWith(backendMessage, {
        variant: "error",
      });
    });

    // Dialog stays open so the user can retry.
    expect(screen.getByText("Duplicate evaluation")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Enter evaluation name")).toHaveValue(
      "my-copy",
    );
  });

  it("falls back to error.message when error.result is not a string", async () => {
    // error.result is typically a string from build_error_envelope, but can be
    // an object when the backend attaches an error_code (e.g. usage-limit or
    // structured login responses).
    mockPost.mockRejectedValueOnce({
      result: { eval_template_id: ["This field is required."] },
      message: "Something went wrong",
    });

    const user = userEvent.setup();
    renderComponent();

    await user.type(
      screen.getByPlaceholderText("Enter evaluation name"),
      "test",
    );
    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    await vi.waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar).toHaveBeenCalledWith("Something went wrong", {
        variant: "error",
      });
    });
  });

  it("falls back to a default message when neither result nor message is present", async () => {
    mockPost.mockRejectedValueOnce({});

    const user = userEvent.setup();
    renderComponent();

    await user.type(
      screen.getByPlaceholderText("Enter evaluation name"),
      "test",
    );
    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    await vi.waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Failed to duplicate evaluation",
        { variant: "error" },
      );
    });
  });
});
