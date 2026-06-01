import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const {
  mockGet,
  mockPost,
  mockEnqueue,
  mockTrackEvent,
  mockRefreshGrid,
  storeRef,
} = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockEnqueue: vi.fn(),
  mockTrackEvent: vi.fn(),
  mockRefreshGrid: vi.fn(),
  storeRef: { hook: null },
}));

// Mock the develop-detail states module so we don't pull in its
// localStorage-at-module-load zustand slices.
vi.mock("../../../states", async () => {
  const { create: makeStore } = await import("zustand");
  const hook = makeStore((set) => ({
    addDevelopEvalFeedbackTarget: null,
    setAddDevelopEvalFeedbackTarget: (value) =>
      set(() => ({ addDevelopEvalFeedbackTarget: value })),
  }));
  storeRef.hook = hook;
  return { useAddDevelopEvalFeedbackStore: hook };
});

import AddDevelopEvalFeedbackDrawer from "../AddDevelopEvalFeedbackDrawer";

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
  },
  endpoints: {
    develop: {
      eval: {
        getFeedbackDetails: "/develop/eval/get-feedback-details",
        getFeedbackTemplate: "/develop/eval/get-feedback-template",
        addFeedback: "/develop/eval/add-feedback",
        updateFeedback: "/develop/eval/update-feedback",
      },
      experiment: {
        feedback: {
          getDetails: (id) => `/develop/experiment/${id}/feedback/details`,
          getTemplate: (id) => `/develop/experiment/${id}/feedback/template`,
          create: (id) => `/develop/experiment/${id}/feedback/create`,
          submit: (id) => `/develop/experiment/${id}/feedback/submit`,
        },
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueue(...args),
}));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: (...args) => mockTrackEvent(...args),
  Events: { datasetSubmitFeedbackClicked: "dataset_submit_feedback_clicked" },
  PropertyName: {
    datasetId: "dataset_id",
    evalId: "eval_id",
    rowIdentifier: "row_identifier",
  },
}));

vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return {
    ...actual,
    useParams: () => ({
      dataset: "ds-1",
      experimentId: "exp-1",
    }),
  };
});

vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: mockRefreshGrid }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, sx: _sx, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

function renderWithQuery(ui) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

// Snake_case target — matches the BE wire shape every dispatcher into the
// useAddDevelopEvalFeedbackStore now sends (CustomCellRender.jsx:215,
// ExperimentDetailDrawerContent.jsx:1048, DatapointDrawer/V2:778/974).
const DATASET_TARGET = {
  id: "eval-1",
  source_id: "metric-1",
  name: "Context Adherence",
  value_infos: { reason: "the verdict was wrong" },
  row_data: { row_id: "row-1" },
};

const EXPERIMENT_TARGET = {
  id: "eval-1",
  source_id: "evalsource-1",
  user_eval_metric_id: "metric-exp-1",
  name: "Toxicity",
  value_infos: { reason: "judged incorrectly" },
  row_data: { row_id: "row-9" },
};

