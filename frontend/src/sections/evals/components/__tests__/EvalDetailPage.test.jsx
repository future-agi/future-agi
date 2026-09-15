import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EvalDetailPage from "../EvalDetailPage";

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());
const axiosPutMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => axiosGetMock(...args),
    post: (...args) => axiosPostMock(...args),
    put: (...args) => axiosPutMock(...args),
  },
  endpoints: {
    develop: {
      eval: {
        getEvalDetail: (id) => `/model-hub/eval-templates/${id}/detail/`,
        getEvalVersions: (id) => `/model-hub/eval-templates/${id}/versions/`,
        updateEvalTemplate: (id) => `/model-hub/eval-templates/${id}/update/`,
        evalPlayground: "/model-hub/eval-playground/",
        getEvalLogs: "/model-hub/get-eval-logs",
        getCompositeDetail: (id) =>
          `/model-hub/eval-templates/${id}/composite/`,
        createEvalVersion: (id) =>
          `/model-hub/eval-templates/${id}/versions/create/`,
      },
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
    useFeatureLocked: () => ({ locked: false, isLoading: false }),
    useCapabilities: () => ({ data: undefined, isLoading: false }),
  };
});

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

// TestPlayground's Custom-tab JSON editor is Monaco-based — swap for a
// plain textarea, same as CustomJsonInput.test.jsx / TestPlayground.test.jsx.
vi.mock("../CodeEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      data-testid="json-editor"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

const { capturedProps } = vi.hoisted(() => ({
  capturedProps: { llmPrompt: null, outputType: null },
}));

vi.mock("../LLMPromptEditor", () => ({
  default: (props) => {
    capturedProps.llmPrompt = props;
    return <div data-testid="llm-prompt-editor" />;
  },
}));

vi.mock("../InstructionEditor", () => ({
  default: () => <div data-testid="instruction-editor" />,
}));

vi.mock("../OutputTypeConfig", () => ({
  default: (props) => {
    capturedProps.outputType = props;
    return <div data-testid="output-type-config" />;
  },
}));

vi.mock("../FewShotExamples", () => ({
  default: () => <div />,
}));

vi.mock("../CodeEvalEditor", () => ({
  default: () => <div />,
}));

vi.mock("../CompositeDetailPanel", () => ({
  default: () => <div />,
}));

const evalDetail = (overrides = {}) => ({
  id: "tpl-1",
  name: "Toxicity Check",
  owner: "system",
  eval_type: "llm",
  output_type: "percentage",
  instructions: "Check the following text for toxicity: {{input}}",
  config: {},
  ...overrides,
});

function mockDetail(data) {
  axiosGetMock.mockImplementation((url) => {
    if (url.includes("/versions/")) {
      return Promise.resolve({ data: { result: { versions: [] } } });
    }
    if (url.includes("/detail/")) {
      return Promise.resolve({ data: { result: data } });
    }
    return Promise.resolve({ data: { result: {} } });
  });
}

function renderDetail(evalId = "tpl-1") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/dashboard/evaluations/${evalId}`]}>
        <Routes>
          <Route
            path="/dashboard/evaluations/:evalId"
            element={<EvalDetailPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("EvalDetailPage", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({ data: { result: {} } });
    axiosPutMock.mockReset();
    axiosPutMock.mockResolvedValue({ data: { result: {} } });
    capturedProps.llmPrompt = null;
    capturedProps.outputType = null;
  });

  it("disables Test Evaluation when the instructions have no template variable", async () => {
    mockDetail(
      evalDetail({ instructions: "Check the following text for toxicity" }),
    );
    renderDetail();

    const button = await screen.findByRole("button", {
      name: "Test Evaluation",
    });
    // Stays disabled — there's no {{var}} in the instructions to map.
    await waitFor(() => expect(button).toBeDisabled());
  });

  it("enables Test Evaluation once the instructions contain a template variable", async () => {
    mockDetail(evalDetail());
    renderDetail();

    const button = await screen.findByRole("button", {
      name: "Test Evaluation",
    });
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it("shows Running... then Test completed after a successful run", async () => {
    mockDetail(evalDetail());
    axiosPostMock.mockImplementation((url) => {
      if (String(url).includes("eval-playground")) {
        return Promise.resolve({
          data: {
            status: true,
            result: {
              output: "Passed",
              output_type: "Pass/Fail",
              reason: "Looks fine",
            },
          },
        });
      }
      return Promise.resolve({ data: { result: {} } });
    });
    renderDetail();

    const button = await screen.findByRole("button", {
      name: "Test Evaluation",
    });
    await waitFor(() => expect(button).not.toBeDisabled());

    fireEvent.click(button);

    expect(
      await screen.findByRole("button", { name: "Running..." }),
    ).toBeInTheDocument();

    expect(await screen.findByText("Test completed")).toBeInTheDocument();
  });

  it("populates instructions/model/output-type state from the fetched template on mount", async () => {
    mockDetail(
      evalDetail({
        output_type: "percentage",
        config: { model: "gpt-4o" },
        instructions: "Rate this: {{input}}",
      }),
    );
    renderDetail();

    await screen.findByTestId("llm-prompt-editor");

    await waitFor(() => expect(capturedProps.llmPrompt?.model).toBe("gpt-4o"));
    expect(
      capturedProps.llmPrompt?.messages?.some((m) =>
        m.content?.includes("Rate this"),
      ),
    ).toBe(true);
    await waitFor(() =>
      expect(capturedProps.outputType?.outputType).toBe("percentage"),
    );
  });
});
