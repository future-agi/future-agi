// The trace annotate drawer after the server answers a save with 200. When
// every label saved, the drawer closes with the team's sentence as a success.
// When some did not, the toast is a warning that says how many saved and
// names the rest in the drawer's order; when none saved, it is an error that
// says so and names them. Each label the server refused shows its reason
// under its field, and the drawer stays open with what was entered so it can
// be sent again.
import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import SelectedNodeContext from "../selectedNodeContext";

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

vi.mock("../common", () => ({
  useTraceErrorAnalysis: () => ({ data: undefined, isPending: true }),
}));

vi.mock("../drawer-bottom", () => ({ default: () => null }));
vi.mock("../trace-tree", () => ({ default: () => null }));
vi.mock("../drawer-right", () => ({ default: () => null }));
vi.mock("../ErrorAnalysis", () => ({ default: () => null }));
vi.mock("../addToDataset/add-dataset", () => ({ default: () => null }));
vi.mock("../AnnotationSidebarContent", () => ({ default: () => null }));
vi.mock("../AddLabelDrawer", () => ({ default: () => null }));
vi.mock(
  "src/sections/develop-detail/Annotations/CreateEditLabel/CreateEditLabel",
  () => ({ default: () => null }),
);

// The label settings drawer is where Annotate is opened from; here it is only
// the button that opens the annotate drawer.
vi.mock("../add-annotations-drawer", () => ({
  default: ({ onAnnotateClick }) => (
    <button type="button" onClick={onAnnotateClick}>
      Open annotate form
    </button>
  ),
}));

import TraceDetailDrawer from "../trace-detail-drawer";

const PROJECT_ID = "project-1";
const SPAN_ID = "span-x";

const TEAM_SENTENCE = "Annotations has been added.";

// The drawer's labels, in the order it shows them.
const LABELS = [
  {
    id: "label-tone",
    name: "Tone",
    type: "text",
    settings: { placeholder: "Tone answer" },
  },
  {
    id: "label-clarity",
    name: "Clarity",
    type: "text",
    settings: { placeholder: "Clarity answer" },
  },
  {
    id: "label-summary",
    name: "Summary",
    type: "text",
    settings: { placeholder: "Summary answer" },
  },
  {
    id: "label-rating",
    name: "Rating",
    type: "star",
    settings: { no_of_stars: 5 },
  },
];

// The person's saved answer for Rating; the text labels are typed in.
const STORED = [{ annotationLabelId: "label-rating", annotationValue: 4 }];

const TYPED = {
  "Tone answer": "Warm and direct",
  "Clarity answer": "Easy to follow",
  "Summary answer": "Answers the question",
};

const TONE_REASON = "Text too short. Minimum length is 50 characters.";
const CLARITY_REASON = "Text too long. Maximum length is 10 characters.";
const RATING_REASON = "Choose from 1 to 3 stars.";

// The server's 200 answer to the save.
const answer = ({ saved, failed, reasons }) => ({
  data: {
    status: true,
    result: {
      id: SPAN_ID,
      failed_labels: failed,
      failed_reasons: reasons,
      success_labels: saved,
    },
  },
});

const createQueryClient = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 5 * 1000 },
      mutations: { retry: false },
    },
  });
  // The page has the project's labels before Annotate can be opened.
  queryClient.setQueryData(["project-annotations-labels", PROJECT_ID], {
    data: { results: LABELS },
  });
  return queryClient;
};

function Harness({ queryClient }) {
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

Harness.propTypes = {
  queryClient: PropTypes.object.isRequired,
};

// The message a text field shows under itself, as its helper text.
const messageUnder = (placeholder) =>
  screen
    .getByPlaceholderText(placeholder)
    .closest(".MuiFormControl-root")
    .querySelector(".MuiFormHelperText-root");

const settle = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

// The drawer slides out in well under 400 ms when it closes, so after this
// wait a drawer that was going to close has gone.
const waitPastClosing = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 400));
  });
};

const annotateButton = () => screen.getByRole("button", { name: "Annotate" });

// Opens the annotate drawer, types an answer for each text label and presses
// Annotate; the server answers with `response`.
const annotateAndSave = async (response) => {
  axios.post.mockResolvedValue(response);
  render(<Harness queryClient={createQueryClient()} />);

  const opener = await screen.findByRole("button", {
    name: "Open annotate form",
  });
  await act(async () => {
    fireEvent.click(opener);
  });
  await screen.findByPlaceholderText("Tone answer");

  for (const [placeholder, value] of Object.entries(TYPED)) {
    await act(async () => {
      fireEvent.change(screen.getByPlaceholderText(placeholder), {
        target: { value },
      });
    });
  }
  await settle();

  await act(async () => {
    fireEvent.click(annotateButton());
  });
  await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
};

const expectToast = async (sentence, variant) => {
  await waitFor(() =>
    expect(enqueueSnackbar).toHaveBeenCalledWith(sentence, { variant }),
  );
};

