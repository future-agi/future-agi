// integration C1 review 2:
// - S11: an edit back to the stored value is dropped by the change-set and
//   shows no "saved with the next version" toast.
// - S5: EditFile opened by a grid double-click names the file the cell
//   shows (documentMetaOf, the FileCellRenderer rule).
import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn(() => Promise.resolve({ data: {} }));
vi.mock("src/utils/axios", () => ({
  default: { post: (...a) => post(...a), get: vi.fn(), put: vi.fn() },
  endpoints: new Proxy(
    {},
    {
      get: () =>
        new Proxy(
          {},
          { get: (_, k) => (ds) => `/fake/${String(k)}/${ds ?? ""}` },
        ),
    },
  ),
}));
const snack = vi.fn();
vi.mock("notistack", () => ({ enqueueSnackbar: (...a) => snack(...a) }));

const { useChangeSetStore } = await import("../Versions/changeSetStore");
const { cellWriter } = await import("../common");
const { pendingCell } = await import("../Versions/versionedCellEdit");
const { documentMetaOf } = await import(
  "../DoubleClickEditCell/mediaEditorRoute"
);
const { getFileType } = await import(
  "src/sections/common/DevelopCellRenderer/CellRenderers/common"
);

const DS = "ds-media-r2";
const params = (dataType, newValue, stored = "https://cdn/x/old.png") => ({
  data: { row_id: "ROW_3", C_M: { cell_value: stored } },
  column: { colId: "C_M", colDef: { dataType } },
  colDef: { dataType },
  value: stored,
  newValue,
  api: { getRowNode: () => ({ setDataValue: vi.fn() }) },
});
const write = (p) =>
  cellWriter({ versioned: true, queryClient: {}, dataset: DS })(p);

describe("S11: no toast when the op is dropped", () => {
  beforeEach(() => {
    useChangeSetStore.getState().discard(DS);
    snack.mockClear();
    post.mockClear();
  });

  it("a real edit toasts; editing back to the stored value does not", () => {
    write(params("image", "https://cdn/x/new.png"));
    expect(pendingCell(DS, "ROW_3", "C_M")).toBe(true);
    expect(snack).toHaveBeenCalledTimes(1);
    expect(snack.mock.calls[0][0]).toBe(
      "Image link replaced · saved with the next version",
    );
    write(params("image", "https://cdn/x/old.png"));
    expect(useChangeSetStore.getState().getOps(DS)).toHaveLength(0);
    expect(pendingCell(DS, "ROW_3", "C_M")).toBe(false);
    expect(snack).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
  });

  it("the same value as stored, with nothing pending, says nothing", () => {
    write(params("document", "https://cdn/x/old.png"));
    expect(useChangeSetStore.getState().getOps(DS)).toHaveLength(0);
    expect(snack).not.toHaveBeenCalled();
  });
});

describe("S5: documentMetaOf names the file the cell shows", () => {
  it("a URL: its last path part and extension", () => {
    expect(documentMetaOf("https://cdn.example/docs/terms-2026.pdf")).toEqual({
      fileName: "terms-2026.pdf",
      fileType: getFileType("pdf"),
    });
  });

  it("an uploaded file (data: URL): its name= and MIME type", () => {
    const m = documentMetaOf(
      "data:application/pdf;name=contract%20v2.pdf;base64,JVBERi0=",
    );
    expect(m.fileName).toBe("contract v2.pdf");
    expect(m.fileType).toBe(documentMetaOf("x/contract.pdf").fileType);
  });

  it("no value: empty strings", () => {
    expect(documentMetaOf(null)).toEqual({ fileName: "", fileType: "" });
    expect(documentMetaOf("")).toEqual({ fileName: "", fileType: "" });
  });
});
