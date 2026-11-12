import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { handleError } from "src/utils/queryErrorHandler";

// W10-EVALS-U04: the Ground Truth upload drawer explains every file it does
// not take, says when a JSON file is malformed, and reports a failed upload
// with one specific toast (not the server message plus "Upload failed").

const mocks = vi.hoisted(() => ({
  enqueueSnackbar: vi.fn(),
  post: vi.fn(),
  dropzone: { props: null },
}));

// The component's own toasts and the app-wide query error handler both land
// on the same spy, so the test counts every toast the user would see.
vi.mock("notistack", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
  enqueueSnackbar: (...args) => mocks.enqueueSnackbar(...args),
}));

vi.mock("react-dropzone", () => ({
  useDropzone: (props) => {
    mocks.dropzone.props = props;
    return {
      getRootProps: () => ({}),
      getInputProps: () => ({}),
      isDragActive: false,
    };
  },
}));

vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [], isLoading: false }),
  useGetDatasetColumns: () => ({ data: [] }),
  useGetDatasetDetail: vi.fn(),
}));

// Only the transport is mocked: the real upload and embedding hooks run.
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: (...args) => mocks.post(...args),
  },
  endpoints: {
    develop: {
      eval: {
        uploadGroundTruth: (id) =>
          `/model-hub/eval-templates/${id}/ground-truth/upload/`,
        groundTruthEmbed: (id) => `/model-hub/ground-truth/${id}/embed/`,
      },
    },
  },
}));

import { UploadDrawer } from "../EvalGroundTruthTab";
import { useTriggerEmbedding } from "../../hooks/useGroundTruth";

const MB = 1024 * 1024;
const XLSX_TYPE =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

// What the axios interceptor rejects with for a 400 from the API: the error
// body spread with its status, never an AxiosError with `.response`.
const SERVER_MESSAGE = "The uploaded file has no data rows.";
const SERVER_ERROR = {
  status: false,
  code: "bad_request",
  message: SERVER_MESSAGE,
  detail: SERVER_MESSAGE,
  result: SERVER_MESSAGE,
  statusCode: 400,
};

const csvFile = (name, sizeBytes) => {
  const file = new File(["input,output\nhi,hello\n"], name, {
    type: "text/csv",
  });
  if (sizeBytes) Object.defineProperty(file, "size", { value: sizeBytes });
  return file;
};

// The calls react-dropzone 14 makes after a drop, in the same order.
const drop = (accepted, rejections = []) =>
  act(() => {
    const { props } = mocks.dropzone;
    props.onDrop?.(accepted, rejections, {});
    if (rejections.length > 0) props.onDropRejected?.(rejections, {});
    if (accepted.length > 0) props.onDropAccepted?.(accepted, {});
  });

const toasts = () => mocks.enqueueSnackbar.mock.calls.map(([text]) => text);

let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

const renderDrawer = () => {
  const onClose = vi.fn();
  render(
    <UploadDrawer
      open
      onClose={onClose}
      templateId="template-1"
      evalVariables={[]}
    />,
    { wrapper },
  );
  return { onClose };
};

beforeEach(() => {
  client = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  mocks.enqueueSnackbar.mockReset();
  mocks.post.mockReset();
  mocks.dropzone.props = null;
});

describe("Ground Truth upload feedback", () => {
  it.each([
    [
      "a file over 50 MB",
      () => [
        {
          file: csvFile("answers.csv", 60 * MB),
          errors: [
            {
              code: "file-too-large",
              message: "File is larger than 52428800 bytes",
            },
          ],
        },
      ],
      "That file is 60 MB. Ground truth files can be up to 50 MB.",
    ],
    [
      "a .tsv file",
      () => [
        {
          file: new File(["input\toutput\n"], "answers.tsv", {
            type: "text/tab-separated-values",
          }),
          errors: [
            {
              code: "file-invalid-type",
              message: "File type must be text/csv,.csv",
            },
          ],
        },
      ],
      "Only .csv, .json, .xls or .xlsx files are supported.",
    ],
    [
      "two files at once",
      () =>
        ["a.csv", "b.csv"].map((name) => ({
          file: csvFile(name),
          errors: [{ code: "too-many-files", message: "Too many files" }],
        })),
      "Drop one file at a time.",
    ],
  ])("says why %s was not accepted", async (_case, rejections, message) => {
    renderDrawer();

    drop([], rejections());

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    // The drawer stays on the source step so the user can choose again.
    expect(
      screen.getByText("Choose a file or drag & drop"),
    ).toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("says a dropped JSON file is not valid JSON and keeps Upload disabled", async () => {
    renderDrawer();

    drop([
      new File(['[{"input": "hi", "output": "hello"},]'], "answers.json", {
        type: "application/json",
      }),
    ]);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /This file is not valid JSON/,
    );
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("reports a rejected upload with one toast carrying the server's reason", async () => {
    mocks.post.mockRejectedValue(SERVER_ERROR);
    const { onClose } = renderDrawer();

    drop([new File(["xlsx-bytes"], "answers.xlsx", { type: XLSX_TYPE })]);
    fireEvent.click(await screen.findByRole("button", { name: "Upload" }));

    await waitFor(() => expect(mocks.enqueueSnackbar).toHaveBeenCalled());
    // Let every handler that reacts to the failure finish before counting.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(toasts()).toEqual([SERVER_MESSAGE]);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("leaves a failed embedding start to the tab's own toast", async () => {
    mocks.post.mockRejectedValue(SERVER_ERROR);
    const { result } = renderHook(() => useTriggerEmbedding(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync("ground-truth-1").catch(() => {});
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(toasts()).toEqual([]);
  });
});