describe("AddDevelopEvalFeedbackDrawer — dataset module", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url) => {
      if (url.includes("get-feedback-details")) {
        return Promise.resolve({ data: { result: { feedback: [] } } });
      }
      if (url.includes("get-feedback-template")) {
        return Promise.resolve({
          data: { result: { output_type: "reason", choices: [] } },
        });
      }
      return Promise.resolve({ data: { result: null } });
    });
    mockPost.mockImplementation((url) => {
      if (url === "/develop/eval/add-feedback") {
        return Promise.resolve({ data: { result: { id: "fb-new-1" } } });
      }
      return Promise.resolve({ data: { result: { ok: true } } });
    });
    // Seed the store with a target so the drawer opens.
    storeRef.hook
      .getState()
      .setAddDevelopEvalFeedbackTarget(DATASET_TARGET);
  });

  it("opens the drawer with the target name and fetches details + template from dataset endpoints", async () => {
    renderWithQuery(<AddDevelopEvalFeedbackDrawer />);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        "/develop/eval/get-feedback-details",
        { params: { user_eval_metric_id: "metric-1", row_id: "row-1" } },
      );
      expect(mockGet).toHaveBeenCalledWith(
        "/develop/eval/get-feedback-template",
        { params: { user_eval_metric_id: "metric-1" } },
      );
    });
    expect(await screen.findByText("Context Adherence")).toBeInTheDocument();
  });

  it("submits stage 1 to /add-feedback with source=dataset payload + analytics + refreshGrid", async () => {
    const user = userEvent.setup();
    renderWithQuery(<AddDevelopEvalFeedbackDrawer />);

    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Improve the tone and grammar/i),
      ).toBeInTheDocument();
    });

    await user.type(
      screen.getByPlaceholderText(/Improve the tone and grammar/i),
      "right value",
    );
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "tighter rubric",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/develop/eval/add-feedback",
        expect.objectContaining({
          value: "right value",
          explanation: "tighter rubric",
          user_eval_metric: "metric-1",
          source: "dataset",
          source_id: "eval-1",
          row_id: "row-1",
        }),
      );
    });
    expect(mockTrackEvent).toHaveBeenCalledWith(
      "dataset_submit_feedback_clicked",
      expect.objectContaining({
        dataset_id: "ds-1",
        eval_id: "metric-1",
        row_identifier: "row-1",
      }),
    );
    expect(mockRefreshGrid).toHaveBeenCalled();
  });

  it("renders radio options when BE template returns snake_case output_type='Pass/Fail'", async () => {
    // BE wire shape — develop_dataset.py:get_template emits `output_type`
    // (snake_case), no camelization layer. Wrapper must normalize so the
    // Pass/Fail branch in EvalFeedbackEntryStage actually fires.
    mockGet.mockImplementation((url) => {
      if (url.includes("get-feedback-details")) {
        return Promise.resolve({ data: { result: { feedback: [] } } });
      }
      if (url.includes("get-feedback-template")) {
        return Promise.resolve({
          data: {
            result: { output_type: "Pass/Fail", choices: ["Passed", "Failed"] },
          },
        });
      }
      return Promise.resolve({ data: { result: null } });
    });

    renderWithQuery(<AddDevelopEvalFeedbackDrawer />);

    await waitFor(() => {
      expect(screen.getByLabelText("Passed")).toBeInTheDocument();
      expect(screen.getByLabelText("Failed")).toBeInTheDocument();
    });
  });

  it("renders the eval reason from target.value_infos.reason", async () => {
    // Locks the read path: entry stage reads `target.value_infos.reason`
    // verbatim because the BE row has no camelCase mirror and no FE-side
    // normalization layer is in the way.
    renderWithQuery(<AddDevelopEvalFeedbackDrawer />);

    await waitFor(() => {
      expect(
        screen.getByText("the verdict was wrong"),
      ).toBeInTheDocument();
    });
  });

  it("pre-fills entry form from BE existing-feedback with snake_case action_type", async () => {
    mockGet.mockImplementation((url) => {
      if (url.includes("get-feedback-details")) {
        return Promise.resolve({
          data: {
            result: {
              feedback: [
                {
                  id: "fb-existing-1",
                  value: "the right value",
                  comment: "needs more strictness",
                  action_type: "retune",
                },
              ],
            },
          },
        });
      }
      if (url.includes("get-feedback-template")) {
        return Promise.resolve({
          data: { result: { output_type: "reason", choices: [] } },
        });
      }
      return Promise.resolve({ data: { result: null } });
    });

    renderWithQuery(<AddDevelopEvalFeedbackDrawer />);

    await waitFor(() => {
      expect(screen.getByDisplayValue("the right value")).toBeInTheDocument();
      expect(
        screen.getByDisplayValue("needs more strictness"),
      ).toBeInTheDocument();
    });
  });

  it("offers the dataset BE handler's three accepted action_type values", async () => {
    // Locks the FE-BE contract for the dataset surface:
    // develop_dataset.py:10971 valid_actions = [retune, recalculate_row,
    // recalculate_dataset, retune_recalculate]. The wrapper picks three of
    // those four (drops the canonical retune_recalculate — its semantic
    // overlaps recalculate_dataset on the dataset BE's else-branch). TH-5604
    // PR B canonicalizes this handler; after that ships, this wrapper
    // switches to ACTION_TYPES (canonical) and this test asserts canonical
    // values instead.
    renderWithQuery(<AddDevelopEvalFeedbackDrawer />);

    // Stage 1 → submit so stage 2 renders.
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Improve the tone and grammar/i),
      ).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.type(
      screen.getByPlaceholderText(/Improve the tone and grammar/i),
      "right value",
    );
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "tighter rubric",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    // Stage 2 — verify the three radio inputs' values directly.
    await waitFor(() => {
      expect(screen.getByText("Re-tune")).toBeInTheDocument();
    });
    const radios = screen.getAllByRole("radio");
    const values = radios.map((r) => r.getAttribute("value")).sort();
    expect(values).toEqual(
      ["recalculate_dataset", "recalculate_row", "retune"].sort(),
    );
  });

  it("submits stage 2 to /update-feedback with action_type + feedback_id", async () => {
    const user = userEvent.setup();
    renderWithQuery(<AddDevelopEvalFeedbackDrawer />);

    // Stage 1
    await waitFor(() => {
      expect(
        screen.getByPlaceholderText(/Improve the tone and grammar/i),
      ).toBeInTheDocument();
    });
    await user.type(
      screen.getByPlaceholderText(/Improve the tone and grammar/i),
      "right value",
    );
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "tighter rubric",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    // Stage 2
    await waitFor(() => {
      expect(screen.getByText("Re-tune")).toBeInTheDocument();
    });
    await user.click(screen.getByText("Re-tune"));
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/develop/eval/update-feedback",
        expect.objectContaining({
          action_type: "retune",
          user_eval_metric_id: "metric-1",
          feedback_id: "fb-new-1",
        }),
      );
    });
  });
});

