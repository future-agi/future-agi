import { format } from "date-fns";
import { enqueueSnackbar } from "notistack";
import { useChangeSetStore } from "./changeSetStore";
import { validateEdit } from "../DatapointDrawerV2/editValue";
import { gridWritesClassic } from "src/sections/develop/realBackendCaps";
import { showCellValue } from "../cellWriteDisplay";

const NUMBER_TYPES = ["integer", "float", "number"];

/**
 * v2.1 g8 (grid:cell-edit): a grid edit of a number cell goes through the
 * Row view's one validator. Returns `{refused: sentence}` for text that is
 * not a number (or a fraction in an integer cell), `{value}` otherwise
 * (`""` clears it). A NaN from the number editor counts as refused text.
 */
export function numberCellEdit(newValue, dataType, columnName) {
  if (!NUMBER_TYPES.includes(dataType)) return { value: newValue };
  if (typeof newValue === "number" && Number.isNaN(newValue))
    return { refused: `${columnName || "This column"} takes a number.` };
  const text = newValue == null ? "" : String(newValue).trim();
  const out = validateEdit(text, dataType);
  if (!out.ok)
    return {
      refused: `${columnName || "This column"}: ${out.error.toLowerCase()}. The cell keeps its value.`,
    };
  return { value: out.value };
}

// Cell types whose values are media (a URL, a data: URL or, for `images`, a
// JSON list of them) or a persona object edited in its own dialog.
export const MEDIA_TYPES = ["image", "images", "audio", "document", "persona"];

const asText = (value) => {
  if (value == null) return "";
  if (value instanceof Date)
    return Number.isNaN(value.getTime())
      ? ""
      : format(value, "yyyy-MM-dd HH:mm:ss");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const isBlob = (v) => typeof Blob !== "undefined" && v instanceof Blob;

const isMediaEdit = (newValue, dataType) =>
  isBlob(newValue) ||
  (typeof newValue === "string" && newValue.startsWith("data:image/")) ||
  MEDIA_TYPES.includes(dataType);

/** A File as a data: URL (FileReader); the file name rides as `name=`. */
export function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve(withName(String(reader.result ?? ""), file?.name));
    };
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

const emptyMedia = (v) =>
  v == null ||
  v === "" ||
  (Array.isArray(v) && !v.length) ||
  (typeof v === "string" && ["[]", "null"].includes(v.trim()));

/** A data: URL carrying the file's name as `name=` (read back by extractFilenameFromDataURL). */
export function withName(value, fileName) {
  if (typeof value !== "string" || !value.startsWith("data:")) return value;
  if (!fileName || /;name=/.test(value.split(",")[0])) return value;
  return value.replace(
    /^data:([^;,]*)/,
    `data:$1;name=${encodeURIComponent(fileName)}`,
  );
}

