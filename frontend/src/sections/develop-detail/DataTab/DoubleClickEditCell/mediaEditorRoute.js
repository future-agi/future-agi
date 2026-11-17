import { MEDIA_TYPES } from "../Versions/versionedCellEdit";
import {
  extractFilenameFromDataURL,
  getFileType,
  getFileTypeFromMime,
} from "src/sections/common/DevelopCellRenderer/CellRenderers/common";

// Computed columns show results; their cells are not hand-edited in place.
const COMPUTED_ORIGINS = [
  "run_prompt",
  "evaluation",
  "optimization",
  "annotation_label",
  "evaluation_reason",
];

/**
 * Whether a grid double-click opens a media editor (EditImageZoom,
 * EditImages, EditFile, AudioWaveformModal, EditPersona): every media type
 * on a user column, never the selection column (integration C1).
 */
export function opensMediaEditor(event, { selectionColumnId } = {}) {
  const dataType = event?.colDef?.dataType;
  const originType = event?.colDef?.originType;
  const colId = event?.column?.colId;
  return (
    MEDIA_TYPES.includes(dataType) &&
    colId !== selectionColumnId &&
    !COMPUTED_ORIGINS.includes(originType)
  );
}

/**
 * Review 2 (S5): the name and type EditFile shows for a document cell's
 * value — a URL's last path part and extension, or an uploaded file's
 * `name=` and MIME type when it is a `data:` URL (a pending edit) — the
 * rule FileCellRenderer shows in the cell. Empty strings without a value.
 * @returns {{fileName: string, fileType: string}}
 */
export function documentMetaOf(value) {
  if (typeof value !== "string" || !value)
    return { fileName: "", fileType: "" };
  if (value.startsWith("data:"))
    return {
      fileName: extractFilenameFromDataURL(value) ?? "",
      fileType: getFileType(
        getFileTypeFromMime(value.match(/^data:([^;,]+)/)?.[1] ?? ""),
      ),
    };
  return {
    fileName: value.split("/").pop() || value,
    fileType: getFileType(value.split(".").pop()),
  };
}