describe("AddDevelopEvalFeedbackDrawer — experiment module", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url) => {
      if (url.endsWith("/feedback/details")) {
        return Promise.resolve({ data: { result: { feedback: [] } } });
      }
      if (url.endsWith("/feedback/template")) {
        return Promise.resolve({
          data: { result: { output_type: "reason", choices: [] } },
        });
      }
      return Promise.resolve({ data: { result: null } });
    });
    mockPost.mockImplementation((url) => {
      if (url.endsWith("/feedback/create")) {
        return Promise.resolve({ data: { result: { id: "fb-exp-1" } } });
      }
      return Promise.resolve({ data: { result: { ok: true } } });
    });
    storeRef.hook
      .getState()
      .setAddDevelopEvalFeedbackTarget(EXPERIMENT_TARGET);
  });

  it("hits experiment-scoped endpoints with source=experiment payload", async () => {
    const user = userEvent.setup();
    renderWithQuery(
      <AddDevelopEvalFeedbackDrawer
        module="experiment"
        onRefreshGrid={mockRefreshGrid}
      />,
    );

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        "/develop/experiment/exp-1/feedback/details",
        { params: { user_eval_metric_id: "metric-exp-1", row_id: "row-9" } },
      );
      expect(mockGet).toHaveBeenCalledWith(
        "/develop/experiment/exp-1/feedback/template",
        { params: { user_eval_metric_id: "metric-exp-1" } },
      );
    });

    await screen.findByPlaceholderText(/Improve the tone and grammar/i);

    await user.type(
      screen.getByPlaceholderText(/Improve the tone and grammar/i),
      "right",
    );
    await user.type(
      screen.getByPlaceholderText(
        /Enter what would you like to improve in the prompt/i,
      ),
      "explain",
    );
    await user.click(screen.getByRole("button", { name: /Submit feedback/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/develop/experiment/exp-1/feedback/create",
        expect.objectContaining({
          source: "experiment",
          source_id: "evalsource-1",
          user_eval_metric: "metric-exp-1",
          row_id: "row-9",
        }),
      );
    });
  });
});
