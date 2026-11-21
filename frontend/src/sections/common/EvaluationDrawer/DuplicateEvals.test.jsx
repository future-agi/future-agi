import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { handleError } from "src/utils/queryErrorHandler";
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

const DEFAULT_PROPS = {
  open: true,
  onClose: vi.fn(),
  evalId: "11111111-1111-1111-1111-111111111111",
  onSubmit: vi.fn(),
};

function renderComponent(props = {}) {
  // Same app-level MutationCache handler as src/app.jsx, so the tests see
  // exactly the error toasts a user would.
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

    // Fill in the evaluation name.
    const input = screen.getByPlaceholderText("Enter evaluation name");
    await user.type(input, "my_copy");

    // Submit the form.
    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    // Verify the request was sent to the correct endpoint with snake_case keys.
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

    // Wait for the mutation to resolve, then check the callback.
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

    // The onChange handler should have transformed the value.
    expect(input).toHaveValue("my_special_eval_");

    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    expect(mockPost).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ name: "my_special_eval_" }),
    );
  });

  // axios is mocked wholesale, so the rejected values below are the shapes
  // the real axios response interceptor rejects with.
  it("shows an error toast and keeps the dialog open when the request fails without a response", async () => {
    mockPost.mockRejectedValueOnce({
      message: "Network Error",
      statusCode: undefined,
      transportCode: "ERR_NETWORK",
    });

    const onClose = vi.fn();
    const onSubmit = vi.fn();
    const user = userEvent.setup();
    renderComponent({ onClose, onSubmit });

    const input = screen.getByPlaceholderText("Enter evaluation name");
    await user.type(input, "my_copy");
    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Failed to duplicate evaluation",
        expect.objectContaining({ variant: "error" }),
      );
    });
    // The dialog stays open with the typed name so the user can retry.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /duplicate/i })).toBeEnabled();
    });
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText("Duplicate evaluation")).toBeInTheDocument();
    expect(input).toHaveValue("my_copy");
  });

  it("shows the backend message in a single error toast when the server rejects the duplicate", async () => {
    // Envelope from GeneralMethods.bad_request (tfc/utils/api_errors.py).
    mockPost.mockRejectedValueOnce({
      status: false,
      code: "invalid",
      message: "Evaluation template with this name already exists.",
      result: "Evaluation template with this name already exists.",
      statusCode: 400,
      transportCode: "ERR_BAD_REQUEST",
    });

    const onClose = vi.fn();
    const user = userEvent.setup();
    renderComponent({ onClose });

    await user.type(
      screen.getByPlaceholderText("Enter evaluation name"),
      "my_copy",
    );
    await user.click(screen.getByRole("button", { name: /duplicate/i }));

    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Evaluation template with this name already exists.",
        expect.objectContaining({ variant: "error" }),
      );
    });
    // Every onError handler has run once the mutation has settled.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /duplicate/i })).toBeEnabled();
    });
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });
});
