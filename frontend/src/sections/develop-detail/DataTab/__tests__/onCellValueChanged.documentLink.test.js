import { describe, it, expect, vi, beforeEach } from "vitest";
import axios from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { onCellValueChangedWrapper } from "../common";

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    develop: {
      updateCellValue: (id) => `/model-hub/develops/${id}/update_cell_value/`,
    },
  },
}));

function makeParams({ newValue, apiError }) {
  const setDataValue = vi.fn();
  const refreshServerSide = vi.fn();
  const onSuccess = vi.fn();
  const onError = vi.fn();
  const params = {
    column: { colId: "col-1", colDef: { dataType: "document" } },
    data: { row_id: "row-1" },
    newValue,
    fileName: "gone.pdf",
    api: {
      getRowNode: () => ({ setDataValue }),
      refreshServerSide,
    },
    onSuccess,
    onError,
  };
  return { params, setDataValue, refreshServerSide, onSuccess, onError, apiError };
}

describe("onCellValueChangedWrapper document link (#2433)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not report success or drop the existing value when the link is unreachable", async () => {
    const { params, setDataValue, refreshServerSide, onSuccess, onError } =
      makeParams({
        newValue: "https://example.com/missing.pdf",
      });
    axios.post.mockRejectedValue({
      response: { data: { message: "The address cannot be reached." } },
    });

    onCellValueChangedWrapper({ invalidateQueries: vi.fn() }, "ds-1")(params);
    await vi.waitFor(() => expect(onError).toHaveBeenCalled());

    expect(onSuccess).not.toHaveBeenCalled();
    expect(setDataValue).not.toHaveBeenCalled();
    expect(refreshServerSide).toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "The address cannot be reached.",
      { variant: "error" },
    );
  });

  it("does not report success or drop the existing value when the link is not a document", async () => {
    const { params, setDataValue, refreshServerSide, onSuccess, onError } =
      makeParams({
        newValue: "https://example.com/index.html",
      });
    axios.post.mockRejectedValue({
      response: { data: { message: "The address is not a document." } },
    });

    onCellValueChangedWrapper({ invalidateQueries: vi.fn() }, "ds-1")(params);
    await vi.waitFor(() => expect(onError).toHaveBeenCalled());

    expect(onSuccess).not.toHaveBeenCalled();
    expect(setDataValue).not.toHaveBeenCalled();
    expect(refreshServerSide).toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "The address is not a document.",
      { variant: "error" },
    );
  });

  it("stores a working document address without an error message", async () => {
    const { params, refreshServerSide, onSuccess, onError } = makeParams({
      newValue: "https://example.com/report.pdf",
    });
    axios.post.mockResolvedValue({ data: { status: true } });

    onCellValueChangedWrapper({ invalidateQueries: vi.fn() }, "ds-1")(params);
    await vi.waitFor(() => expect(onSuccess).toHaveBeenCalled());

    expect(onError).not.toHaveBeenCalled();
    expect(refreshServerSide).toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
});
