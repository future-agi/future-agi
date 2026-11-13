import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import AddNewDataset from "../AddNewDataset";
import AddExistingDataset from "../AddExistingDataset";

// Both add-to-dataset endpoints queue the row work in the background and
// answer `result.status: "processing"`. The toast must say the rows are still
// being added unless the API reports "completed".

const NEW_PROCESSING_COPY =
  "Dataset created. Rows are still being added in the background, this can take a while for large selections.";
const NEW_DONE_COPY = "Datapoints added to newly created dataset";
const EXISTING_PROCESSING_COPY =
  "Rows are being added to the dataset in the background, this can take a while for large selections.";
const EXISTING_DONE_COPY = "Data added successfully";

const { mutationOptions, mockEnqueueSnackbar, mockNavigate } = vi.hoisted(
  () => ({
    mutationOptions: [],
    mockEnqueueSnackbar: vi.fn(),
    mockNavigate: vi.fn(),
  }),
);

vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useMutation: (options) => {
    mutationOptions.push(options);
    return { mutate: vi.fn(), isPending: false };
  },
  useQuery: () => ({ data: undefined, isLoading: false }),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mockNavigate,
}));

const respond = (result) => ({ data: { status: true, result } });

// Results that do not report completion: the real "processing" answer, a
// missing result, and empty or unknown statuses.
const notCompletedResults = [
  ["processing", { dataset_id: "ds-1", status: "processing", message: "m" }],
  ["no result", undefined],
  ["null status", { dataset_id: "ds-1", status: null }],
  ["empty status", { dataset_id: "ds-1", status: "" }],
  ["unknown status", { dataset_id: "ds-1", status: "queued" }],
];

const runOnSuccess = (res) =>
  mutationOptions[mutationOptions.length - 1].onSuccess(res);

const renderToast = () => {
  expect(mockEnqueueSnackbar).toHaveBeenCalledTimes(1);
  return render(mockEnqueueSnackbar.mock.calls[0][0]);
};

beforeEach(() => {
  mutationOptions.length = 0;
  vi.clearAllMocks();
});

describe("AddNewDataset toast", () => {
  const renderNew = (props = {}) =>
    render(<AddNewDataset handleclose={vi.fn()} {...props} />);

  it.each(notCompletedResults)(
    "says rows are still being added for %s",
    (_label, result) => {
      renderNew();
      runOnSuccess(respond(result));

      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(expect.anything(), {
        variant: "info",
      });
      const { container } = renderToast();
      expect(container).toHaveTextContent(NEW_PROCESSING_COPY);
      expect(container).not.toHaveTextContent(NEW_DONE_COPY);
      expect(screen.getByText("View Dataset")).toBeInTheDocument();
    },
  );

  it("claims completion only when the API reports completed", () => {
    renderNew();
    runOnSuccess(respond({ dataset_id: "ds-1", status: "completed" }));

    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(expect.anything(), {
      variant: "success",
    });
    const { container } = renderToast();
    expect(container).toHaveTextContent(NEW_DONE_COPY);
    expect(container).not.toHaveTextContent(NEW_PROCESSING_COPY);
  });

  it.each([
    ["dataset_id", { dataset_id: "ds-1", status: "processing" }, "ds-1"],
    ["datasetId", { datasetId: "ds-2", status: "processing" }, "ds-2"],
  ])("links View Dataset to the new dataset via %s", (_key, result, id) => {
    renderNew();
    runOnSuccess(respond(result));
    renderToast();

    fireEvent.click(screen.getByText("View Dataset"));
    expect(mockNavigate).toHaveBeenCalledWith(
      `/dashboard/develop/${id}?tab=data`,
      { state: { from: window.location.pathname } },
    );
  });

  it("still closes the drawer and calls onSuccess", () => {
    const handleclose = vi.fn();
    const onSuccess = vi.fn();
    renderNew({ handleclose, onSuccess });
    runOnSuccess(respond({ dataset_id: "ds-1", status: "processing" }));

    expect(handleclose).toHaveBeenCalledTimes(1);
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });
});

describe("AddExistingDataset toast", () => {
  const renderExisting = (props = {}) =>
    render(
      <AddExistingDataset
        handleclose={vi.fn()}
        observationFields={[]}
        availableDatasets={[]}
        {...props}
      />,
    );

  it.each(notCompletedResults)(
    "says rows are still being added for %s",
    (_label, result) => {
      renderExisting();
      runOnSuccess(respond(result));

      expect(mockEnqueueSnackbar).toHaveBeenCalledWith(expect.anything(), {
        variant: "info",
      });
      const { container } = renderToast();
      expect(container).toHaveTextContent(EXISTING_PROCESSING_COPY);
      expect(container).not.toHaveTextContent(EXISTING_DONE_COPY);
      expect(screen.getByText("View Dataset")).toBeInTheDocument();
    },
  );

  it("claims completion only when the API reports completed", () => {
    renderExisting();
    runOnSuccess(respond({ dataset_id: "ds-1", status: "completed" }));

    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(expect.anything(), {
      variant: "success",
    });
    const { container } = renderToast();
    expect(container).toHaveTextContent(EXISTING_DONE_COPY);
    expect(container).not.toHaveTextContent(EXISTING_PROCESSING_COPY);
  });

  it("still closes the drawer and calls onSuccess", () => {
    const handleclose = vi.fn();
    const onSuccess = vi.fn();
    renderExisting({ handleclose, onSuccess });
    runOnSuccess(respond({ dataset_id: "ds-1", status: "processing" }));

    expect(handleclose).toHaveBeenCalledTimes(1);
    expect(onSuccess).toHaveBeenCalledTimes(1);
  });

  it("shows no toast when the response envelope reports failure", () => {
    renderExisting();
    runOnSuccess({ data: { status: false, result: "error" } });

    expect(mockEnqueueSnackbar).not.toHaveBeenCalled();
  });
});
