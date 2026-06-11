/* eslint-disable react/prop-types */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BottomEvaluationSection from "./BottomEvaluationSection";

const mocks = vi.hoisted(() => ({
  axiosGet: vi.fn(),
  copyToClipboard: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("@monaco-editor/react", async () => {
  const ReactModule = await import("react");
  const Editor = ReactModule.forwardRef(({ value, onMount }, ref) => {
    ReactModule.useEffect(() => {
      onMount?.(
        { updateOptions: vi.fn() },
        {
          editor: {
            defineTheme: vi.fn(),
            setTheme: vi.fn(),
          },
        },
      );
    }, [onMount]);
    return (
      <pre ref={ref} data-testid="sdk-code">
        {value}
      </pre>
    );
  });
  Editor.displayName = "MockMonacoEditor";
  return {
    Editor,
  };
});

vi.mock("src/utils/axios", () => ({
  default: {
    get: mocks.axiosGet,
  },
  endpoints: {
    develop: {
      eval: {
        evalsSDKCode: "/model-hub/eval-sdk-code/",
      },
    },
  },
}));

vi.mock("src/utils/utils", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    copyToClipboard: mocks.copyToClipboard,
  };
});

vi.mock("notistack", () => ({
  enqueueSnackbar: mocks.enqueueSnackbar,
}));

vi.mock("../../EvalDetails/EvalsLog/LogsTabGrid", () => ({
  default: () => <div data-testid="logs-tab-grid" />,
}));

function renderWithQueryClient(ui) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("BottomEvaluationSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.copyToClipboard.mockResolvedValue(true);
    mocks.axiosGet.mockResolvedValue({
      data: {
        result: {
          python: "futureagi.eval(template_id='eval-123')",
          javascript: "futureagi.eval({ templateId: 'eval-123' })",
          curl: "curl https://api.futureagi.com/eval",
        },
      },
    });
  });

  it("copies the active SDK snippet from the eval playground", async () => {
    const selectedData = {
      mapping: {
        output: "answer",
        model: "gpt-4o-mini",
      },
      source: "playground",
    };

    renderWithQueryClient(
      <BottomEvaluationSection
        evaluation={{ id: "eval-123", name: "SDK Copy Eval" }}
        selectedData={selectedData}
        setInitialLeftWidth={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Python" }));

    await screen.findByText("futureagi.eval(template_id='eval-123')");
    expect(mocks.axiosGet).toHaveBeenCalledWith(
      "/model-hub/eval-sdk-code/",
      expect.objectContaining({
        params: expect.objectContaining({
          template_id: "eval-123",
          mapping: { output: "answer" },
          source: "playground",
        }),
      }),
    );
    expect(selectedData.mapping.model).toBe("gpt-4o-mini");

    fireEvent.click(
      screen.getByRole("button", { name: "Copy Python SDK code" }),
    );

    await waitFor(() =>
      expect(mocks.copyToClipboard).toHaveBeenCalledWith(
        "futureagi.eval(template_id='eval-123')",
      ),
    );
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith("Copied to clipboard", {
      variant: "success",
    });
  });
});
