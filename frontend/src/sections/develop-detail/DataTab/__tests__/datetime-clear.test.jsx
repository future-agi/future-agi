import { beforeEach, describe, expect, it, vi } from "vitest";
import { format } from "date-fns";

// Isolate menus/renderers/stores; exercise the complete writer and its real
// cellWriteDisplay callbacks. Only the HTTP boundary is mocked.
vi.mock("src/sections/common/ux/oneview/headerMenuItems", () => ({ completeAgMenuCheckedState: vi.fn() }));
vi.mock("src/sections/common/CustomDevelopDetailColumn", () => ({ default: vi.fn() }));
vi.mock("src/utils/agUtils", () => ({ parseCellValue: vi.fn() }));
vi.mock("src/utils/constant", () => ({ AGGridCellDataType: {}, DevelopDataBlockedChangeDataType: [] }));
vi.mock("src/sections/common/DevelopCellRenderer/CustomCellRender", () => ({ default: vi.fn() }));
vi.mock("src/sections/common/DevelopCellRenderer/CustomDevelopGroupCellHeader", () => ({ default: vi.fn() }));
vi.mock("src/utils/runStatus", () => ({ statusColors: vi.fn() }));
vi.mock("src/utils/MenuIconSet/setMeniIcons", () => ({ reorderMenuList: vi.fn(), setMenuIcons: vi.fn() }));
vi.mock("src/utils/MenuIconSet/svgIcons", () => ({ menuIcons: {} }));
vi.mock("../../states", () => ({}));
vi.mock("../../AddColumn/ExecuteCode/codeDisabled", () => ({ CODE_COLUMNS_DISABLED_REASON: "", isCodeColumn: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: { post: vi.fn() }, endpoints: { develop: { updateCellValue: (id) => `/datasets/${id}/cell` } } }));
vi.mock("src/utils/logger", () => ({ default: { warn: vi.fn(), error: vi.fn() } }));
vi.mock("src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper", () => ({ StatusTypes: {} }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../Versions/changeSetStore", () => ({ cellIndex: vi.fn(), useChangeSetStore: vi.fn() }));
vi.mock("../Versions/versionedCellEdit", () => ({ MEDIA_TYPES: new Set(), versionedCellEdit: vi.fn() }));
vi.mock("src/sections/develop/realBackendCaps", () => ({ gridWritesClassic: () => true }));
vi.mock("../DataGrid/dataGridView", () => ({ columnWidthFor: vi.fn(), ROLE_LABEL: {}, USER_ROLES: [] }));
vi.mock("../DataGrid/columnRole", () => ({ saveColumnRole: vi.fn() }));
vi.mock("src/sections/common/Values/columnMenuItems", () => ({ datatypeColumnMenuItems: vi.fn() }));
vi.mock("../DataGrid/EmptyColumnHeader", () => ({ default: vi.fn() }));

import axios from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { onCellValueChangedWrapper } from "../common";

const previous = "2026-11-08 12:30:00";

async function write(newValue) {
  const node = { data: { row_id: "row", date: { cell_value: newValue, metadata: "keep" } } };
  const api = { getRowNode: vi.fn(() => node), refreshCells: vi.fn() };
  onCellValueChangedWrapper({ invalidateQueries: vi.fn() }, "dataset")({
    type: "cellValueChanged", source: "edit", newValue, oldValue: previous,
    data: node.data, column: { colId: "date", colDef: { dataType: "datetime" } }, api,
  });
  await Promise.resolve();
  return { node, api };
}

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ status: 200 });
});

describe("datetime grid clearing", () => {
  it.each([
    ["null", null], ["undefined", undefined], ["empty string", ""], ["whitespace", "   "],
  ])("sends a clearing payload for %s, not the Unix epoch", async (_, value) => {
    const { node } = await write(value);
    expect(axios.post).toHaveBeenCalledExactlyOnceWith("/datasets/dataset/cell", {
      row_id: "row", column_id: "date", new_value: "",
    });
    expect(node.data.date.cell_value).toBeNull();
    expect(node.data.date.metadata).toBe("keep");
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("formats a valid nonempty datetime as before", async () => {
    const value = new Date(2026, 10, 8, 12, 30, 0);
    const { node } = await write(value);
    expect(axios.post.mock.calls[0][1].new_value).toBe(format(value, "yyyy-MM-dd HH:mm:ss"));
    expect(node.data.date.cell_value).toEqual(value);
  });

  it.each(["invalid date", new Date(NaN)])("rejects invalid dates without a write: %s", async (value) => {
    const { node } = await write(value);
    expect(axios.post).not.toHaveBeenCalled();
    expect(node.data.date.cell_value).toBe(previous);
    expect(enqueueSnackbar).toHaveBeenCalledWith(expect.any(String), { variant: "error" });
  });

  it("restores the previous date if the clearing request is refused", async () => {
    axios.post.mockRejectedValue(new Error("refused"));
    const { node } = await write(null);
    expect(axios.post.mock.calls[0][1].new_value).toBe("");
    expect(node.data.date.cell_value).toBe(previous);
    expect(enqueueSnackbar).toHaveBeenCalledWith(expect.any(String), { variant: "error" });
  });
});
