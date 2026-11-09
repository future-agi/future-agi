// integration C1 (DM-04): on the dataset page every media edit is ONE pending
// update_cell op (no update_cell_value request); scenario grids keep the
// immediate writer.
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
const { mediaEditNote, mediaOpValue, versionedCellEdit, withName } =
  await import("../Versions/versionedCellEdit");
const { opensMediaEditor } = await import(
  "../DoubleClickEditCell/mediaEditorRoute"
);

const DS = "ds-media";
const rowNode = { setDataValue: vi.fn() };
const params = (dataType, newValue, stored = "https://cdn/x/old.bin") => ({
  data: { row_id: "ROW_3", C_M: { cell_value: stored } },
  column: { colId: "C_M", colDef: { dataType } },
  colDef: { dataType },
  value: stored,
  newValue,
  api: { getRowNode: () => rowNode, refreshServerSide: vi.fn() },
});
const ops = () => useChangeSetStore.getState().getOps(DS);
const flush = () => new Promise((r) => setTimeout(r, 30));
const pngFile = () =>
  new File([new Uint8Array([137, 80, 78, 71])], "red.png", {
    type: "image/png",
  });
const pdfFile = () =>
  new File(["%PDF-1.4"], "contract-v2.pdf", { type: "application/pdf" });
const wavFile = () => new File(["RIFF"], "call.wav", { type: "audio/wav" });

describe("K4F-C C1 media edits on the dataset page", () => {
  beforeEach(() => {
    useChangeSetStore.getState().discard(DS);
    post.mockClear();
    snack.mockClear();
  });
  const write = (p) =>
    cellWriter({ versioned: true, queryClient: {}, dataset: DS })(p);

  it.each([
    ["image", "https://cdn/x/new.png", "https://cdn/x/new.png"],
    [
      "image",
      "data:image/png;base64,iVBORw0KGgo=",
      "data:image/png;base64,iVBORw0KGgo=",
    ],
    [
      "images",
      '["https://a/1.png","https://a/2.png"]',
      '["https://a/1.png","https://a/2.png"]',
    ],
    ["images", ["https://a/1.png"], '["https://a/1.png"]'],
    ["audio", "https://cdn/x/new.wav", "https://cdn/x/new.wav"],
    ["document", "https://cdn/x/new.pdf", "https://cdn/x/new.pdf"],
    ["persona", '{"gender":"female"}', '{"gender":"female"}'],
    ["image", null, null],
  ])("%s %j becomes one pending op, no update_cell_value", (t, v, want) => {
    write(params(t, v));
    expect(ops()).toHaveLength(1);
    expect(ops()[0]).toMatchObject({
      op: "update_cell",
      row_id: "ROW_3",
      column_id: "C_M",
      value: want,
      before: "https://cdn/x/old.bin",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it.each([
    ["image", pngFile, /^data:image\/png;name=red\.png;base64,/],
    [
      "document",
      pdfFile,
      /^data:application\/pdf;name=contract-v2\.pdf;base64,/,
    ],
    ["audio", wavFile, /^data:audio\/wav;name=call\.wav;base64,/],
  ])("a %s File is read to a data: URL in one op", async (t, mk, re) => {
    await write(params(t, mk()));
    await flush();
    expect(ops()).toHaveLength(1);
    expect(ops()[0].value).toMatch(re);
    expect(post).not.toHaveBeenCalled();
  });

  it("a FileReader data URL keeps the file name the editor passed", () => {
    write({
      ...params("document", "data:application/pdf;base64,JVBERi0="),
      fileName: "a b.pdf",
    });
    expect(ops()[0].value).toBe(
      "data:application/pdf;name=a%20b.pdf;base64,JVBERi0=",
    );
  });

  it("a PDF link into an empty cell is a pending op with its toast", () => {
    write(params("document", "https://example.org/terms.pdf", null));
    expect(ops()).toHaveLength(1);
    expect(ops()[0]).toMatchObject({
      value: "https://example.org/terms.pdf",
      before: null,
    });
    expect(snack).toHaveBeenCalledWith(
      "PDF link added · saved with the next version",
      expect.anything(),
    );
  });

  it("editing back to the stored value drops the op (revert)", () => {
    write(params("image", "https://cdn/x/new.png"));
    write(params("image", "https://cdn/x/old.bin"));
    expect(ops()).toHaveLength(0);
  });

  it("scenario grids keep the immediate writer", () => {
    cellWriter({ versioned: false, queryClient: {}, dataset: DS })(
      params("image", "data:image/png;base64,AAA"),
    );
    expect(ops()).toHaveLength(0);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toMatch(/updateCellValue/);
  });

  it("without mediaPending the legacy fallback still receives media", () => {
    const fallback = vi.fn();
    versionedCellEdit({ datasetId: DS, fallback })(
      params("audio", "https://cdn/x/new.wav"),
    );
    expect(fallback).toHaveBeenCalledTimes(1);
    expect(ops()).toHaveLength(0);
  });
});

describe("K4F-C C1 pure helpers", () => {
  it("mediaOpValue", () => {
    expect(mediaOpValue("", "image")).toBeNull();
    expect(mediaOpValue("[]", "images")).toBeNull();
    expect(mediaOpValue("https://a/1.png", "images")).toBe(
      '["https://a/1.png"]',
    );
    expect(mediaOpValue({ a: 1 }, "persona")).toBe('{"a":1}');
  });
  it("withName leaves URLs and named data URLs alone", () => {
    expect(withName("https://a/x.pdf", "x.pdf")).toBe("https://a/x.pdf");
    expect(withName("data:a/b;name=k;base64,Z", "y")).toBe(
      "data:a/b;name=k;base64,Z",
    );
  });
  it("mediaEditNote words", () => {
    const n = (dataType, value, before) =>
      mediaEditNote({ dataType, value, before });
    expect(n("image", "data:image/png;base64,A", "https://a/1.png")).toBe(
      "Image replaced · saved with the next version",
    );
    expect(n("audio", null, "https://a/1.wav")).toBe(
      "Audio removed · saved with the next version",
    );
    expect(n("document", "data:application/pdf;base64,A", null)).toBe(
      "PDF added · saved with the next version",
    );
    expect(n("document", "https://a/x.docx", "https://a/y.pdf")).toBe(
      "Document link replaced · saved with the next version",
    );
    expect(n("images", '["a"]', '["b"]')).toBe(
      "Images updated · saved with the next version",
    );
  });
});

describe("K4F-C C1 double-click routing", () => {
  const ev = (dataType, originType = "OTHERS", colId = "C_M") => ({
    colDef: { dataType, originType },
    column: { colId },
  });
  it.each(["image", "images", "audio", "document", "persona"])(
    "%s opens its editor",
    (t) => expect(opensMediaEditor(ev(t))).toBe(true),
  );
  it.each(["text", "json", "integer", "boolean", "datetime"])(
    "%s does not",
    (t) => expect(opensMediaEditor(ev(t))).toBe(false),
  );
  it("computed columns and the selection column do not", () => {
    expect(opensMediaEditor(ev("image", "run_prompt"))).toBe(false);
    expect(
      opensMediaEditor(ev("image", "OTHERS", "sel"), {
        selectionColumnId: "sel",
      }),
    ).toBe(false);
  });
});