const expectStillOpenWithTheEnteredValues = async () => {
  await waitPastClosing();
  expect(annotateButton()).toBeInTheDocument();
  for (const [placeholder, value] of Object.entries(TYPED)) {
    expect(screen.getByPlaceholderText(placeholder)).toHaveValue(value);
  }
};

describe("trace annotate drawer: a save where some labels did not save", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.params = { projectId: PROJECT_ID };
    axios.get.mockImplementation((url) => {
      if (url === "/model-hub/annotations-labels/") {
        return Promise.resolve({
          data: { results: LABELS, next: null, current_page: 1 },
        });
      }
      if (url === "/tracer/span-annotations/") {
        return Promise.resolve({
          data: { result: { annotations: STORED, notes: [] } },
        });
      }
      if (url === `/tracer/observation-span/${SPAN_ID}/`) {
        return Promise.resolve({ data: { result: { name: "Span X" } } });
      }
      return Promise.resolve({ data: { result: { observation_spans: [] } } });
    });
  });

  it("names the label that did not save, shows its reason under it, and stays open", async () => {
    await annotateAndSave(
      answer({
        saved: ["label-clarity", "label-summary", "label-rating"],
        failed: ["label-tone"],
        reasons: { "label-tone": TONE_REASON },
      }),
    );

    await expectToast("Saved 3 of 4 labels. Not saved: Tone.", "warning");
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      TEAM_SENTENCE,
      expect.anything(),
    );
    await waitFor(() =>
      expect(messageUnder("Tone answer")?.textContent).toBe(TONE_REASON),
    );
    expect(messageUnder("Clarity answer")).toBeNull();
    expect(messageUnder("Summary answer")).toBeNull();

    await expectStillOpenWithTheEnteredValues();
    expect(messageUnder("Tone answer")?.textContent).toBe(TONE_REASON);
  });

  it("names two labels in the drawer's order and shows both reasons", async () => {
    await annotateAndSave(
      answer({
        saved: ["label-summary", "label-rating"],
        failed: ["label-clarity", "label-tone"],
        reasons: {
          "label-clarity": CLARITY_REASON,
          "label-tone": TONE_REASON,
        },
      }),
    );

    await expectToast(
      "Saved 2 of 4 labels. Not saved: Tone, Clarity.",
      "warning",
    );
    await waitFor(() =>
      expect(messageUnder("Tone answer")?.textContent).toBe(TONE_REASON),
    );
    expect(messageUnder("Clarity answer")?.textContent).toBe(CLARITY_REASON);
    expect(messageUnder("Summary answer")).toBeNull();

    await expectStillOpenWithTheEnteredValues();
  });

  it("shows the reason under a label that is not a text field", async () => {
    await annotateAndSave(
      answer({
        saved: ["label-tone", "label-clarity", "label-summary"],
        failed: ["label-rating"],
        reasons: { "label-rating": RATING_REASON },
      }),
    );

    await expectToast("Saved 3 of 4 labels. Not saved: Rating.", "warning");
    const reason = await screen.findByText(RATING_REASON);
    // Under Rating: after its name and before the notes field that follows.
    expect(
      screen.getByText("Rating").compareDocumentPosition(reason) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      reason.compareDocumentPosition(
        screen.getByPlaceholderText("Add optional notes here"),
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await expectStillOpenWithTheEnteredValues();
  });

  it('counts a label the drawer does not list as "1 label"', async () => {
    await annotateAndSave(
      answer({
        saved: ["label-clarity", "label-summary"],
        failed: ["label-gone", "label-tone"],
        reasons: { "label-tone": TONE_REASON },
      }),
    );

    await expectToast(
      "Saved 2 of 4 labels. Not saved: Tone, 1 label.",
      "warning",
    );
    await waitFor(() =>
      expect(messageUnder("Tone answer")?.textContent).toBe(TONE_REASON),
    );

    await expectStillOpenWithTheEnteredValues();
  });

  it("says no labels were saved, as an error, when none saved", async () => {
    await annotateAndSave(
      answer({
        saved: [],
        failed: ["label-rating", "label-gone", "label-tone"],
        reasons: {
          "label-rating": RATING_REASON,
          "label-tone": TONE_REASON,
        },
      }),
    );

    await expectToast(
      "No labels were saved. Not saved: Tone, Rating, 1 label.",
      "error",
    );
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      TEAM_SENTENCE,
      expect.anything(),
    );
    await waitFor(() =>
      expect(messageUnder("Tone answer")?.textContent).toBe(TONE_REASON),
    );
    expect(screen.getByText(RATING_REASON)).toBeInTheDocument();

    await expectStillOpenWithTheEnteredValues();
  });

  it("closes with the team's sentence when every label saved", async () => {
    await annotateAndSave(
      answer({
        saved: ["label-tone", "label-clarity", "label-summary", "label-rating"],
        failed: [],
        reasons: {},
      }),
    );

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(TEAM_SENTENCE, {
        variant: "success",
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Annotate" }),
      ).not.toBeInTheDocument(),
    );
  });
});
