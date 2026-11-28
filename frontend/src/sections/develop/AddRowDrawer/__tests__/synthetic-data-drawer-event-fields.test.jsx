import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const MARK = "SYNTHETIC_DATASET_TEXT_93ad";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    formValues: {},
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      register: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
    post: vi.fn(),
    knowledgeBases: {
      data: [
        { id: "kb-2", name: "Knowledge base SYNTHETIC_DATASET_TEXT_93ad" },
      ],
      refetch: () => {},
    },
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: {
      ...actual.default,
      get: () => new Promise(() => {}),
      post: (...args) => mocks.post(...args),
    },
  };
});
// The drawer's column fields are select menus; the resolver hands the
// submit handler the values the filled-in form produces.
vi.mock("@hookform/resolvers/zod", () => ({
  zodResolver: () => async () => ({ values: mocks.formValues, errors: {} }),
}));
// One stable list, as the query hook returns between renders.
vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => mocks.knowledgeBases,
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn(), closeSnackbar: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// The column rows are not under test here; their values come from the
// resolver above.
vi.mock("../CreateSyntheticData/AddColumnField", () => ({
  default: () => null,
}));

import SyntheticDataDrawer from "../CreateSyntheticData";
import { Events } from "src/utils/Mixpanel";

const trackedProperties = (eventName) =>
  mocks.mixpanel.track.mock.calls
    .filter(([name]) => name === eventName)
    .map(([, properties]) => properties);

const renderDrawer = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <SyntheticDataDrawer
          open
          onClose={vi.fn()}
          refreshGrid={vi.fn()}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const submitTwice = async () => {
  // "Next" opens the column description step; "Create" submits.
  fireEvent.click(await screen.findByRole("button", { name: "Next" }));
  fireEvent.click(await screen.findByRole("button", { name: "Create" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
};

describe("synthetic data drawer analytics events", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mocks.mixpanel.track.mockClear();
    mocks.post.mockReset();
    mocks.post.mockResolvedValue({ data: { result: { data: { id: "d-1" } } } });
  });

  it("sends counts, types and flags when rows are added to a dataset", async () => {
    mocks.formValues = {
      name: `Dataset ${MARK}`,
      description: `Description ${MARK}`,
      useCase: "",
      pattern: `Pattern ${MARK}`,
      rowNumber: "12",
      kb_id: "",
      columns: [
        {
          name: `answer_${MARK}`,
          data_type: "integer",
          description: `Column description ${MARK}`,
          property: [{ type: "value", value: `1-10 ${MARK}` }],
        },
      ],
    };
    renderDrawer({ datasetId: "dataset-3" });

    await submitTwice();

    expect(
      trackedProperties(Events.syntheticDatasetCreationSuccessful),
    ).toEqual([
      {
        row_count: 12,
        column_count: 1,
        column_types: ["integer"],
        property_count: 1,
        property_types: ["value"],
        has_knowledge_base: false,
        has_use_case: false,
        has_patterns: true,
        edit_mode: false,
        dataset_id: "dataset-3",
      },
    ]);
    expect(
      trackedProperties(Events.syntheticDatasetCreationSuccessfulColumn),
    ).toEqual([
      { column: [{ Column_Type: "integer", Property_Types: ["value"] }] },
    ]);
    mocks.mixpanel.track.mock.calls.forEach(([, properties]) => {
      expect(JSON.stringify(properties)).not.toContain(MARK);
    });

    // The request itself still carries the full dataset definition.
    const [, body] = mocks.post.mock.calls[0];
    expect(body.dataset.name).toBe(`Dataset ${MARK}`);
    expect(body.columns[0].name).toBe(`answer_${MARK}`);
  });

  it("sends the knowledge base id and custom property types for a new dataset", async () => {
    mocks.formValues = {
      name: `Dataset ${MARK}`,
      description: `Description ${MARK}`,
      useCase: `Use case ${MARK}`,
      pattern: "",
      rowNumber: "40",
      kb_id: "kb-2",
      columns: [
        {
          name: `topic_${MARK}`,
          data_type: "text",
          description: "",
          property: [
            { type: "min_length", value: "3" },
            { type: `Tone ${MARK}`, value: `formal ${MARK}` },
          ],
        },
        {
          name: `flag_${MARK}`,
          data_type: "boolean",
          description: "",
          property: [],
        },
      ],
    };
    renderDrawer({});
    fireEvent.change(await screen.findByPlaceholderText("Enter name"), {
      target: { value: `Dataset ${MARK}` },
    });

    await submitTwice();

    expect(
      trackedProperties(Events.syntheticDatasetCreationSuccessful),
    ).toEqual([
      {
        row_count: 40,
        column_count: 2,
        column_types: ["text", "boolean"],
        property_count: 2,
        property_types: ["min_length", "custom"],
        knowledge_base_id: "kb-2",
        has_knowledge_base: true,
        has_use_case: true,
        has_patterns: false,
        edit_mode: false,
      },
    ]);
    expect(
      trackedProperties(Events.syntheticDatasetCreationSuccessfulColumn),
    ).toEqual([
      {
        column: [
          { Column_Type: "text", Property_Types: ["min_length", "custom"] },
          { Column_Type: "boolean", Property_Types: [] },
        ],
      },
    ]);
    mocks.mixpanel.track.mock.calls.forEach(([, properties]) => {
      expect(JSON.stringify(properties)).not.toContain(MARK);
    });
  });
});
