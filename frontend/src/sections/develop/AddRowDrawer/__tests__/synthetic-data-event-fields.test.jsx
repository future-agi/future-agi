import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const MARK = "SYNTHETIC_DATASET_TEXT_4c7e";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
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
        { id: "kb-1", name: "Knowledge base SYNTHETIC_DATASET_TEXT_4c7e" },
      ],
      refetch: () => {},
    },
    put: vi.fn(),
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
      put: (...args) => mocks.put(...args),
    },
  };
});
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
vi.mock("src/hooks/useBeforeUnload", () => ({ useBeforeUnload: () => {} }));

import CreateSyntheticDataView from "../CreateSyntheticDataView";
import CreateNewProperties from "../CreateSyntheticData/CreateNewProperties";
import { Events } from "src/utils/Mixpanel";

// A dataset as the edit view receives it from the server.
const savedDataset = {
  dataset: {
    name: `Dataset ${MARK}`,
    description: `Description ${MARK}`,
    objective: `Use case ${MARK}`,
    patterns: `Pattern ${MARK}`,
  },
  kb_id: "kb-1",
  num_rows: 25,
  columns: [
    {
      name: `question_${MARK}`,
      data_type: "text",
      description: `Column description ${MARK}`,
      property: { min_length: 5, max_length: 80 },
    },
    {
      name: `label_${MARK}`,
      data_type: "array",
      description: `Column description ${MARK}`,
      property: { value: `yes, no, ${MARK}`, [`custom ${MARK}`]: MARK },
    },
  ],
};

const expectedViewFields = {
  row_count: 25,
  column_count: 2,
  column_types: ["text", "array"],
  property_count: 4,
  property_types: ["min_length", "max_length", "value", "custom"],
  knowledge_base_id: "kb-1",
  has_knowledge_base: true,
  has_use_case: true,
  has_patterns: true,
  edit_mode: false,
};

const trackedProperties = (eventName) =>
  mocks.mixpanel.track.mock.calls
    .filter(([name]) => name === eventName)
    .map(([, properties]) => properties);

const expectNoTextSent = () => {
  mocks.mixpanel.track.mock.calls.forEach(([, properties]) => {
    expect(JSON.stringify(properties)).not.toContain(MARK);
  });
};

const renderWithProviders = (ui, path = "/dashboard/develop/create") => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/dashboard/develop/create" element={ui} />
          <Route path="/dashboard/develop/:dataset/edit" element={ui} />
          <Route path="*" element={null} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const submitForm = (container) => {
  const form = container.querySelector("form");
  expect(form).not.toBeNull();
  fireEvent.submit(form);
};

describe("synthetic data analytics events", () => {
  beforeEach(() => {
    mocks.mixpanel.track.mockClear();
    mocks.post.mockReset();
    mocks.put.mockReset();
    mocks.post.mockResolvedValue({ data: { result: { data: { id: "d-1" } } } });
    mocks.put.mockResolvedValue({ data: { result: { data: {} } } });
  });

  it("sends counts, types and flags when a synthetic dataset is created", async () => {
    const { container } = renderWithProviders(
      <CreateSyntheticDataView editData={savedDataset} onClose={vi.fn()} />,
    );

    submitForm(container);
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));

    expect(trackedProperties(Events.createSyntheticDatasetClicked)).toEqual([
      { form_fields: expectedViewFields },
    ]);
    expectNoTextSent();

    // The request itself still carries the full dataset definition.
    const [, body] = mocks.post.mock.calls[0];
    expect(body.dataset.name).toBe(`Dataset ${MARK}`);
    expect(body.columns.map((column) => column.name)).toEqual([
      `question_${MARK}`,
      `label_${MARK}`,
    ]);
  });

  it("sends the dataset id and edit flag when the edit form is submitted", async () => {
    const { container } = renderWithProviders(
      <CreateSyntheticDataView
        editMode
        editData={savedDataset}
        onClose={vi.fn()}
      />,
      "/dashboard/develop/dataset-7/edit",
    );

    submitForm(container);
    await waitFor(() =>
      expect(
        trackedProperties(Events.createSyntheticDatasetClicked),
      ).toHaveLength(1),
    );

    expect(trackedProperties(Events.createSyntheticDatasetClicked)).toEqual([
      {
        form_fields: {
          ...expectedViewFields,
          edit_mode: true,
          dataset_id: "dataset-7",
        },
      },
    ]);
    expectNoTextSent();
  });

  it("sends only whether a description was given when a column property is created", () => {
    render(
      <CreateNewProperties
        open
        onClose={vi.fn()}
        data="0"
        setOptions={vi.fn()}
        update={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Enter property name"), {
      target: { value: `Tone ${MARK}` },
    });
    fireEvent.change(
      screen.getByPlaceholderText("Enter property description"),
      {
        target: { value: `How formal ${MARK}` },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(
      trackedProperties(Events.syntheticDatasetNewPropertyCreated),
    ).toEqual([{ has_description: true }]);
    expectNoTextSent();
  });
});
