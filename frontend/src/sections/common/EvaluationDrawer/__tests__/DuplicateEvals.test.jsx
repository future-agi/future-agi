import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DuplicateEvals from "../DuplicateEvals";

// Mock axios
vi.mock("src/utils/axios", () => {
  const post = vi.fn();
  return {
    default: { post },
    endpoints: {
      develop: {
        eval: {
          duplicateEvalsTemplate: "/model-hub/duplicate-eval-template/",
        },
      },
    },
    __mockPost: post,
  };
});

// Mock notistack
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Import mocked axios
import { __mockPost as mockAxiosPost } from "src/utils/axios";

const renderDuplicateEvals = (props = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  const defaultProps = {
    open: true,
    onClose: vi.fn(),
    evalId: "test-eval-id-123",
    onSubmit: vi.fn(),
    ...props,
  };

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <DuplicateEvals {...defaultProps} />
    </QueryClientProvider>,
  );

  return { ...utils, ...defaultProps };
};

describe("DuplicateEvals", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the dialog when open", () => {
    renderDuplicateEvals();
    expect(screen.getByText("Duplicate evaluation")).toBeInTheDocument();
  });

  it("does not render when closed", () => {
    renderDuplicateEvals({ open: false });
    expect(screen.queryByText("Duplicate evaluation")).not.toBeInTheDocument();
  });

  it("sends eval_template_id (snake_case) in the payload, not evalTemplateId (camelCase)", async () => {
    const user = userEvent.setup();
    mockAxiosPost.mockResolvedValueOnce({
      data: { result: { id: "new-eval-id" } },
    });

    const onSubmit = vi.fn();
    renderDuplicateEvals({ onSubmit });

    // Type a name
    const nameInput = screen.getByPlaceholderText("Enter evaluation name");
    await user.type(nameInput, "my-duplicate-eval");

    // Click duplicate
    const duplicateButton = screen.getByText("Duplicate");
    await user.click(duplicateButton);

    await waitFor(() => {
      expect(mockAxiosPost).toHaveBeenCalled();
    });

    // Verify the payload uses snake_case
    const [url, payload] = mockAxiosPost.mock.calls[0];
    expect(url).toBe("/model-hub/duplicate-eval-template/");
    expect(payload).toHaveProperty("eval_template_id", "test-eval-id-123");
    expect(payload).toHaveProperty("name", "my-duplicate-eval");
    expect(payload).not.toHaveProperty("evalTemplateId");
  });

  it("transforms the name to lowercase with underscores", async () => {
    const user = userEvent.setup();
    mockAxiosPost.mockResolvedValueOnce({
      data: { result: { id: "new-eval-id" } },
    });

    renderDuplicateEvals();

    const nameInput = screen.getByPlaceholderText("Enter evaluation name");
    await user.type(nameInput, "My Test Eval");

    // The onChange handler transforms the value
    expect(nameInput.value).toBe("my_test_eval");
  });

  it("calls onClose when cancel is clicked", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderDuplicateEvals({ onClose });

    const cancelButton = screen.getByText("Cancel");
    await user.click(cancelButton);

    expect(onClose).toHaveBeenCalled();
  });
});
