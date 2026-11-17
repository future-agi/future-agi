// The trace annotate drawer saves the answers the person changed. An answer
// left as it was stored is not sent again, so a stored answer that breaks its
// label's newer limits gets no "Not saved" line under a field the person did
// not touch, and does not hold back the answers they changed. An answer
// changed back to its stored value counts as unchanged. When no changed
// answer has a value and the notes changed, the notes are still sent with the
// stored answers. Otherwise Annotate with nothing to send closes the drawer
// and sends nothing.
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

// The drawer's labels, in the order it shows them. Tone's minimum length was
// raised after its answer was stored.
const LABELS = [
  {
    id: "label-tone",
    name: "Tone",
    type: "text",
    settings: { placeholder: "Tone answer", min_length: 50, max_length: 1000 },
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

// The person's stored answers; Summary has none.
const STORED = [
  { annotationLabelId: "label-tone", annotationValue: "Short tone" },
  { annotationLabelId: "label-clarity", annotationValue: "Clear" },
  { annotationLabelId: "label-rating", annotationValue: 4 },
];

const STORED_VALUES = {
  "label-tone": "Short tone",
  "label-clarity": "Clear",
  "label-rating": 4,
};

const TONE_REASON = "Text too short. Minimum length is 50 characters.";

// The server refuses any Tone answer under its newer minimum, saves the other
// labels it is sent, and answers 400 when it is sent no answer at all.
const REFUSED = { "label-tone": TONE_REASON };

const serverAnswer = (_url, body) => {
  const sent = Object.keys(body?.annotation_values ?? {});
  if (sent.length === 0) {
    return Promise.reject({ response: { status: 400 } });
  }
  const failed = sent.filter((id) => REFUSED[id]);
  return Promise.resolve({
    data: {
      status: true,
      result: {
        id: SPAN_ID,
        failed_labels: failed,
        failed_reasons: Object.fromEntries(
          failed.map((id) => [id, REFUSED[id]]),
        ),
        success_labels: sent.filter((id) => !REFUSED[id]),
      },
    },
  });
};

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

const openAnnotateForm = async () => {
  render(<Harness queryClient={createQueryClient()} />);
  const opener = await screen.findByRole("button", {
    name: "Open annotate form",
  });
  await act(async () => {
    fireEvent.click(opener);
  });
  await waitFor(() =>
    expect(screen.getByPlaceholderText("Tone answer")).toHaveValue(
      "Short tone",
    ),
  );
};

const type = async (placeholder, value) => {
  await act(async () => {
    fireEvent.change(screen.getByPlaceholderText(placeholder), {
      target: { value },
    });
  });
};

const pressAnnotate = async () => {
  await settle();
  await act(async () => {
    fireEvent.click(annotateButton());
  });
};

// The body of the one save request.
const sentBody = async () => {
  await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
  expect(axios.post.mock.calls[0][0]).toBe("/tracer/add-annotation-values/");
  return axios.post.mock.calls[0][1];
};

const expectClosedWithTheTeamSentence = async () => {
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
};

describe("trace annotate drawer: a save sends the answers the person changed", () => {
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
    axios.post.mockImplementation(serverAnswer);
  });

  it("does not send an untouched stored answer, so the save is full and closes", async () => {
    await openAnnotateForm();

    await type("Summary answer", "Answers the question");
    await pressAnnotate();

    const body = await sentBody();
    expect(body.annotation_values).toEqual({
      "label-summary": "Answers the question",
    });
    await expectClosedWithTheTeamSentence();
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
  });

  it("sends a changed answer that breaks the limits, and names it with its reason", async () => {
    await openAnnotateForm();

    await type("Tone answer", "Still short");
    await type("Summary answer", "Answers the question");
    await pressAnnotate();

    const body = await sentBody();
    expect(body.annotation_values).toEqual({
      "label-tone": "Still short",
      "label-summary": "Answers the question",
    });
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Saved 1 of 2 labels. Not saved: Tone.",
        { variant: "warning" },
      ),
    );
    await waitFor(() =>
      expect(messageUnder("Tone answer")?.textContent).toBe(TONE_REASON),
    );
    await waitPastClosing();
    expect(annotateButton()).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Tone answer")).toHaveValue(
      "Still short",
    );
  });

  it("sends a changed valid answer with its new value", async () => {
    await openAnnotateForm();

    await type("Clarity answer", "Clear and direct");
    await pressAnnotate();

    const body = await sentBody();
    expect(body.annotation_values["label-clarity"]).toBe("Clear and direct");
  });

  it("does not send an answer changed back to its stored value", async () => {
    await openAnnotateForm();

    await type("Clarity answer", "Clearer");
    await type("Clarity answer", "Clear");
    await type("Summary answer", "Answers the question");
    await pressAnnotate();

    const body = await sentBody();
    expect(body.annotation_values).toEqual({
      "label-summary": "Answers the question",
    });
    await expectClosedWithTheTeamSentence();
  });

  it("sends the notes with the stored answers when only the notes changed", async () => {
    await openAnnotateForm();

    await type("Add optional notes here", "A note");
    await pressAnnotate();

    const body = await sentBody();
    expect(body.notes).toBe("A note");
    expect(body.annotation_values).toEqual(STORED_VALUES);
  });

  it("sends the notes with the stored answers when an answer was typed and cleared", async () => {
    await openAnnotateForm();

    await type("Summary answer", "Answers the question");
    await type("Summary answer", "");
    await type("Add optional notes here", "A note");
    await pressAnnotate();

    const body = await sentBody();
    expect(body.notes).toBe("A note");
    expect(body.annotation_values).toEqual(STORED_VALUES);
  });

  it("closes and sends nothing when Annotate is pressed with nothing changed", async () => {
    await openAnnotateForm();

    await pressAnnotate();
    await waitPastClosing();

    expect(axios.post).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Annotate" }),
    ).not.toBeInTheDocument();
  });

  it("closes and sends nothing when an answer was changed and changed back", async () => {
    await openAnnotateForm();

    await type("Clarity answer", "Clearer");
    await type("Clarity answer", "Clear");
    await pressAnnotate();
    await waitPastClosing();

    expect(axios.post).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Annotate" }),
    ).not.toBeInTheDocument();
  });
});
