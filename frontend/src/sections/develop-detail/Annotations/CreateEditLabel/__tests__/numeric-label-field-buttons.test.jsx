// The number field in an annotate form has − and + buttons beside its slider.
// A press changes the value the form saves, not only the number shown, and
// moves one step the way the server checks a number label: by step_size,
// counted from the label's minimum (0 when it has none), within min and max.
// At either end the button is off and a press changes nothing.
import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import SelectedNodeContext from "src/components/traceDetailDrawer/selectedNodeContext";
import NumericLabelField from "../LabelFields/NumericLabelField";

const mocks = vi.hoisted(() => ({ params: {} }));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => mocks.params,
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  readQuery: vi.fn(() => Promise.resolve({ data: { result: {} } })),
  fetcher: vi.fn(),
  createQueryString: vi.fn(() => ""),
  endpoints: {
    project: {
      getAnnotationLabels: () => "/model-hub/annotations-labels/",
      addAnnotationValuesForSpan: () => "/tracer/add-annotation-values/",
      getAnnotationsForSpanId: () => "/tracer/span-annotations/",
      deleteLabel: () => "/tracer/delete-annotation-label/",
      getTrace: (id) => `/tracer/trace/${id}/`,
      getTraceIdByIndex: () => "/tracer/trace/get_trace_id_by_index/",
      getTraceIdByIndexObserve: () => "/tracer/trace/observe-index/",
      getTraceIdByIndexSpansAsBase: () => "/tracer/span/index/",
      getTraceIdByIndexSpansAsObserve: () => "/tracer/span/observe-index/",
      getObservationSpan: (id) => `/tracer/observation-span/${id}/`,
    },
  },
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "user-1" } }),
}));

vi.mock("src/components/traceDetailDrawer/common", () => ({
  useTraceErrorAnalysis: () => ({ data: undefined, isPending: true }),
}));

vi.mock("src/components/traceDetailDrawer/drawer-bottom", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/trace-tree", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/drawer-right", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/ErrorAnalysis", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/addToDataset/add-dataset", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/AnnotationSidebarContent", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/AddLabelDrawer", () => ({
  default: () => null,
}));
vi.mock("../CreateEditLabel", () => ({ default: () => null }));

// The label settings drawer is where Annotate is opened from; here it is only
// the button that opens the annotate drawer.
vi.mock("src/components/traceDetailDrawer/add-annotations-drawer", () => ({
  default: ({ onAnnotateClick }) => (
    <button type="button" onClick={onAnnotateClick}>
      Open annotate form
    </button>
  ),
}));

import TraceDetailDrawer from "src/components/traceDetailDrawer/trace-detail-drawer";

// A number label stepping by 2 from 1: the server accepts 1, 3, 5, 7 and 9.
const ODD_STEPS = {
  min: 1,
  max: 9,
  step_size: 2,
  display_type: "slider",
};

// ---------------------------------------------------------------------------
// The field in a form of its own

function NumberForm({ settings, value, onSave }) {
  const { control, handleSubmit } = useForm({
    defaultValues: { score: value },
  });
  return (
    <>
      <NumericLabelField
        control={control}
        fieldName="score"
        label="Score"
        settings={settings}
      />
      <button type="button" onClick={handleSubmit(onSave)}>
        Save
      </button>
    </>
  );
}

NumberForm.propTypes = {
  settings: PropTypes.object.isRequired,
  value: PropTypes.number,
  onSave: PropTypes.func.isRequired,
};

// The − and + buttons are the two buttons beside the slider.
const stepButtons = () => {
  let box = screen.getByRole("slider");
  while (box && box.querySelectorAll("button").length < 2) {
    box = box.parentElement;
  }
  const [minus, plus] = box.querySelectorAll("button");
  return { minus, plus };
};

// The number shown between − and +.
const shownNumber = () => stepButtons().minus.nextElementSibling.textContent;

const press = async (button) => {
  await act(async () => {
    fireEvent.click(button);
  });
};

// A browser moves a focused range input one step on an arrow key the way
// stepUp does, then tells the page the value changed.
const pressArrowRight = async () => {
  const slider = screen.getByRole("slider");
  await act(async () => {
    slider.focus();
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    slider.stepUp();
    fireEvent.input(slider);
    fireEvent.change(slider);
  });
};

const openForm = (settings, value) => {
  const onSave = vi.fn();
  render(<NumberForm settings={settings} value={value} onSave={onSave} />);
  return onSave;
};

// Saves the form and gives the number it saved.
const savedNumber = async (onSave) => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  });
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  return onSave.mock.calls[0][0].score;
};

const expectShown = (value) => {
  expect(shownNumber()).toBe(String(value));
  expect(screen.getByRole("slider")).toHaveAttribute(
    "aria-valuenow",
    String(value),
  );
};