/** The op value of a media edit: a URL / data: URL string, a JSON list for images, null when removed. */
export function mediaOpValue(value, dataType) {
  if (emptyMedia(value)) return null;
  if (dataType === "images") {
    if (Array.isArray(value)) return JSON.stringify(value);
    if (typeof value === "string") {
      const t = value.trim();
      if (t.startsWith("[")) return t;
      return JSON.stringify([t]);
    }
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const isPdf = (v) =>
  typeof v === "string" &&
  (/^data:application\/pdf[;,]/i.test(v) ||
    /\.pdf(?:$|[?#])/i.test(v.split(";base64,")[0]));

const KIND = {
  image: "Image",
  images: "Images",
  audio: "Audio",
  persona: "Persona",
};

/**
 * The one line a media edit shows: what changed and where it goes, e.g.
 * "PDF link added · saved with the next version".
 */
export function mediaEditNote({ dataType, value, before }) {
  const kind =
    dataType === "document"
      ? isPdf(value) || (value == null && isPdf(before))
        ? "PDF"
        : "Document"
      : KIND[dataType] ?? "File";
  const link =
    typeof value === "string" &&
    /^https?:\/\//i.test(value) &&
    dataType !== "images";
  const verb =
    value == null
      ? "removed"
      : dataType === "images" || dataType === "persona"
        ? "updated"
        : emptyMedia(before)
          ? link
            ? "link added"
            : "added"
          : link
            ? "link replaced"
            : "replaced";
  return `${kind} ${verb} · saved with the next version`;
}

/**
 * Whether the change-set holds an edit of this cell after an addOp: its
 * `update_cell` op, or the cell of a pending new row (`add_row`).
 */
export function pendingCell(datasetId, rowId, columnId) {
  return useChangeSetStore
    .getState()
    .getOps(datasetId)
    .some(
      (o) =>
        (o.op === "update_cell" &&
          o.row_id === rowId &&
          o.column_id === columnId) ||
        (o.op === "add_row" && o.temp_id === rowId),
    );
}

/**
 * onCellValueChanged for a versioned dataset grid (PRODUCT-SPEC §3.5, C28):
 * a hand edit becomes a pending `update_cell` op in the change-set instead
 * of an immediate write; Save version commits it. The grid already shows the
 * new value; Discard and Revert refresh it from the server.
 *
 * Media edits (image, images, audio, document, persona and any File) go to
 * `fallback` (the immediate writer) unless `mediaPending` is set; the dataset
 * page sets it (the interface contract C1), so they too become ONE pending op: a URL string, a
 * File read as a data: URL, an images JSON list, null when removed.
 */
export function versionedCellEdit({
  datasetId,
  fallback,
  mediaPending = false,
}) {
  return (params) => {
    if (params?.type === "cellValueChanged" && params?.source === undefined)
      return undefined;
    const newValue = params?.newValue;
    const dataType = params?.column?.colDef?.dataType;
    const rowId = params?.data?.row_id ?? params?.data?.rowId;
    const columnId = params?.column?.colId;
    // L1 (the capability contract): the real backend has no versions. Every edit,
    // media included, goes to `fallback` (the immediate update_cell_value
    // writer) after the same number check; nothing is staged.
    if (gridWritesClassic()) {
      if (!isMediaEdit(newValue, dataType)) {
        const checked = numberCellEdit(
          newValue,
          dataType,
          params?.column?.colDef?.headerName ??
            params?.column?.colDef?.col?.name,
        );
        if (checked.refused) {
          // Put the old value back for display only. RowNode.setDataValue
          // would be another cellValueChanged ('edit' while Tab's next
          // editor is open), which this handler writes as an update.
          if (columnId)
            showCellValue(
              params?.api,
              params?.node,
              columnId,
              params?.oldValue,
            );
          enqueueSnackbar(checked.refused, { variant: "error" });
          return undefined;
        }
      }
      if (!fallback) {
        enqueueSnackbar("This edit can't be saved here.", {
          variant: "error",
        });
        return undefined;
      }
      return fallback(params);
    }
    if (isMediaEdit(newValue, dataType)) {
      if (!mediaPending) {
        fallback?.(params);
        return undefined;
      }
      if (!rowId || !columnId) return undefined;
      const stored = params?.data?.[columnId];
      const before =
        params?.oldValue !== undefined
          ? params.oldValue
          : stored && typeof stored === "object" && "cell_value" in stored
            ? stored.cell_value
            : stored?.cellValue ?? null;
      const add = (raw) => {
        const value = withName(mediaOpValue(raw, dataType), params?.fileName);
        useChangeSetStore.getState().addOp(datasetId, {
          op: "update_cell",
          row_id: rowId,
          column_id: columnId,
          value,
          before: before ?? null,
        });
        // Review 2 (S11): an edit back to the stored value is dropped by the
        // change-set (nothing to save), so it says nothing.
        if (!pendingCell(datasetId, rowId, columnId)) return;
        enqueueSnackbar(mediaEditNote({ dataType, value, before }), {
          variant: "info",
          // The column's writer and the grid's both see one media save.
          preventDuplicate: true,
        });
      };
      if (isBlob(newValue))
        return readAsDataUrl(newValue).then(add, () =>
          enqueueSnackbar("Could not read the file", { variant: "error" }),
        );
      add(newValue);
      return undefined;
    }
    if (!rowId || !columnId) return undefined;
    const checked = numberCellEdit(
      newValue,
      dataType,
      params?.column?.colDef?.headerName ?? params?.column?.colDef?.col?.name,
    );
    if (checked.refused) {
      // The grid shows the typed text until it is put back. Staged path only
      // (prototype): the staged valueSetter records the op and returns false,
      // so AG Grid emits no cellValueChanged; and if one did reach this
      // handler, a non-media edit here only stages an op, never a request.
      params?.node?.setDataValue?.(columnId, params?.oldValue);
      enqueueSnackbar(checked.refused, { variant: "error" });
      return undefined;
    }
    const value = asText(checked.value);
    const before = asText(params?.oldValue);
    // An edit that leaves the cell as it was is no change.
    if (value === before && !pendingCell(datasetId, rowId, columnId))
      return undefined;
    useChangeSetStore.getState().addOp(datasetId, {
      op: "update_cell",
      row_id: rowId,
      column_id: columnId,
      value,
      before,
    });
    return undefined;
  };
}
