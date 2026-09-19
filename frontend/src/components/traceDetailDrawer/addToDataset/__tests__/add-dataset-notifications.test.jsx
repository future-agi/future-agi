import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import { useMutation, useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import AddExistingDataset from "../AddExistingDataset";
import AddNewDataset from "../AddNewDataset";

vi.mock("@tanstack/react-query", () => ({
  useMutation: vi.fn(),
  useQuery: vi.fn(),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
  endpoints: {
    develop: {
      getDatasetColumns: vi.fn(),
    },
    project: {
      addExistingDataset: "/add-existing-dataset/",
      addNewDataset: "/add-new-dataset/",
    },
  },
}));

const baseProps = {
  availableDatasets: [],
  currentTab: "trace",
  observationFields: [{ name: "input", type: "text" }],
  selectAll: false,
  selectedNode: "span-1",
  selectedSpans: [],
  selectedTraces: [],
  handleclose: vi.fn(),
  onSuccess: vi.fn(),
};

let mutationOptions;

function renderWithMutation(Component) {
  useMutation.mockImplementation((options) => {
    mutationOptions = options;
    return { isPending: false, mutate: vi.fn() };
  });

  render(<Component {...baseProps} />);

  return mutationOptions;
}

function getSnackbarText() {
  const snackbar = enqueueSnackbar.mock.calls[0][0];
  return render(snackbar).container.textContent;
}

describe.each([
  {
    component: AddNewDataset,
    name: "creating a new dataset",
    response: (status) => ({
      data: { result: { dataset_id: "dataset-1", status } },
    }),
    processingMessage:
      "Dataset created. Rows are still being added in the background",
    completedMessage: "Datapoints added to newly created dataset",
  },
  {
    component: AddExistingDataset,
    name: "adding to an existing dataset",
    response: (status) => ({
      data: { result: { dataset_id: "dataset-1", status }, status: true },
    }),
    processingMessage: "Rows are being added to the dataset in the background",
    completedMessage: "Data added successfully",
  },
])(
  "dataset addition notifications when $name",
  ({ component, response, processingMessage, completedMessage }) => {
    beforeEach(() => {
      vi.clearAllMocks();
      useQuery.mockReturnValue({ data: undefined, isLoading: false });
      baseProps.handleclose = vi.fn();
      baseProps.onSuccess = vi.fn();
      mutationOptions = undefined;
    });

    it.each([
      ["processing", "info", processingMessage],
      ["completed", "success", completedMessage],
      ["missing", "info", processingMessage],
    ])("uses the %s status presentation", (status, variant, message) => {
      const options = renderWithMutation(component);

      options.onSuccess(response(status === "missing" ? undefined : status));

      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar.mock.calls[0][1]).toEqual({ variant });
      expect(getSnackbarText()).toContain(message);
      expect(baseProps.handleclose).toHaveBeenCalledTimes(1);
      expect(baseProps.onSuccess).toHaveBeenCalledTimes(1);
    });
  },
);