describe("annotate form number field: the − and + buttons", () => {
  it("+ from 3 with step_size 2 from 1 saves 5", async () => {
    const onSave = openForm(ODD_STEPS, 3);

    await press(stepButtons().plus);

    expectShown(5);
    expect(await savedNumber(onSave)).toBe(5);
  });

  it("− from 5 saves 3", async () => {
    const onSave = openForm(ODD_STEPS, 5);

    await press(stepButtons().minus);

    expectShown(3);
    expect(await savedNumber(onSave)).toBe(3);
  });

  it("from a number off the steps, + and − go to the next step the server accepts", async () => {
    const onSave = openForm(ODD_STEPS, 4);

    await press(stepButtons().plus);
    expectShown(5);

    await press(stepButtons().minus);
    await press(stepButtons().minus);
    expectShown(1);

    expect(await savedNumber(onSave)).toBe(1);
  });

  it("counts the steps from 0 when the label has no minimum", async () => {
    const onSave = openForm({ max: 10, step_size: 2 }, 4);

    await press(stepButtons().plus);

    expectShown(6);
    expect(await savedNumber(onSave)).toBe(6);
  });

  it("− at the minimum is off and changes nothing", async () => {
    const onSave = openForm(ODD_STEPS, 1);

    expect(stepButtons().minus).toBeDisabled();
    await press(stepButtons().minus);

    expectShown(1);
    expect(await savedNumber(onSave)).toBe(1);
  });

  it("+ at the maximum is off and changes nothing", async () => {
    const onSave = openForm(ODD_STEPS, 9);

    expect(stepButtons().plus).toBeDisabled();
    await press(stepButtons().plus);

    expectShown(9);
    expect(await savedNumber(onSave)).toBe(9);
  });

  it("+ at the last step below a maximum off the steps is off", async () => {
    const onSave = openForm(
      { min: 0, max: 9, step_size: 2, display_type: "slider" },
      8,
    );

    expect(stepButtons().plus).toBeDisabled();
    await press(stepButtons().plus);

    expectShown(8);
    expect(await savedNumber(onSave)).toBe(8);
  });

  it("a slider move still sets the value the form saves", async () => {
    const onSave = openForm(ODD_STEPS, 3);

    await pressArrowRight();

    expectShown(5);
    expect(await savedNumber(onSave)).toBe(5);
  });
});

// ---------------------------------------------------------------------------
// The field in the trace annotate drawer

const PROJECT_ID = "project-1";
const SPAN_ID = "span-x";

const SCORE_LABEL = {
  id: "label-score",
  name: "Score",
  type: "numeric",
  settings: ODD_STEPS,
};

function DrawerHarness({ queryClient }) {
  return (
    <QueryClientProvider client={queryClient}>
      <SelectedNodeContext.Provider
        value={{
          selectedNode: {
            id: SPAN_ID,
            name: "Span X",
            observation_type: "llm",
          },
          setSelectedNode: () => {},
        }}
      >
        <TraceDetailDrawer
          open
          onClose={() => {}}
          traceData={{ trace_id: "trace-1" }}
        />
      </SelectedNodeContext.Provider>
    </QueryClientProvider>
  );
}

DrawerHarness.propTypes = {
  queryClient: PropTypes.object.isRequired,
};

describe("trace annotate drawer: a number changed with +", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.params = { projectId: PROJECT_ID };
    axios.get.mockImplementation((url) => {
      if (url === "/model-hub/annotations-labels/") {
        return Promise.resolve({
          data: { results: [SCORE_LABEL], next: null, current_page: 1 },
        });
      }
      if (url === "/tracer/span-annotations/") {
        return Promise.resolve({
          data: {
            result: {
              annotations: [
                { annotationLabelId: "label-score", annotationValue: 3 },
              ],
              notes: [],
            },
          },
        });
      }
      if (url === `/tracer/observation-span/${SPAN_ID}/`) {
        return Promise.resolve({ data: { result: { name: "Span X" } } });
      }
      return Promise.resolve({ data: { result: { observation_spans: [] } } });
    });
    axios.post.mockResolvedValue({
      data: {
        status: true,
        result: {
          id: SPAN_ID,
          failed_labels: [],
          failed_reasons: {},
          success_labels: ["label-score"],
        },
      },
    });
  });

  it("sends the new number when Annotate is pressed", async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 5 * 1000 },
        mutations: { retry: false },
      },
    });
    queryClient.setQueryData(["project-annotations-labels", PROJECT_ID], {
      data: { results: [SCORE_LABEL] },
    });
    render(<DrawerHarness queryClient={queryClient} />);

    // Waited for outside act, so the drawer can finish loading meanwhile.
    const opener = await screen.findByRole("button", {
      name: "Open annotate form",
    });
    await act(async () => {
      fireEvent.click(opener);
    });
    await screen.findByRole("slider");
    await waitFor(() => expectShown(3));

    await press(stepButtons().plus);
    expectShown(5);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Annotate" }));
    });

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(
        "/tracer/add-annotation-values/",
        expect.objectContaining({
          observation_span_id: SPAN_ID,
          annotation_values: { "label-score": 5 },
        }),
      ),
    );
  });
});
